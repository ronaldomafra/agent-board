from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from agentboard.config import (
    config_fingerprint,
    validate_config,
    write_config_apply_marker,
)
from agentboard.domain import (
    Actor,
    AssignmentStatus,
    BlockerStatus,
    CommandResult,
    DependencyType,
    DomainError,
    IdempotencyConflictError,
    InvalidTransitionError,
    LeaseFencedError,
    NotFoundError,
    PlanStatus,
    PolicyViolationError,
    Priority,
    ReviewStatus,
    RiskLevel,
    RunStatus,
    Task,
    TaskState,
    TaskView,
    VersionConflictError,
)
from agentboard.git_local import GitPolicyError, LocalGitAdapter
from agentboard.storage import connect, immediate, initialize


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(value: datetime | None = None) -> str:
    return (value or _now()).isoformat(timespec="microseconds")


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _id() -> str:
    return uuid.uuid4().hex


def _actor(value: Actor | str) -> Actor:
    if not isinstance(value, Actor):
        raise PolicyViolationError(
            "BoardService requires an authenticated Actor derived by the adapter."
        )
    return value


def _normalized_path(value: str) -> str:
    cleaned = value.strip().replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    if (
        not cleaned
        or cleaned.startswith(("/", "//"))
        or (len(cleaned) >= 2 and cleaned[1] == ":")
    ):
        raise PolicyViolationError(f"Unsafe or empty leased path: {value!r}.")
    parsed = PurePosixPath(cleaned)
    if any(part in {"", ".", ".."} for part in parsed.parts):
        raise PolicyViolationError("Leased paths must be normalized and project-relative.")
    return os.path.normcase(str(parsed)).replace("\\", "/")


def _paths_overlap(left: str, right: str) -> bool:
    left_parts = PurePosixPath(left).parts
    right_parts = PurePosixPath(right).parts
    length = min(len(left_parts), len(right_parts))
    return left_parts[:length] == right_parts[:length]


@dataclass(slots=True)
class BoardService:
    """Sole transactional boundary for AgentBoard operational state."""

    database_path: Path | None = None
    wip_limit: int = 3
    profile_wip_limits: dict[str, int] = field(default_factory=dict)
    evidence_profiles: dict[str, dict[str, Any]] = field(
        default_factory=lambda: {
            "default": {
                "require_changed_files": False,
                "require_tests": False,
                "require_acceptance_evidence": False,
                "require_local_checkpoint": False,
                "require_local_integration": False,
            },
            "code_with_git": {
                "require_changed_files": False,
                "require_tests": True,
                "require_acceptance_evidence": False,
                "require_local_checkpoint": True,
                "require_local_integration": True,
            },
        }
    )
    reservation_ttl_seconds: int = 300
    lease_ttl_seconds: int = 1800
    transient_max_attempts: int = 2

    def __post_init__(self) -> None:
        if self.database_path is not None:
            self.database_path = Path(self.database_path)
            initialize(self.database_path)
            self._recover_external_intents()

    def _external_intent_path(
        self,
        operation: str,
        idempotency_key: str,
    ) -> Path:
        digest = hashlib.sha256(
            f"{operation}\0{idempotency_key}".encode()
        ).hexdigest()[:32]
        directory = self._project_root() / ".agentboard" / "external-intents"
        directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{digest}.json"

    def _write_external_intent(
        self,
        operation: str,
        idempotency_key: str,
        payload: dict[str, Any],
    ) -> Path:
        target = self._external_intent_path(operation, idempotency_key)
        temporary = target.with_suffix(".tmp")
        document = {
            "schema_version": 1,
            "intent_id": target.stem,
            "operation": operation,
            "idempotency_hash": hashlib.sha256(
                idempotency_key.encode("utf-8")
            ).hexdigest(),
            "payload": payload,
            "created_at": _iso(),
        }
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
        return target

    @staticmethod
    def _update_external_intent(path: Path, **values: Any) -> None:
        document = json.loads(path.read_text(encoding="utf-8"))
        document.update(values)
        temporary = path.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            json.dump(document, stream, sort_keys=True, separators=(",", ":"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _recover_external_intents(self) -> None:
        directory = self._project_root() / ".agentboard" / "external-intents"
        if not directory.is_dir():
            return
        for path in sorted(directory.glob("*.json")):
            try:
                document = json.loads(path.read_text(encoding="utf-8"))
                if (
                    document.get("schema_version") != 1
                    or document.get("intent_id") != path.stem
                    or not isinstance(document.get("payload"), dict)
                ):
                    continue
                operation = document.get("operation")
                payload = document["payload"]
                if operation == "worktree_create":
                    self._recover_worktree_intent(path, payload)
                elif operation in {"git_checkpoint", "git_integrate_task"}:
                    self._recover_task_git_intent(path, document)
                elif operation == "git_integrate_plan":
                    self._recover_plan_git_intent(path, document)
            except (OSError, ValueError, KeyError, GitPolicyError, DomainError):
                # Keep the marker for explicit operator recovery if safe automatic
                # reconciliation cannot prove what happened.
                continue

    def _recover_worktree_intent(
        self,
        path: Path,
        payload: dict[str, Any],
    ) -> None:
        assignment_id = str(payload["assignment_id"])
        with connect(self._path()) as connection:
            assignment = connection.execute(
                "SELECT 1 FROM assignments WHERE id=?",
                (assignment_id,),
            ).fetchone()
        if assignment:
            path.unlink(missing_ok=True)
            return
        LocalGitAdapter(self._project_root()).cleanup_owned_worktree(
            worktree=Path(payload["worktree_path"]),
            branch=str(payload["branch_ref"]),
            expected_start_sha=str(payload["start_sha"]),
        )
        path.unlink(missing_ok=True)

    def _recover_task_git_intent(
        self,
        path: Path,
        document: dict[str, Any],
    ) -> None:
        payload = document["payload"]
        task_id = str(payload["task_id"])
        run_id = str(payload["run_id"])
        expected_sha = str(payload["expected_sha"])
        kind = (
            "CHECKPOINT"
            if document["operation"] == "git_checkpoint"
            else "INTEGRATE_TASK"
        )
        with connect(self._path()) as connection:
            recorded = connection.execute(
                """
                SELECT 1 FROM git_operations
                WHERE task_id=? AND run_id=? AND kind=? AND status='SUCCEEDED'
                """,
                (task_id, run_id, kind),
            ).fetchone()
            task = connection.execute(
                "SELECT version FROM tasks WHERE id=?", (task_id,)
            ).fetchone()
        if recorded:
            path.unlink(missing_ok=True)
            return
        adapter = LocalGitAdapter(self._project_root())
        if document["operation"] == "git_checkpoint":
            worktree = Path(payload["worktree_path"])
            if not worktree.exists() or adapter.head_sha(worktree) == expected_sha:
                path.unlink(missing_ok=True)
                return
            result_sha = adapter.head_sha(worktree)
        else:
            result_sha = adapter.resolve_ref(str(payload["target_ref"]))
            if result_sha == expected_sha:
                path.unlink(missing_ok=True)
                return
        if not task:
            return
        recovery_actor = Actor(
            "runtime:external-recovery",
            frozenset({"orchestrator"}),
        )
        intent_id = str(document["intent_id"])

        def action(
            connection: sqlite3.Connection,
            principal: Actor,
        ) -> CommandResult:
            row = self._task_row(connection, task_id)
            self._version(row, task["version"])
            connection.execute(
                """
                INSERT OR IGNORE INTO git_operations(
                    id, task_id, run_id, kind, status, target_sha, result_sha,
                    actor_id, created_at
                ) VALUES (?, ?, ?, ?, 'RECOVERY_REQUIRED', ?, ?, ?, ?)
                """,
                (
                    intent_id,
                    task_id,
                    run_id,
                    kind,
                    expected_sha,
                    result_sha,
                    principal.id,
                    _iso(),
                ),
            )
            blocker = connection.execute(
                """
                SELECT id FROM blockers
                WHERE task_id=? AND status='OPEN' AND reason LIKE 'External Git effect%'
                """,
                (task_id,),
            ).fetchone()
            blocker_id = blocker["id"] if blocker else _id()
            if not blocker:
                connection.execute(
                    """
                    INSERT INTO blockers(
                        id, task_id, run_id, status, reason, owner,
                        previous_state, created_at
                    ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?)
                    """,
                    (
                        blocker_id,
                        task_id,
                        run_id,
                        "External Git effect requires recovery after runtime interruption",
                        "human",
                        row["state"],
                        _iso(),
                    ),
                )
            assignment = connection.execute(
                """
                SELECT id FROM assignments
                WHERE task_id=? AND status IN ('RESERVED','ACCEPTED')
                """,
                (task_id,),
            ).fetchone()
            if assignment:
                self._release(connection, assignment["id"])
            abandoned_review_ids = self._abandon_pending_reviews(
                connection,
                task_id,
                "External Git recovery invalidated the pending review.",
            )
            connection.execute(
                """
                UPDATE runs SET status='STALE', finished_at=?
                WHERE id=? AND status IN ('RUNNING','SUCCEEDED')
                """,
                (_iso(), run_id),
            )
            version = row["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='BACKLOG', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection,
                "task",
                task_id,
                "EXTERNAL_GIT_RECOVERY_REQUIRED",
                principal,
                {
                    "intent_id": intent_id,
                    "kind": kind,
                    "result_sha": result_sha,
                    "blocker_id": blocker_id,
                    "abandoned_review_ids": abandoned_review_ids,
                },
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.BACKLOG,
                data={"recovery_required": True, "blocker_id": blocker_id},
            )

        self._mutate(
            operation="external_git_recover",
            actor=recovery_actor,
            idempotency_key=f"recover-{intent_id}",
            payload={
                "intent_id": intent_id,
                "task_id": task_id,
                "run_id": run_id,
                "result_sha": result_sha,
            },
            action=action,
        )
        path.unlink(missing_ok=True)

    def _recover_plan_git_intent(
        self,
        path: Path,
        document: dict[str, Any],
    ) -> None:
        payload = document["payload"]
        plan_id = str(payload["plan_id"])
        revision = int(payload["revision"])
        expected_sha = str(payload["expected_sha"])
        adapter = LocalGitAdapter(self._project_root())
        result_sha = adapter.resolve_ref(str(payload["target_ref"]))
        with connect(self._path()) as connection:
            recorded = connection.execute(
                """
                SELECT 1 FROM git_operations
                WHERE kind='INTEGRATE_PLAN' AND target_sha=?
                  AND result_sha=? AND status='SUCCEEDED'
                """,
                (expected_sha, result_sha),
            ).fetchone()
            plan = connection.execute(
                "SELECT * FROM plans WHERE id=? AND revision=?",
                (plan_id, revision),
            ).fetchone()
        if recorded or result_sha == expected_sha:
            path.unlink(missing_ok=True)
            return
        if not plan:
            return
        actor = Actor("runtime:external-recovery", frozenset({"orchestrator"}))
        intent_id = str(document["intent_id"])

        def action(
            connection: sqlite3.Connection,
            principal: Actor,
        ) -> CommandResult:
            row = self._plan_row(connection, plan_id, revision)
            self._version(row, plan["version"])
            connection.execute(
                """
                INSERT OR IGNORE INTO git_operations(
                    id, kind, status, target_sha, result_sha, actor_id, created_at
                ) VALUES (?, 'INTEGRATE_PLAN', 'RECOVERY_REQUIRED', ?, ?, ?, ?)
                """,
                (intent_id, expected_sha, result_sha, principal.id, _iso()),
            )
            version = row["version"] + 1
            connection.execute(
                "UPDATE plans SET version=? WHERE id=? AND revision=?",
                (version, plan_id, revision),
            )
            self._event(
                connection,
                "plan",
                plan_id,
                "EXTERNAL_GIT_RECOVERY_REQUIRED",
                principal,
                {
                    "intent_id": intent_id,
                    "revision": revision,
                    "result_sha": result_sha,
                },
                version,
            )
            return CommandResult(
                plan_id,
                version,
                row["status"],
                data={"recovery_required": True},
            )

        self._mutate(
            operation="external_plan_git_recover",
            actor=actor,
            idempotency_key=f"recover-{intent_id}",
            payload={
                "intent_id": intent_id,
                "plan_id": plan_id,
                "revision": revision,
                "result_sha": result_sha,
            },
            action=action,
        )
        path.unlink(missing_ok=True)

    # Kept as a pure helper for callers constructing an aggregate before persistence.
    def transition_task(self, task: Task, target: TaskState, expected_version: int) -> Task:
        if task.version != expected_version:
            raise VersionConflictError("Task version is stale; load and retry.")
        return task.transition_to(target)

    def _path(self) -> Path:
        if self.database_path is None:
            raise RuntimeError("This operation requires a configured database path.")
        return self.database_path

    def _project_root(self) -> Path:
        database = self._path().resolve()
        return database.parent.parent if database.parent.name == ".agentboard" else database.parent

    def authenticate_capability(
        self,
        token: str,
        operation: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        lease_generation: int | None = None,
    ) -> Actor:
        """Derive the actor from a hashed, scoped and expiring capability."""
        if len(token) < 32:
            raise PolicyViolationError("Capability is invalid.")
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        with connect(self._path()) as connection:
            row = connection.execute(
                """
                SELECT * FROM capabilities
                WHERE token_hash=? AND revoked_at IS NULL AND expires_at>?
                """,
                (token_hash, _iso()),
            ).fetchone()
        if not row:
            raise PolicyViolationError("Capability is expired, revoked or unknown.")
        operations = set(json.loads(row["operations_json"]))
        if operation not in operations:
            raise PolicyViolationError("Capability does not permit this operation.")
        if row["task_id"] and row["task_id"] != task_id:
            raise PolicyViolationError("Capability is outside the requested task scope.")
        if row["run_id"] and row["run_id"] != run_id:
            raise PolicyViolationError("Capability is outside the requested run scope.")
        if (
            row["lease_generation"] is not None
            and row["lease_generation"] != lease_generation
        ):
            raise LeaseFencedError("Capability lease generation is stale.")
        return Actor(row["actor_id"], frozenset(json.loads(row["roles_json"])))

    def _event(
        self,
        connection: sqlite3.Connection,
        entity_type: str,
        entity_id: str,
        event_type: str,
        actor: Actor,
        payload: dict[str, Any],
        version: int | None = None,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events(
                event_id, entity_type, entity_id, event_type, actor_id,
                task_version, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_id(), entity_type, entity_id, event_type, actor.id, version, _json(payload), _iso()),
        )

    def _mutate(
        self,
        *,
        operation: str,
        actor: Actor | str,
        idempotency_key: str,
        payload: dict[str, Any],
        action: Callable[[sqlite3.Connection, Actor], CommandResult],
    ) -> CommandResult:
        principal = _actor(actor)
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required.")
        request_hash = hashlib.sha256(_json(payload).encode()).hexdigest()
        with immediate(self._path()) as connection:
            previous = connection.execute(
                "SELECT * FROM idempotency WHERE key = ?", (idempotency_key,)
            ).fetchone()
            if previous:
                if (
                    previous["operation"] != operation
                    or previous["actor_id"] != principal.id
                    or previous["request_hash"] != request_hash
                ):
                    raise IdempotencyConflictError(
                        "Idempotency key was already used with a different request."
                    )
                data = json.loads(previous["response_json"])
                data["replayed"] = True
                return CommandResult(**data)
            result = action(connection, principal)
            stored = asdict(result)
            stored["replayed"] = False
            connection.execute(
                """
                INSERT INTO idempotency(
                    key, operation, actor_id, request_hash, response_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (idempotency_key, operation, principal.id, request_hash, _json(stored), _iso()),
            )
            return result

    @staticmethod
    def _version(row: sqlite3.Row, expected_version: int) -> None:
        if row["version"] != expected_version:
            raise VersionConflictError(
                f"Expected version {expected_version}, current version is {row['version']}."
            )

    @staticmethod
    def _require_role(principal: Actor, *roles: str) -> None:
        if not principal.has_any_role(*roles):
            raise PolicyViolationError(
                f"Operation requires one of these roles: {', '.join(roles)}."
            )

    @staticmethod
    def _task_row(connection: sqlite3.Connection, task_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if not row:
            raise NotFoundError(f"Task {task_id!r} was not found.")
        return row

    @staticmethod
    def _task_requires_local_integration(task: sqlite3.Row) -> bool:
        policy = json.loads(task["evidence_policy_json"] or "{}")
        return bool(policy.get("require_local_integration"))

    def _assignment_policy_snapshot(
        self,
        assignment: sqlite3.Row,
    ) -> dict[str, Any]:
        snapshot = json.loads(assignment["policy_snapshot_json"] or "{}")
        return {
            "lease_ttl_seconds": int(
                snapshot.get("lease_ttl_seconds", self.lease_ttl_seconds)
            ),
            "transient_max_attempts": int(
                snapshot.get(
                    "transient_max_attempts",
                    self.transient_max_attempts,
                )
            ),
        }

    @staticmethod
    def _plan_row(
        connection: sqlite3.Connection, plan_id: str, revision: int
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM plans WHERE id = ? AND revision = ?", (plan_id, revision)
        ).fetchone()
        if not row:
            raise NotFoundError(f"Plan {plan_id!r} revision {revision} was not found.")
        return row

    def create_plan_draft(
        self,
        plan_id: str,
        title: str,
        content: dict[str, Any],
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        revision: int = 1,
        parent_revision: int | None = None,
    ) -> CommandResult:
        payload = {
            "plan_id": plan_id,
            "revision": revision,
            "title": title,
            "content": content,
            "expected_version": expected_version,
            "parent_revision": parent_revision,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator", "human")
            if expected_version != 0:
                raise VersionConflictError("A new plan draft must use expected_version=0.")
            if connection.execute(
                "SELECT 1 FROM plans WHERE id=? AND revision=?", (plan_id, revision)
            ).fetchone():
                raise VersionConflictError("Plan revision already exists.")
            if parent_revision is not None:
                parent = self._plan_row(connection, plan_id, parent_revision)
                if parent["status"] not in (PlanStatus.ACTIVE, PlanStatus.SUPERSEDED):
                    raise PolicyViolationError("A revision can only derive from an approved plan.")
            self._validate_plan_content(content)
            connection.execute(
                """
                INSERT INTO plans(
                    id, revision, parent_revision, title, status, version,
                    content_json, created_at
                ) VALUES (?, ?, ?, ?, 'DRAFT', 0, ?, ?)
                """,
                (plan_id, revision, parent_revision, title, _json(content), _iso()),
            )
            self._event(
                connection, "plan", plan_id, "PLAN_DRAFT_CREATED", principal,
                {"revision": revision}, 0,
            )
            return CommandResult(plan_id, 0, PlanStatus.DRAFT, data={"revision": revision})

        return self._mutate(
            operation="plan_draft_create",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def update_plan_draft(
        self,
        plan_id: str,
        revision: int,
        content: dict[str, Any],
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "plan_id": plan_id,
            "revision": revision,
            "content": content,
            "expected_version": expected_version,
        }
        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator", "human")
            row = self._plan_row(connection, plan_id, revision)
            self._version(row, expected_version)
            if row["status"] != PlanStatus.DRAFT:
                raise PolicyViolationError("Approved plan revisions are immutable.")
            self._validate_plan_content(content)
            version = row["version"] + 1
            connection.execute(
                """
                UPDATE plans
                SET title=?, content_json=?, version=?
                WHERE id=? AND revision=?
                """,
                (
                    content.get("title", row["title"]),
                    _json(content),
                    version,
                    plan_id,
                    revision,
                ),
            )
            self._event(
                connection, "plan", plan_id, "PLAN_DRAFT_UPDATED", principal,
                {"revision": revision}, version,
            )
            return CommandResult(plan_id, version, PlanStatus.DRAFT, data={"revision": revision})

        return self._mutate(
            operation="plan_draft_update", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def _validate_plan_content(self, content: dict[str, Any]) -> None:
        if "title" in content and (
            not isinstance(content["title"], str) or not content["title"].strip()
        ):
            raise PolicyViolationError("Plan title must be non-empty text.")
        tasks = content.get("tasks")
        if not isinstance(tasks, list) or not tasks:
            raise PolicyViolationError("Plan requires at least one executable task.")
        if any(not isinstance(task, dict) for task in tasks):
            raise PolicyViolationError("Each plan task must be an object.")
        ids = [task.get("id") for task in tasks]
        if any(not value for value in ids) or len(ids) != len(set(ids)):
            raise PolicyViolationError("Task ids must be present and unique.")
        known = set(ids)
        groups = content.get("groups", [])
        if not isinstance(groups, list) or any(
            not isinstance(group, dict) for group in groups
        ):
            raise PolicyViolationError("Plan groups must be a list of objects.")
        group_ids = [group.get("id") for group in groups]
        if len(group_ids) != len(set(group_ids)) or any(
            not isinstance(group_id, str) or not group_id
            for group_id in group_ids
        ):
            raise PolicyViolationError("Plan group ids must be present and unique.")
        for group in groups:
            if group.get("kind") not in {"milestone", "gate", "feature"}:
                raise PolicyViolationError("Plan group kind is invalid.")
            if not isinstance(group.get("title"), str) or not group["title"].strip():
                raise PolicyViolationError("Plan groups require a title.")
            if (
                not isinstance(group.get("position", 0), int)
                or group.get("position", 0) < 0
            ):
                raise PolicyViolationError(
                    "Plan group position must be a non-negative integer."
                )
        known_groups = set(group_ids)
        graph: dict[str, set[str]] = {task_id: set() for task_id in ids}
        for task in tasks:
            task_id = task["id"]
            if not isinstance(task_id, str) or len(task_id) > 120:
                raise PolicyViolationError("Task ids must be strings up to 120 characters.")
            for field_name in ("title", "objective"):
                if not isinstance(task.get(field_name), str) or not task[field_name].strip():
                    raise PolicyViolationError(
                        f"Task {task_id!r} requires {field_name}."
                    )
            for field_name in ("acceptance", "tests"):
                values = task.get(field_name)
                if (
                    not isinstance(values, list)
                    or not values
                    or any(not isinstance(item, str) or not item.strip() for item in values)
                ):
                    raise PolicyViolationError(
                        f"Task {task_id!r} requires non-empty {field_name} entries."
                    )
            try:
                Priority(task.get("priority", Priority.P2))
                RiskLevel(task.get("risk", RiskLevel.MEDIUM))
            except ValueError as exc:
                raise PolicyViolationError(
                    f"Task {task_id!r} has invalid priority or risk."
                ) from exc
            if not isinstance(task.get("suggested_profile", "worker"), str):
                raise PolicyViolationError(f"Task {task_id!r} has invalid profile.")
            evidence_profile = task.get("evidence_profile", "default")
            if evidence_profile not in self.evidence_profiles:
                raise PolicyViolationError(
                    f"Task {task_id!r} references unknown evidence profile "
                    f"{evidence_profile!r}."
                )
            if task.get("group_id") is not None and task["group_id"] not in known_groups:
                raise PolicyViolationError(
                    f"Task {task_id!r} references an unknown plan group."
                )
            paths = task.get("paths", [])
            if not isinstance(paths, list):
                raise PolicyViolationError(f"Task {task_id!r} paths must be a list.")
            for path in paths:
                if not isinstance(path, str):
                    raise PolicyViolationError(f"Task {task_id!r} has a non-text path.")
                _normalized_path(path)
            dependencies = task.get("dependencies", [])
            if not isinstance(dependencies, list):
                raise PolicyViolationError(f"Task {task_id!r} dependencies must be a list.")
            seen_edges: set[tuple[str, str]] = set()
            for dependency in dependencies:
                if not isinstance(dependency, dict):
                    raise PolicyViolationError("Dependencies must be objects.")
                target = dependency.get("task_id")
                kind = dependency.get("kind", DependencyType.REQUIRES)
                if target not in known or target == task_id:
                    raise PolicyViolationError("Dependencies must target another task in the plan.")
                try:
                    dependency_type = DependencyType(kind)
                except ValueError as exc:
                    raise PolicyViolationError(f"Unknown dependency kind {kind!r}.") from exc
                edge = (target, dependency_type.value)
                if edge in seen_edges:
                    raise PolicyViolationError("Dependency edges must be unique.")
                seen_edges.add(edge)
                graph[task_id].add(target)
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(node: str) -> None:
            if node in visiting:
                raise PolicyViolationError("REQUIRES dependencies contain a cycle.")
            if node in visited:
                return
            visiting.add(node)
            for child in graph[node]:
                visit(child)
            visiting.remove(node)
            visited.add(node)

        for task_id in ids:
            visit(task_id)

    @staticmethod
    def _plan_graph_metrics(
        content: dict[str, Any],
    ) -> dict[str, tuple[int, int]]:
        children: dict[str, set[str]] = {
            task["id"]: set() for task in content["tasks"]
        }
        for task in content["tasks"]:
            for dependency in task.get("dependencies", []):
                children[dependency["task_id"]].add(task["id"])

        descendants_cache: dict[str, set[str]] = {}
        depth_cache: dict[str, int] = {}

        def descendants(task_id: str) -> set[str]:
            if task_id not in descendants_cache:
                values: set[str] = set()
                for child in children[task_id]:
                    values.add(child)
                    values.update(descendants(child))
                descendants_cache[task_id] = values
            return descendants_cache[task_id]

        def depth(task_id: str) -> int:
            if task_id not in depth_cache:
                depth_cache[task_id] = (
                    0
                    if not children[task_id]
                    else 1 + max(depth(child) for child in children[task_id])
                )
            return depth_cache[task_id]

        return {
            task_id: (depth(task_id), len(descendants(task_id)))
            for task_id in children
        }

    def approve_plan(
        self,
        plan_id: str,
        revision: int,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "plan_id": plan_id, "revision": revision, "expected_version": expected_version
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "human")
            row = self._plan_row(connection, plan_id, revision)
            self._version(row, expected_version)
            if row["status"] != PlanStatus.DRAFT:
                raise PolicyViolationError("Only a draft plan can be approved.")
            content = json.loads(row["content_json"])
            self._validate_plan_content(content)
            metrics = self._plan_graph_metrics(content)
            materialized_ids: dict[str, str] = {}
            materialized_groups: dict[str, str] = {}
            for group in content.get("groups", []):
                logical_group_id = group["id"]
                storage_group_id = logical_group_id
                if connection.execute(
                    "SELECT 1 FROM plan_groups WHERE id=?",
                    (storage_group_id,),
                ).fetchone():
                    storage_group_id = (
                        f"{plan_id}@{revision}:group:{logical_group_id}"
                    )
                materialized_groups[logical_group_id] = storage_group_id
            for task in content["tasks"]:
                logical_id = task["id"]
                storage_id = logical_id
                if connection.execute(
                    "SELECT 1 FROM tasks WHERE id=?", (storage_id,)
                ).fetchone():
                    storage_id = f"{plan_id}@{revision}:{logical_id}"
                if connection.execute(
                    "SELECT 1 FROM tasks WHERE id=?", (storage_id,)
                ).fetchone():
                    raise VersionConflictError(
                        f"Task identity {logical_id!r} is already materialized."
                    )
                materialized_ids[logical_id] = storage_id
            connection.execute(
                """
                UPDATE plans SET status='SUPERSEDED'
                WHERE id=? AND status='ACTIVE' AND revision<>?
                """,
                (plan_id, revision),
            )
            version = row["version"] + 1
            now = _iso()
            connection.execute(
                """
                UPDATE plans SET status='ACTIVE', version=?, approved_by=?, approved_at=?
                WHERE id=? AND revision=?
                """,
                (version, principal.id, now, plan_id, revision),
            )
            for group in content.get("groups", []):
                connection.execute(
                    """
                    INSERT INTO plan_groups(
                        id, plan_id, plan_revision, kind, title, position
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        materialized_groups[group["id"]],
                        plan_id,
                        revision,
                        group["kind"],
                        group["title"],
                        group.get("position", 0),
                    ),
                )
            for position, task in enumerate(content["tasks"]):
                storage_id = materialized_ids[task["id"]]
                critical_path, downstream_count = metrics[task["id"]]
                evidence_profile = task.get("evidence_profile", "default")
                evidence_policy = self.evidence_profiles[evidence_profile]
                git_required = bool(
                    evidence_policy.get("require_local_checkpoint")
                    or evidence_policy.get("require_local_integration")
                )
                connection.execute(
                    """
                    INSERT INTO tasks(
                        id, plan_id, plan_revision, title, objective, scope,
                        acceptance_json, tests_json, priority, risk, suggested_profile,
                        group_id, evidence_profile, evidence_policy_json,
                        state, version, plan_order, critical_path, downstream_count,
                        git_required, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'BACKLOG', 0, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        storage_id, plan_id, revision, task["title"], task.get("objective", ""),
                        task.get("scope", ""), _json(task.get("acceptance", [])),
                        _json(task.get("tests", [])), task.get("priority", "P2"),
                        task.get("risk", "MEDIUM"), task.get("suggested_profile", "worker"),
                        materialized_groups.get(task.get("group_id")),
                        evidence_profile, _json(evidence_policy),
                        position, critical_path,
                        downstream_count, int(git_required),
                        now, now,
                    ),
                )
                for path in {_normalized_path(item) for item in task.get("paths", [])}:
                    connection.execute(
                        "INSERT INTO task_paths(task_id, path) VALUES (?, ?)",
                        (storage_id, path),
                    )
            for task in content["tasks"]:
                for dependency in task.get("dependencies", []):
                    connection.execute(
                        """
                        INSERT INTO dependencies(task_id, depends_on_task_id, kind)
                        VALUES (?, ?, ?)
                        """,
                        (
                            materialized_ids[task["id"]],
                            materialized_ids[dependency["task_id"]],
                            dependency.get("kind", DependencyType.REQUIRES),
                        ),
                    )
            self._event(
                connection, "plan", plan_id, "PLAN_APPROVED", principal,
                {
                    "revision": revision,
                    "task_count": len(content["tasks"]),
                    "materialized_task_ids": materialized_ids,
                },
                version,
            )
            return CommandResult(
                plan_id, version, PlanStatus.ACTIVE,
                data={
                    "revision": revision,
                    "task_count": len(content["tasks"]),
                    "materialized_task_ids": materialized_ids,
                },
            )

        return self._mutate(
            operation="plan_approve", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def register_agent(
        self,
        agent_id: str,
        profile: str,
        capacity: int,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "agent_id": agent_id, "profile": profile, "capacity": capacity,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator")
            if capacity < 1:
                raise PolicyViolationError("Agent capacity must be positive.")
            row = connection.execute("SELECT * FROM agents WHERE id=?", (agent_id,)).fetchone()
            if row:
                self._version(row, expected_version)
                version = row["version"] + 1
                connection.execute(
                    "UPDATE agents SET profile=?, capacity=?, version=? WHERE id=?",
                    (profile, capacity, version, agent_id),
                )
            else:
                if expected_version != 0:
                    raise VersionConflictError("A new agent must use expected_version=0.")
                version = 0
                connection.execute(
                    "INSERT INTO agents(id, profile, capacity) VALUES (?, ?, ?)",
                    (agent_id, profile, capacity),
                )
            self._event(
                connection, "agent", agent_id, "AGENT_REGISTERED", principal,
                {"profile": profile, "capacity": capacity}, version,
            )
            return CommandResult(agent_id, version, "ENABLED")

        return self._mutate(
            operation="agent_register", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def stale_candidates(self) -> list[dict[str, Any]]:
        with connect(self._path()) as connection:
            rows = connection.execute(
                """
                SELECT a.id AS assignment_id, a.task_id, a.lease_generation,
                       a.expires_at, t.version
                FROM assignments a
                JOIN tasks t ON t.id=a.task_id
                WHERE a.status IN ('RESERVED','ACCEPTED') AND a.expires_at <= ?
                ORDER BY a.expires_at, a.id
                """,
                (_iso(),),
            ).fetchall()
        return [dict(row) for row in rows]

    def expire_stale_assignment(
        self,
        task_id: str,
        assignment_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "assignment_id": assignment_id,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator")
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=? AND task_id=?",
                (assignment_id, task_id),
            ).fetchone()
            now = _iso()
            if (
                not assignment
                or assignment["status"]
                not in (AssignmentStatus.RESERVED, AssignmentStatus.ACCEPTED)
                or assignment["expires_at"] > now
            ):
                raise PolicyViolationError("Assignment is not stale.")
            self._release(connection, assignment_id, status="EXPIRED")
            abandoned_review_ids = self._abandon_pending_reviews(
                connection,
                task_id,
                "Assignment lease expired before review completed.",
            )
            connection.execute(
                """
                UPDATE runs SET status='STALE', finished_at=?
                WHERE assignment_id=? AND status IN ('RUNNING','SUCCEEDED')
                """,
                (now, assignment_id),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='BACKLOG', version=?, updated_at=? WHERE id=?",
                (version, now, task_id),
            )
            self._event(
                connection,
                "task",
                task_id,
                "ASSIGNMENT_EXPIRED",
                principal,
                {
                    "assignment_id": assignment_id,
                    "generation": assignment["lease_generation"],
                    "abandoned_review_ids": abandoned_review_ids,
                },
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.BACKLOG,
                data={
                    "assignment_id": assignment_id,
                    "expired": True,
                    "abandoned_review_ids": abandoned_review_ids,
                },
            )

        return self._mutate(
            operation="assignment_expire",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def register_agent_instance(
        self,
        instance_id: str,
        agent_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        thread_id: str | None = None,
    ) -> CommandResult:
        payload = {
            "instance_id": instance_id,
            "agent_id": agent_id,
            "thread_id": thread_id,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator")
            agent = connection.execute(
                "SELECT * FROM agents WHERE id=? AND enabled=1", (agent_id,)
            ).fetchone()
            if not agent:
                raise NotFoundError(f"Agent {agent_id!r} was not found.")
            self._version(agent, expected_version)
            if connection.execute(
                "SELECT 1 FROM agent_instances WHERE id=?", (instance_id,)
            ).fetchone():
                raise VersionConflictError("Agent instance already exists.")
            connection.execute(
                """
                INSERT INTO agent_instances(id, agent_id, thread_id, status, created_at)
                VALUES (?, ?, ?, 'STARTING', ?)
                """,
                (instance_id, agent_id, thread_id, _iso()),
            )
            version = agent["version"] + 1
            connection.execute(
                "UPDATE agents SET version=? WHERE id=?", (version, agent_id)
            )
            self._event(
                connection,
                "agent_instance",
                instance_id,
                "AGENT_INSTANCE_REGISTERED",
                principal,
                {"agent_id": agent_id, "thread_id": thread_id},
                version,
            )
            return CommandResult(
                instance_id,
                version,
                "STARTING",
                data={"agent_id": agent_id, "thread_id": thread_id},
            )

        return self._mutate(
            operation="agent_instance_register",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def _readiness(
        self, connection: sqlite3.Connection, task_id: str
    ) -> tuple[bool, list[str]]:
        row = self._task_row(connection, task_id)
        reasons: list[str] = []
        plan = connection.execute(
            "SELECT status FROM plans WHERE id=? AND revision=?",
            (row["plan_id"], row["plan_revision"]),
        ).fetchone()
        if not plan or plan["status"] != PlanStatus.ACTIVE:
            reasons.append("plan_revision_not_active")
        if row["state"] != TaskState.BACKLOG:
            reasons.append(f"phase:{row['state']}")
        if connection.execute(
            "SELECT 1 FROM blockers WHERE task_id=? AND status='OPEN'", (task_id,)
        ).fetchone():
            reasons.append("open_blocker")
        missing = connection.execute(
            """
            SELECT d.depends_on_task_id, t.state
            FROM dependencies d
            JOIN tasks t ON t.id=d.depends_on_task_id
            LEFT JOIN dependency_waivers w
              ON w.task_id=d.task_id AND w.depends_on_task_id=d.depends_on_task_id
             AND w.kind=d.kind
            WHERE d.task_id=? AND d.kind='REQUIRES' AND t.state<>'DONE' AND w.id IS NULL
            ORDER BY d.depends_on_task_id
            """,
            (task_id,),
        ).fetchall()
        reasons.extend(f"requires:{item['depends_on_task_id']}:{item['state']}" for item in missing)
        return not reasons, reasons

    def claim_task(
        self,
        task_id: str,
        agent_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        instance_id: str | None = None,
        config_revision: str | None = None,
        capability_token: str | None = None,
    ) -> CommandResult:
        capability_hash = (
            hashlib.sha256(capability_token.encode()).hexdigest()
            if capability_token is not None
            else None
        )
        payload = {
            "task_id": task_id, "agent_id": agent_id, "expected_version": expected_version,
            "instance_id": instance_id, "config_revision": config_revision,
            "capability_hash": capability_hash,
        }
        intent_path: Path | None = None

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            nonlocal intent_path
            self._require_role(principal, "orchestrator")
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            ready, reasons = self._readiness(connection, task_id)
            if not ready:
                raise PolicyViolationError("Task is not eligible: " + ", ".join(reasons))
            agent = connection.execute(
                "SELECT * FROM agents WHERE id=? AND enabled=1", (agent_id,)
            ).fetchone()
            if not agent:
                raise PolicyViolationError("Agent is not registered or is disabled.")
            if agent["profile"] != task["suggested_profile"]:
                raise PolicyViolationError(
                    f"Task requires profile {task['suggested_profile']!r}."
                )
            if instance_id:
                instance = connection.execute(
                    "SELECT * FROM agent_instances WHERE id=?", (instance_id,)
                ).fetchone()
                if not instance or instance["agent_id"] != agent_id:
                    raise PolicyViolationError(
                        "Agent instance is unknown or belongs to another profile."
                    )
            active_count = connection.execute(
                "SELECT COUNT(*) AS count FROM assignments WHERE status IN ('RESERVED','ACCEPTED')"
            ).fetchone()["count"]
            if active_count >= self.wip_limit:
                raise PolicyViolationError(f"Project WIP limit {self.wip_limit} is full.")
            profile_limit = self.profile_wip_limits.get(agent["profile"])
            if profile_limit is not None:
                profile_count = connection.execute(
                    """
                    SELECT COUNT(*) AS count
                    FROM assignments active
                    JOIN agents assigned_agent ON assigned_agent.id=active.agent_id
                    WHERE assigned_agent.profile=?
                      AND active.status IN ('RESERVED','ACCEPTED')
                    """,
                    (agent["profile"],),
                ).fetchone()["count"]
                if profile_count >= profile_limit:
                    raise PolicyViolationError(
                        f"Profile WIP limit {profile_limit} is full for "
                        f"{agent['profile']!r}."
                    )
            agent_count = connection.execute(
                """
                SELECT COUNT(*) AS count FROM assignments
                WHERE agent_id=? AND status IN ('RESERVED','ACCEPTED')
                """,
                (agent_id,),
            ).fetchone()["count"]
            if agent_count >= agent["capacity"]:
                raise PolicyViolationError("Agent capacity is full.")
            paths = [
                item["path"]
                for item in connection.execute(
                    "SELECT path FROM task_paths WHERE task_id=?", (task_id,)
                ).fetchall()
            ]
            active_paths = connection.execute(
                """
                SELECT lp.path, l.task_id FROM lease_paths lp
                JOIN leases l ON l.id=lp.lease_id
                WHERE l.status='ACTIVE' AND l.expires_at > ?
                """,
                (_iso(),),
            ).fetchall()
            for path in paths:
                for leased in active_paths:
                    if leased["task_id"] != task_id and _paths_overlap(path, leased["path"]):
                        raise PolicyViolationError(
                            f"Path lease conflicts with task {leased['task_id']}: {path}."
                        )
            generation = connection.execute(
                "SELECT COALESCE(MAX(lease_generation),0)+1 AS value FROM assignments WHERE task_id=?",
                (task_id,),
            ).fetchone()["value"]
            active_config = connection.execute(
                """
                SELECT id, revision, content_hash
                FROM config_revisions
                WHERE status='ACTIVE'
                ORDER BY revision DESC
                LIMIT 1
                """
            ).fetchone()
            active_config_id = active_config["id"] if active_config else None
            if config_revision is not None and config_revision != active_config_id:
                raise VersionConflictError(
                    "Requested configuration revision is not currently active."
                )
            policy_snapshot = {
                "config_revision_id": active_config_id,
                "config_revision": (
                    active_config["revision"] if active_config is not None else None
                ),
                "config_content_hash": (
                    active_config["content_hash"] if active_config is not None else None
                ),
                "reservation_ttl_seconds": self.reservation_ttl_seconds,
                "lease_ttl_seconds": self.lease_ttl_seconds,
                "transient_max_attempts": self.transient_max_attempts,
            }
            assignment_id = _id()
            lease_id = _id()
            expires_at = _iso(_now() + timedelta(seconds=self.reservation_ttl_seconds))
            connection.execute(
                """
                INSERT INTO assignments(
                    id, task_id, agent_id, instance_id, status, lease_generation,
                    reserved_at, expires_at, config_revision_id, policy_snapshot_json
                ) VALUES (?, ?, ?, ?, 'RESERVED', ?, ?, ?, ?, ?)
                """,
                (
                    assignment_id,
                    task_id,
                    agent_id,
                    instance_id,
                    generation,
                    _iso(),
                    expires_at,
                    active_config_id,
                    _json(policy_snapshot),
                ),
            )
            connection.execute(
                """
                INSERT INTO leases(id, assignment_id, task_id, generation, status, expires_at)
                VALUES (?, ?, ?, ?, 'ACTIVE', ?)
                """,
                (lease_id, assignment_id, task_id, generation, expires_at),
            )
            for path in paths:
                connection.execute(
                    "INSERT INTO lease_paths(lease_id, path) VALUES (?, ?)", (lease_id, path)
                )
            git_data: dict[str, str] = {}
            if task["git_required"]:
                root = self._project_root()
                if not (root / ".git").exists():
                    raise PolicyViolationError(
                        "Task requires local Git, but the project is not a Git worktree."
                    )
                adapter = LocalGitAdapter(root)
                try:
                    adapter.assert_repository_safe()
                    plan_branch = adapter.ensure_plan_branch(
                        task["plan_id"], adapter.head_sha()
                    )
                    start_sha = adapter.resolve_ref(plan_branch)
                    worktree, run_branch = adapter.run_worktree_spec(
                        plan_id=task["plan_id"],
                        run_id=assignment_id,
                    )
                    intent_path = self._write_external_intent(
                        "worktree_create",
                        idempotency_key,
                        {
                            "task_id": task_id,
                            "assignment_id": assignment_id,
                            "worktree_path": str(worktree),
                            "branch_ref": run_branch,
                            "start_sha": start_sha,
                        },
                    )
                    worktree, run_branch = adapter.create_run_worktree(
                        plan_id=task["plan_id"],
                        run_id=assignment_id,
                        start_sha=start_sha,
                    )
                except GitPolicyError as exc:
                    raise PolicyViolationError(str(exc)) from exc
                git_data = {
                    "worktree_path": str(worktree),
                    "branch_ref": run_branch,
                    "start_sha": start_sha,
                    "plan_branch": plan_branch,
                }
                connection.execute(
                    """
                    UPDATE assignments
                    SET worktree_path=?, branch_ref=?, start_sha=?
                    WHERE id=?
                    """,
                    (str(worktree), run_branch, start_sha, assignment_id),
                )
            if capability_hash:
                profile = str(agent["profile"]).casefold()
                roles = ["worker"]
                if "reviewer" in profile:
                    roles.append("reviewer")
                if "orchestrator" in profile:
                    roles.append("orchestrator")
                connection.execute(
                    """
                    INSERT INTO capabilities(
                        token_hash, project_id, actor_id, roles_json, task_id, instance_id,
                        assignment_id, lease_generation, operations_json, expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capability_hash,
                        task["plan_id"],
                        agent_id,
                        _json(roles),
                        task_id,
                        instance_id,
                        assignment_id,
                        generation,
                        _json(["run_start"]),
                        expires_at,
                        _iso(),
                    ),
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, "TASK_RESERVED", principal,
                {
                    "assignment_id": assignment_id, "agent_id": agent_id,
                    "lease_generation": generation, "expires_at": expires_at,
                    "config_revision_id": active_config_id,
                }, version,
            )
            return CommandResult(
                task_id, version, "ASSIGNED",
                data={
                    "assignment_id": assignment_id, "lease_id": lease_id,
                    "lease_generation": generation, "expires_at": expires_at,
                    "config_revision_id": active_config_id,
                    **git_data,
                },
            )

        try:
            result = self._mutate(
                operation="task_claim", actor=actor, idempotency_key=idempotency_key,
                payload=payload, action=action,
            )
        except Exception:
            self._recover_external_intents()
            raise
        if intent_path is not None:
            intent_path.unlink(missing_ok=True)
        return result

    def run_start(
        self,
        task_id: str,
        assignment_id: str,
        lease_generation: int,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        thread_id: str | None = None,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "assignment_id": assignment_id,
            "lease_generation": lease_generation, "expected_version": expected_version,
            "thread_id": thread_id,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=? AND task_id=?",
                (assignment_id, task_id),
            ).fetchone()
            if not assignment or assignment["status"] != AssignmentStatus.RESERVED:
                raise PolicyViolationError("Assignment is not an active reservation.")
            if assignment["agent_id"] != principal.id:
                raise PolicyViolationError("Only the assigned worker can start this run.")
            if assignment["lease_generation"] != lease_generation:
                raise LeaseFencedError("Lease generation is stale.")
            if assignment["expires_at"] <= _iso():
                raise LeaseFencedError("Reservation has expired.")
            policy = self._assignment_policy_snapshot(assignment)
            expires_at = _iso(
                _now() + timedelta(seconds=policy["lease_ttl_seconds"])
            )
            connection.execute(
                """
                UPDATE assignments
                SET status='ACCEPTED', accepted_at=?, expires_at=?
                WHERE id=?
                """,
                (_iso(), expires_at, assignment_id),
            )
            connection.execute(
                "UPDATE leases SET expires_at=? WHERE assignment_id=?",
                (expires_at, assignment_id),
            )
            run_id = _id()
            attempt = connection.execute(
                "SELECT COUNT(*)+1 AS value FROM runs WHERE task_id=?", (task_id,)
            ).fetchone()["value"]
            connection.execute(
                """
                INSERT INTO runs(
                    id, task_id, assignment_id, actor_id, status, lease_generation,
                    plan_revision, attempt, started_at, heartbeat_at,
                    worktree_path, branch_ref, start_sha
                ) VALUES (?, ?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id, task_id, assignment_id, principal.id, lease_generation,
                    task["plan_revision"], attempt, _iso(), _iso(),
                    assignment["worktree_path"], assignment["branch_ref"],
                    assignment["start_sha"],
                ),
            )
            connection.execute(
                """
                UPDATE capabilities
                SET run_id=?, operations_json=?, expires_at=?
                WHERE assignment_id=? AND revoked_at IS NULL
                """,
                (
                    run_id,
                    _json(
                        [
                            "task_heartbeat",
                            "task_block",
                            "task_report_result",
                            "run_fail",
                            "git_checkpoint",
                        ]
                    ),
                    expires_at,
                    assignment_id,
                ),
            )
            if assignment["instance_id"]:
                connection.execute(
                    "UPDATE agent_instances SET thread_id=?, status='RUNNING' WHERE id=?",
                    (thread_id, assignment["instance_id"]),
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='IN_PROGRESS', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "run", run_id, "RUN_STARTED", principal,
                {"task_id": task_id, "lease_generation": lease_generation}, version,
            )
            return CommandResult(
                task_id, version, TaskState.IN_PROGRESS,
                data={
                    "run_id": run_id,
                    "assignment_id": assignment_id,
                    "expires_at": expires_at,
                    "worktree_path": assignment["worktree_path"],
                    "branch_ref": assignment["branch_ref"],
                    "start_sha": assignment["start_sha"],
                },
            )

        return self._mutate(
            operation="run_start", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def task_release_reservation(
        self,
        task_id: str,
        assignment_id: str,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "assignment_id": assignment_id,
            "reason": reason,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator", "human")
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=? AND task_id=?",
                (assignment_id, task_id),
            ).fetchone()
            if not assignment or assignment["status"] != AssignmentStatus.RESERVED:
                raise PolicyViolationError(
                    "Only an unaccepted reservation can be released after spawn failure."
                )
            self._release(connection, assignment_id)
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection,
                "assignment",
                assignment_id,
                "ASSIGNMENT_RELEASED",
                principal,
                {"task_id": task_id, "reason": reason},
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.BACKLOG,
                data={"assignment_id": assignment_id, "released": True},
            )

        return self._mutate(
            operation="task_release_reservation",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def _active_run(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        run_id: str,
        principal: Actor,
        generation: int,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        run = connection.execute(
            "SELECT * FROM runs WHERE id=? AND task_id=?", (run_id, task_id)
        ).fetchone()
        if not run or run["status"] != RunStatus.RUNNING:
            raise PolicyViolationError("Run is not active.")
        if run["actor_id"] != principal.id:
            raise PolicyViolationError("Worker can only mutate its own run.")
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
        ).fetchone()
        if (
            run["lease_generation"] != generation
            or assignment["lease_generation"] != generation
        ):
            raise LeaseFencedError("Lease generation is stale.")
        if assignment["status"] != AssignmentStatus.ACCEPTED:
            raise LeaseFencedError("Assignment is not accepted.")
        if assignment["expires_at"] <= _iso():
            raise LeaseFencedError("Lease has expired.")
        return run, assignment

    def task_heartbeat(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        checkpoint: dict[str, Any] | None = None,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "run_id": run_id, "lease_generation": lease_generation,
            "expected_version": expected_version, "checkpoint": checkpoint,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            run, assignment = self._active_run(
                connection, task_id, run_id, principal, lease_generation
            )
            policy = self._assignment_policy_snapshot(assignment)
            expires_at = _iso(
                _now() + timedelta(seconds=policy["lease_ttl_seconds"])
            )
            connection.execute(
                "UPDATE runs SET heartbeat_at=? WHERE id=?", (_iso(), run_id)
            )
            connection.execute(
                "UPDATE assignments SET expires_at=? WHERE id=?",
                (expires_at, assignment["id"]),
            )
            connection.execute(
                "UPDATE leases SET expires_at=? WHERE assignment_id=?",
                (expires_at, assignment["id"]),
            )
            connection.execute(
                """
                UPDATE capabilities SET expires_at=?
                WHERE assignment_id=? AND revoked_at IS NULL
                """,
                (expires_at, assignment["id"]),
            )
            if checkpoint:
                connection.execute(
                    """
                    INSERT INTO checkpoints(id, run_id, kind, summary, payload_json, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id(), run_id, checkpoint.get("kind", "progress"),
                        checkpoint.get("summary", ""), _json(checkpoint), _iso(),
                    ),
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "run", run_id, "RUN_HEARTBEAT", principal,
                {"expires_at": expires_at, "checkpoint": bool(checkpoint)}, version,
            )
            return CommandResult(
                task_id, version, task["state"],
                data={"run_id": run["id"], "expires_at": expires_at},
            )

        return self._mutate(
            operation="task_heartbeat", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def task_report_result(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        evidence: list[dict[str, Any]],
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        summary: str | None = None,
        checkpoint_sha: str | None = None,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "run_id": run_id, "lease_generation": lease_generation,
            "evidence": evidence, "summary": summary,
            "checkpoint_sha": checkpoint_sha, "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            run, _ = self._active_run(
                connection, task_id, run_id, principal, lease_generation
            )
            if task["state"] != TaskState.IN_PROGRESS:
                raise InvalidTransitionError("Only an in-progress task can report a result.")
            if checkpoint_sha is not None and checkpoint_sha != run["checkpoint_sha"]:
                raise VersionConflictError(
                    "Reported checkpoint SHA does not match the AgentBoard checkpoint."
                )
            if not evidence:
                raise PolicyViolationError("Result evidence is required.")
            evidence_policy = json.loads(task["evidence_policy_json"] or "{}")
            successful_kinds: set[str] = set()
            for item in evidence:
                payload_data = item.get("payload", {})
                if payload_data.get("passed", True) is not True:
                    raise PolicyViolationError(
                        "Failed evidence cannot advance a task to verification."
                    )
                successful_kinds.add(str(item.get("kind", "")).casefold())
            required_kinds = {
                "changed_files": {"changed_files", "files"},
                "tests": {"tests", "test"},
                "acceptance": {"acceptance", "acceptance_criteria"},
            }
            requirements = {
                "changed_files": bool(evidence_policy.get("require_changed_files")),
                "tests": bool(evidence_policy.get("require_tests")),
                "acceptance": bool(
                    evidence_policy.get("require_acceptance_evidence")
                ),
            }
            missing = [
                label
                for label, required in requirements.items()
                if required and successful_kinds.isdisjoint(required_kinds[label])
            ]
            if missing:
                raise PolicyViolationError(
                    "Evidence profile requirements are missing: " + ", ".join(missing)
                )
            checkpoint_required = bool(
                evidence_policy.get("require_local_checkpoint")
                or evidence_policy.get("require_local_integration")
            )
            if checkpoint_required and not connection.execute(
                """
                SELECT 1 FROM git_operations
                WHERE task_id=? AND run_id=? AND kind='CHECKPOINT' AND status='SUCCEEDED'
                """,
                (task_id, run_id),
            ).fetchone():
                raise PolicyViolationError(
                    "Git-required work needs an AgentBoard checkpoint before result reporting."
                )
            for item in evidence:
                if not item.get("kind") or not item.get("summary"):
                    raise PolicyViolationError("Evidence requires kind and summary.")
                if item["kind"] in {
                    "git_checkpoint",
                    "git_integration",
                    "review",
                    "human_approval",
                }:
                    raise PolicyViolationError(
                        f"Evidence kind {item['kind']!r} is reserved for AgentBoard."
                    )
                connection.execute(
                    """
                    INSERT INTO evidence(
                        id, task_id, run_id, kind, summary, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id(), task_id, run_id, item["kind"], item["summary"],
                        _json(item.get("payload", {})), _iso(),
                    ),
                )
            connection.execute(
                """
                UPDATE runs
                SET status='SUCCEEDED', result_summary=?, finished_at=?
                WHERE id=?
                """,
                (summary, _iso(), run_id),
            )
            review_id = _id()
            connection.execute(
                """
                INSERT INTO reviews(id, task_id, run_id, status, created_at)
                VALUES (?, ?, ?, 'PENDING', ?)
                """,
                (review_id, task_id, run_id, _iso()),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='VERIFYING', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, "TASK_READY_FOR_REVIEW", principal,
                {"run_id": run_id, "review_id": review_id, "evidence_count": len(evidence)},
                version,
            )
            return CommandResult(
                task_id, version, TaskState.VERIFYING,
                data={"run_id": run_id, "review_id": review_id},
            )

        return self._mutate(
            operation="task_report_result", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def git_checkpoint(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        expected_head: str,
        message: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "run_id": run_id,
            "lease_generation": lease_generation,
            "expected_head": expected_head,
            "message": message,
            "expected_version": expected_version,
        }
        intent_path: Path | None = None

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            nonlocal intent_path
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            run, _ = self._active_run(
                connection, task_id, run_id, principal, lease_generation
            )
            if not task["git_required"] or not run["worktree_path"]:
                raise PolicyViolationError("This run has no AgentBoard-managed Git worktree.")
            paths = tuple(
                row["path"]
                for row in connection.execute(
                    "SELECT path FROM task_paths WHERE task_id=? ORDER BY path", (task_id,)
                )
            )
            intent_path = self._write_external_intent(
                "git_checkpoint",
                idempotency_key,
                {
                    "task_id": task_id,
                    "run_id": run_id,
                    "worktree_path": run["worktree_path"],
                    "expected_sha": expected_head,
                },
            )
            try:
                checkpoint = LocalGitAdapter(self._project_root()).checkpoint(
                    worktree=Path(run["worktree_path"]),
                    expected_head=expected_head,
                    allowed_paths=paths,
                    message=message,
                    actor=principal.id,
                )
            except GitPolicyError as exc:
                raise PolicyViolationError(str(exc)) from exc
            self._update_external_intent(
                intent_path,
                effect_completed=True,
                result_sha=checkpoint.commit_sha,
            )
            operation_id = _id()
            now = _iso()
            connection.execute(
                """
                INSERT INTO git_operations(
                    id, task_id, run_id, kind, status, target_sha, result_sha,
                    actor_id, created_at
                ) VALUES (?, ?, ?, 'CHECKPOINT', 'SUCCEEDED', ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    task_id,
                    run_id,
                    expected_head,
                    checkpoint.commit_sha,
                    principal.id,
                    now,
                ),
            )
            evidence_id = _id()
            evidence_payload = {
                "operation_id": operation_id,
                "commit_sha": checkpoint.commit_sha,
                "parent_sha": checkpoint.parent_sha,
                "paths": checkpoint.paths,
            }
            connection.execute(
                """
                INSERT INTO evidence(
                    id, task_id, run_id, kind, summary, payload_json, created_at
                ) VALUES (?, ?, ?, 'git_checkpoint', ?, ?, ?)
                """,
                (
                    evidence_id,
                    task_id,
                    run_id,
                    f"Local checkpoint {checkpoint.commit_sha[:12]}",
                    _json(evidence_payload),
                    now,
                ),
            )
            connection.execute(
                """
                INSERT INTO checkpoints(id, run_id, kind, summary, payload_json, created_at)
                VALUES (?, ?, 'git', ?, ?, ?)
                """,
                (_id(), run_id, message, _json(evidence_payload), now),
            )
            connection.execute(
                "UPDATE runs SET checkpoint_sha=? WHERE id=?",
                (checkpoint.commit_sha, run_id),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, now, task_id),
            )
            self._event(
                connection,
                "git",
                operation_id,
                "GIT_CHECKPOINT_CREATED",
                principal,
                evidence_payload,
                version,
            )
            return CommandResult(
                task_id,
                version,
                task["state"],
                data=evidence_payload,
            )

        try:
            result = self._mutate(
                operation="git_checkpoint",
                actor=actor,
                idempotency_key=idempotency_key,
                payload=payload,
                action=action,
            )
        except Exception:
            self._recover_external_intents()
            raise
        if intent_path is not None:
            intent_path.unlink(missing_ok=True)
        return result

    def _release(
        self, connection: sqlite3.Connection, assignment_id: str, status: str = "RELEASED"
    ) -> None:
        assignment = connection.execute(
            "SELECT instance_id FROM assignments WHERE id=?",
            (assignment_id,),
        ).fetchone()
        connection.execute(
            "UPDATE assignments SET status=?, released_at=? WHERE id=?",
            (status, _iso(), assignment_id),
        )
        connection.execute(
            "UPDATE leases SET status=? WHERE assignment_id=?",
            ("EXPIRED" if status == "EXPIRED" else "RELEASED", assignment_id),
        )
        connection.execute(
            """
            UPDATE capabilities SET revoked_at=?
            WHERE assignment_id=? AND revoked_at IS NULL
            """,
            (_iso(), assignment_id),
        )
        if assignment and assignment["instance_id"]:
            self._mark_instance_idle_if_unused(
                connection,
                assignment["instance_id"],
            )

    @staticmethod
    def _mark_instance_idle_if_unused(
        connection: sqlite3.Connection,
        instance_id: str,
    ) -> None:
        in_use = connection.execute(
            """
            SELECT 1
            FROM assignments
            WHERE instance_id=? AND status IN ('RESERVED','ACCEPTED')
            UNION ALL
            SELECT 1
            FROM reviews
            WHERE reviewer_instance_id=? AND status='PENDING'
            LIMIT 1
            """,
            (instance_id, instance_id),
        ).fetchone()
        if not in_use:
            connection.execute(
                "UPDATE agent_instances SET status='IDLE' WHERE id=?",
                (instance_id,),
            )

    def _abandon_pending_reviews(
        self,
        connection: sqlite3.Connection,
        task_id: str,
        reason: str,
    ) -> list[str]:
        reviews = connection.execute(
            """
            SELECT id, reviewer_instance_id
            FROM reviews
            WHERE task_id=? AND status='PENDING'
            ORDER BY created_at, id
            """,
            (task_id,),
        ).fetchall()
        if not reviews:
            return []
        now = _iso()
        connection.execute(
            """
            UPDATE reviews
            SET status='ABANDONED', decision_reason=?, decided_at=?
            WHERE task_id=? AND status='PENDING'
            """,
            (reason, now, task_id),
        )
        connection.execute(
            """
            UPDATE capabilities SET revoked_at=?
            WHERE task_id=? AND revoked_at IS NULL
            """,
            (now, task_id),
        )
        for instance_id in {
            review["reviewer_instance_id"]
            for review in reviews
            if review["reviewer_instance_id"]
        }:
            self._mark_instance_idle_if_unused(connection, instance_id)
        return [review["id"] for review in reviews]

    def run_fail(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        failure_kind: str,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        return self._end_run(
            task_id,
            run_id,
            lease_generation,
            RunStatus.FAILED,
            failure_kind,
            reason,
            expected_version=expected_version, actor=actor, idempotency_key=idempotency_key,
        )

    def task_block(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        owner: str | None = None,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "run_id": run_id, "lease_generation": lease_generation,
            "reason": reason, "owner": owner, "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            run, assignment = self._active_run(
                connection, task_id, run_id, principal, lease_generation
            )
            connection.execute(
                "UPDATE runs SET status='BLOCKED', finished_at=? WHERE id=?", (_iso(), run_id)
            )
            blocker_id = _id()
            connection.execute(
                """
                INSERT INTO blockers(
                    id, task_id, run_id, status, reason, owner, previous_state, created_at
                ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?)
                """,
                (blocker_id, task_id, run_id, reason, owner, task["state"], _iso()),
            )
            self._release(connection, assignment["id"])
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='BACKLOG', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, "TASK_BLOCKED", principal,
                {"run_id": run["id"], "blocker_id": blocker_id, "reason": reason}, version,
            )
            return CommandResult(
                task_id, version, "BLOCKED", data={"blocker_id": blocker_id}
            )

        return self._mutate(
            operation="task_block", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def _end_run(
        self,
        task_id: str,
        run_id: str,
        lease_generation: int,
        run_status: RunStatus,
        failure_kind: str,
        failure_reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "run_id": run_id, "lease_generation": lease_generation,
            "run_status": run_status, "failure_kind": failure_kind,
            "failure_reason": failure_reason,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            run, assignment = self._active_run(
                connection, task_id, run_id, principal, lease_generation
            )
            connection.execute(
                """
                UPDATE runs
                SET status=?, failure_kind=?, failure_reason=?, finished_at=?
                WHERE id=?
                """,
                (run_status, failure_kind, failure_reason, _iso(), run_id),
            )
            self._release(connection, assignment["id"])
            policy = self._assignment_policy_snapshot(assignment)
            automatic_retry = (
                run_status == RunStatus.FAILED
                and failure_kind == "TRANSIENT"
                and run["attempt"] < policy["transient_max_attempts"]
            )
            blocker_id: str | None = None
            if not automatic_retry:
                blocker_id = _id()
                connection.execute(
                    """
                    INSERT INTO blockers(
                        id, task_id, run_id, status, reason, owner,
                        previous_state, created_at
                    ) VALUES (?, ?, ?, 'OPEN', ?, ?, ?, ?)
                    """,
                    (
                        blocker_id,
                        task_id,
                        run_id,
                        failure_reason,
                        principal.id,
                        task["state"],
                        _iso(),
                    ),
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='BACKLOG', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "run", run_id, f"RUN_{run_status}", principal,
                {
                    "failure_kind": failure_kind,
                    "failure_reason": failure_reason,
                    "automatic_retry": automatic_retry,
                    "blocker_id": blocker_id,
                },
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.BACKLOG,
                data={
                    "run_id": run_id,
                    "automatic_retry": automatic_retry,
                    "blocker_id": blocker_id,
                },
            )

        return self._mutate(
            operation=f"run_{run_status.value.lower()}", actor=actor,
            idempotency_key=idempotency_key, payload=payload, action=action,
        )

    def blocker_resolve(
        self,
        task_id: str,
        blocker_id: str,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        waive: bool = False,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "blocker_id": blocker_id,
            "reason": reason,
            "waive": waive,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            blocker = connection.execute(
                "SELECT * FROM blockers WHERE id=? AND task_id=?",
                (blocker_id, task_id),
            ).fetchone()
            if not blocker or blocker["status"] != BlockerStatus.OPEN:
                raise PolicyViolationError("Blocker is not open.")
            if waive or blocker["owner"] not in (None, principal.id):
                self._require_role(principal, "orchestrator", "reviewer", "human")
            status = BlockerStatus.WAIVED if waive else BlockerStatus.RESOLVED
            connection.execute(
                """
                UPDATE blockers SET status=?, resolved_at=?, owner=COALESCE(owner, ?)
                WHERE id=?
                """,
                (status, _iso(), principal.id, blocker_id),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection,
                "task",
                task_id,
                f"BLOCKER_{status}",
                principal,
                {"blocker_id": blocker_id, "reason": reason},
                version,
            )
            return CommandResult(
                task_id, version, TaskState.BACKLOG, data={"blocker_id": blocker_id}
            )

        return self._mutate(
            operation="blocker_resolve",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def review_claim(
        self,
        task_id: str,
        review_id: str,
        reviewer_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        capability_token: str | None = None,
        reviewer_instance_id: str | None = None,
    ) -> CommandResult:
        capability_hash = (
            hashlib.sha256(capability_token.encode()).hexdigest()
            if capability_token is not None
            else None
        )
        payload = {
            "task_id": task_id,
            "review_id": review_id,
            "reviewer_id": reviewer_id,
            "reviewer_instance_id": reviewer_instance_id,
            "expected_version": expected_version,
            "capability_hash": capability_hash,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if task["state"] != TaskState.VERIFYING:
                raise InvalidTransitionError("Task is not awaiting review.")
            if not principal.has_any_role("orchestrator", "reviewer", "human"):
                raise PolicyViolationError("Review assignment requires elevated authority.")
            review = connection.execute(
                "SELECT * FROM reviews WHERE id=? AND task_id=?", (review_id, task_id)
            ).fetchone()
            if not review or review["status"] != ReviewStatus.PENDING:
                raise PolicyViolationError("Review is not pending.")
            if review["reviewer_id"] and review["reviewer_id"] != reviewer_id:
                raise PolicyViolationError("Review is already assigned.")
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (review["run_id"],)
            ).fetchone()
            if run["actor_id"] == reviewer_id:
                raise PolicyViolationError("A worker cannot review its own run.")
            risk = RiskLevel(task["risk"])
            roles = ["reviewer"]
            reviewer = connection.execute(
                "SELECT * FROM agents WHERE id=? AND enabled=1", (reviewer_id,)
            ).fetchone()
            if risk in (RiskLevel.HIGH, RiskLevel.CRITICAL):
                if not reviewer or "reviewer" not in reviewer["profile"].casefold():
                    raise PolicyViolationError("High-risk work needs a registered reviewer.")
            elif reviewer_id == principal.id and principal.has_any_role("orchestrator", "human"):
                roles = sorted(principal.roles)
            elif not reviewer:
                raise PolicyViolationError("Reviewer is not registered or is disabled.")
            if reviewer_instance_id is not None:
                instance = connection.execute(
                    "SELECT * FROM agent_instances WHERE id=?",
                    (reviewer_instance_id,),
                ).fetchone()
                if not instance or instance["agent_id"] != reviewer_id:
                    raise PolicyViolationError(
                        "Reviewer instance is unknown or belongs to another reviewer."
                    )
            if reviewer is not None:
                active_reviews = connection.execute(
                    """
                    SELECT COUNT(*) AS count FROM reviews
                    WHERE reviewer_id=? AND status='PENDING' AND id<>?
                    """,
                    (reviewer_id, review_id),
                ).fetchone()["count"]
                if active_reviews >= reviewer["capacity"]:
                    raise PolicyViolationError("Reviewer capacity is full.")
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != AssignmentStatus.ACCEPTED
                or assignment["expires_at"] <= _iso()
            ):
                raise LeaseFencedError("Verification lease is no longer active.")
            connection.execute(
                """
                UPDATE reviews SET reviewer_id=?, reviewer_instance_id=? WHERE id=?
                """,
                (reviewer_id, reviewer_instance_id, review_id),
            )
            if capability_hash:
                connection.execute(
                    """
                    INSERT INTO capabilities(
                        token_hash, project_id, actor_id, roles_json, task_id, run_id,
                        instance_id, assignment_id, lease_generation, operations_json,
                        expires_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capability_hash,
                        task["plan_id"],
                        reviewer_id,
                        _json(roles),
                        task_id,
                        run["id"],
                        reviewer_instance_id,
                        assignment["id"],
                        assignment["lease_generation"],
                        _json(["review_start", "review_decide"]),
                        assignment["expires_at"],
                        _iso(),
                    ),
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection,
                "review",
                review_id,
                "REVIEW_CLAIMED",
                principal,
                {
                    "task_id": task_id,
                    "reviewer_id": reviewer_id,
                    "reviewer_instance_id": reviewer_instance_id,
                },
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.VERIFYING,
                data={"review_id": review_id, "reviewer_id": reviewer_id},
            )

        return self._mutate(
            operation="review_claim",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def review_start(
        self,
        task_id: str,
        review_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        thread_id: str | None = None,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "review_id": review_id,
            "thread_id": thread_id,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            review = connection.execute(
                "SELECT * FROM reviews WHERE id=? AND task_id=?", (review_id, task_id)
            ).fetchone()
            if (
                not review
                or review["status"] != ReviewStatus.PENDING
                or review["reviewer_id"] != principal.id
            ):
                raise PolicyViolationError("Review is not assigned to this actor.")
            if review["started_at"] is not None:
                raise InvalidTransitionError("Review has already been started.")
            if not thread_id:
                raise PolicyViolationError("Review start requires a Codex thread id.")
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (review["run_id"],)
            ).fetchone()
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != AssignmentStatus.ACCEPTED
                or assignment["expires_at"] <= _iso()
            ):
                raise LeaseFencedError("Verification lease is no longer active.")
            policy = self._assignment_policy_snapshot(assignment)
            expires_at = _iso(
                _now() + timedelta(seconds=policy["lease_ttl_seconds"])
            )
            connection.execute(
                "UPDATE reviews SET reviewer_thread_id=?, started_at=? WHERE id=?",
                (thread_id, _iso(), review_id),
            )
            if review["reviewer_instance_id"]:
                connection.execute(
                    "UPDATE agent_instances SET status='RUNNING' WHERE id=?",
                    (review["reviewer_instance_id"],),
                )
            connection.execute(
                "UPDATE assignments SET expires_at=? WHERE id=?",
                (expires_at, assignment["id"]),
            )
            connection.execute(
                "UPDATE leases SET expires_at=? WHERE assignment_id=?",
                (expires_at, assignment["id"]),
            )
            connection.execute(
                """
                UPDATE capabilities SET expires_at=?
                WHERE assignment_id=? AND revoked_at IS NULL
                """,
                (expires_at, assignment["id"]),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection,
                "review",
                review_id,
                "REVIEW_STARTED",
                principal,
                {"task_id": task_id, "thread_id": thread_id},
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.VERIFYING,
                data={"review_id": review_id, "expires_at": expires_at},
            )

        return self._mutate(
            operation="review_start",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def review_decide(
        self,
        task_id: str,
        review_id: str,
        decision: ReviewStatus,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
        human_approved: bool = False,
        evidence: list[dict[str, Any]] | None = None,
    ) -> CommandResult:
        evidence = evidence or []
        payload = {
            "task_id": task_id, "review_id": review_id, "decision": decision,
            "reason": reason, "expected_version": expected_version,
            "human_approved": human_approved, "evidence": evidence,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if task["state"] != TaskState.VERIFYING:
                raise InvalidTransitionError("Task is not awaiting review.")
            review = connection.execute(
                "SELECT * FROM reviews WHERE id=? AND task_id=?", (review_id, task_id)
            ).fetchone()
            if not review or review["status"] != ReviewStatus.PENDING:
                raise PolicyViolationError("Review is not pending.")
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (review["run_id"],)
            ).fetchone()
            if run["actor_id"] == principal.id:
                raise PolicyViolationError("A worker cannot review its own run.")
            risk = RiskLevel(task["risk"])
            if review["reviewer_id"] is None:
                raise PolicyViolationError("Review must be claimed before decision.")
            if review["reviewer_id"] != principal.id:
                raise PolicyViolationError("Review is not assigned to this actor.")
            if review["started_at"] is None:
                raise PolicyViolationError("Review must be started before decision.")
            if risk is RiskLevel.HIGH and not principal.has_any_role("reviewer"):
                raise PolicyViolationError("High-risk work needs an independent reviewer.")
            if risk is RiskLevel.CRITICAL and not principal.has_any_role("reviewer"):
                raise PolicyViolationError(
                    "Critical work needs an independent reviewer before human approval."
                )
            if human_approved:
                raise PolicyViolationError(
                    "Human approval must be recorded by a separate authenticated human actor."
                )
            if decision not in (
                ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED
            ):
                raise PolicyViolationError("Review decision must be final.")
            for item in evidence:
                if not item.get("kind") or not item.get("summary"):
                    raise PolicyViolationError(
                        "Review evidence requires kind and summary."
                    )
                payload_data = item.get("payload", {})
                if (
                    decision is ReviewStatus.APPROVED
                    and payload_data.get("passed", True) is not True
                ):
                    raise PolicyViolationError(
                        "An approved review cannot include failed evidence."
                    )
                connection.execute(
                    """
                    INSERT INTO evidence(
                        id, task_id, run_id, kind, summary, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        _id(),
                        task_id,
                        run["id"],
                        f"review:{item['kind']}",
                        item["summary"],
                        _json(payload_data),
                        _iso(),
                    ),
                )
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != AssignmentStatus.ACCEPTED
                or assignment["expires_at"] <= _iso()
            ):
                raise LeaseFencedError("Verification lease has expired.")
            if decision is ReviewStatus.APPROVED:
                evidence_count = connection.execute(
                    "SELECT COUNT(*) AS count FROM evidence WHERE task_id=? AND run_id=?",
                    (task_id, run["id"]),
                ).fetchone()["count"]
                if not evidence_count:
                    raise PolicyViolationError("DONE requires recorded evidence.")
                pending_gate = (
                    self._task_requires_local_integration(task)
                    or risk is RiskLevel.CRITICAL
                )
                state = TaskState.VERIFYING if pending_gate else TaskState.DONE
                event = "REVIEW_APPROVED_PENDING_GATE" if pending_gate else "TASK_DONE"
            else:
                state = TaskState.BACKLOG
                event = "TASK_REWORK_REQUESTED"
            connection.execute(
                """
                UPDATE reviews
                SET status=?, reviewer_id=?, decision_reason=?, human_approved=?, decided_at=?
                WHERE id=?
                """,
                (decision, principal.id, reason, 0, _iso(), review_id),
            )
            if decision is ReviewStatus.CHANGES_REQUESTED or state is TaskState.DONE:
                self._release(connection, assignment["id"])
            else:
                connection.execute(
                    """
                    UPDATE capabilities SET revoked_at=?
                    WHERE assignment_id=? AND actor_id=? AND revoked_at IS NULL
                    """,
                    (_iso(), assignment["id"], principal.id),
                )
            if review["reviewer_instance_id"]:
                self._mark_instance_idle_if_unused(
                    connection,
                    review["reviewer_instance_id"],
                )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state=?, version=?, updated_at=? WHERE id=?",
                (state, version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, event, principal,
                {"review_id": review_id, "reason": reason}, version,
            )
            return CommandResult(task_id, version, state)

        return self._mutate(
            operation="review_decide", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def review_human_approve(
        self,
        task_id: str,
        review_id: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "review_id": review_id,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if not principal.has_any_role("human"):
                raise PolicyViolationError("Critical approval requires an authenticated human.")
            if RiskLevel(task["risk"]) is not RiskLevel.CRITICAL:
                raise PolicyViolationError("Human gate is only required for critical work.")
            review = connection.execute(
                "SELECT * FROM reviews WHERE id=? AND task_id=?", (review_id, task_id)
            ).fetchone()
            if not review or review["status"] != ReviewStatus.APPROVED:
                raise PolicyViolationError("Independent review must approve before the human gate.")
            run = connection.execute(
                "SELECT * FROM runs WHERE id=?", (review["run_id"],)
            ).fetchone()
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != AssignmentStatus.ACCEPTED
                or assignment["expires_at"] <= _iso()
            ):
                raise LeaseFencedError("Verification lease has expired.")
            connection.execute(
                """
                INSERT INTO review_approvals(review_id, actor_id, created_at)
                VALUES (?, ?, ?)
                """,
                (review_id, principal.id, _iso()),
            )
            connection.execute(
                "UPDATE reviews SET human_approved=1 WHERE id=?", (review_id,)
            )
            requires_integration = self._task_requires_local_integration(task)
            state = TaskState.VERIFYING if requires_integration else TaskState.DONE
            if state is TaskState.DONE:
                self._release(connection, assignment["id"])
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state=?, version=?, updated_at=? WHERE id=?",
                (state, version, _iso(), task_id),
            )
            event = "HUMAN_APPROVAL_RECORDED" if requires_integration else "TASK_DONE"
            self._event(
                connection,
                "task",
                task_id,
                event,
                principal,
                {"review_id": review_id},
                version,
            )
            return CommandResult(
                task_id,
                version,
                state,
                data={"review_id": review_id, "human_approved": True},
            )

        return self._mutate(
            operation="review_human_approve",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def git_integrate(
        self,
        task_id: str,
        run_id: str,
        expected_target_sha: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id,
            "run_id": run_id,
            "expected_target_sha": expected_target_sha,
            "expected_version": expected_version,
        }
        intent_path: Path | None = None

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            nonlocal intent_path
            self._require_role(principal, "orchestrator", "human")
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if (
                task["state"] != TaskState.VERIFYING
                or not self._task_requires_local_integration(task)
            ):
                raise InvalidTransitionError("Task is not awaiting local Git integration.")
            run = connection.execute(
                "SELECT * FROM runs WHERE id=? AND task_id=?", (run_id, task_id)
            ).fetchone()
            if not run or not run["branch_ref"] or not run["checkpoint_sha"]:
                raise PolicyViolationError("Run has no validated local checkpoint.")
            review = connection.execute(
                "SELECT * FROM reviews WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                (run_id,),
            ).fetchone()
            if not review or review["status"] != ReviewStatus.APPROVED:
                raise PolicyViolationError("Review must approve before local integration.")
            if RiskLevel(task["risk"]) is RiskLevel.CRITICAL and not review["human_approved"]:
                raise PolicyViolationError("Critical work still needs authenticated human approval.")
            assignment = connection.execute(
                "SELECT * FROM assignments WHERE id=?", (run["assignment_id"],)
            ).fetchone()
            if (
                not assignment
                or assignment["status"] != AssignmentStatus.ACCEPTED
                or assignment["expires_at"] <= _iso()
            ):
                raise LeaseFencedError("Verification lease has expired.")
            adapter = LocalGitAdapter(self._project_root())
            try:
                plan_branch = adapter.ensure_plan_branch(
                    task["plan_id"], run["start_sha"]
                )
                intent_path = self._write_external_intent(
                    "git_integrate_task",
                    idempotency_key,
                    {
                        "task_id": task_id,
                        "run_id": run_id,
                        "target_ref": plan_branch,
                        "expected_sha": expected_target_sha,
                    },
                )
                integrated = adapter.integrate_refs(
                    target_branch=plan_branch,
                    source_ref=run["branch_ref"],
                    expected_target_sha=expected_target_sha,
                    expected_source_sha=run["checkpoint_sha"],
                    message=f"Integrate {task_id}",
                    actor=principal.id,
                    require_clean_worktree=False,
                )
            except GitPolicyError as exc:
                raise PolicyViolationError(str(exc)) from exc
            self._update_external_intent(
                intent_path,
                effect_completed=True,
                result_sha=integrated.commit_sha,
            )
            operation_id = _id()
            now = _iso()
            connection.execute(
                """
                INSERT INTO git_operations(
                    id, task_id, run_id, kind, status, target_sha, result_sha,
                    actor_id, created_at
                ) VALUES (?, ?, ?, 'INTEGRATE_TASK', 'SUCCEEDED', ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    task_id,
                    run_id,
                    integrated.target_before,
                    integrated.commit_sha,
                    principal.id,
                    now,
                ),
            )
            evidence_payload = {
                "operation_id": operation_id,
                "target_before": integrated.target_before,
                "source_sha": integrated.source_sha,
                "commit_sha": integrated.commit_sha,
                "target_ref": integrated.target_ref,
            }
            connection.execute(
                """
                INSERT INTO evidence(
                    id, task_id, run_id, kind, summary, payload_json, created_at
                ) VALUES (?, ?, ?, 'git_integration', ?, ?, ?)
                """,
                (
                    _id(),
                    task_id,
                    run_id,
                    f"Integrated locally as {integrated.commit_sha[:12]}",
                    _json(evidence_payload),
                    now,
                ),
            )
            connection.execute(
                "UPDATE runs SET integration_sha=? WHERE id=?",
                (integrated.commit_sha, run_id),
            )
            self._release(connection, assignment["id"])
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='DONE', version=?, updated_at=? WHERE id=?",
                (version, now, task_id),
            )
            self._event(
                connection,
                "task",
                task_id,
                "TASK_DONE",
                principal,
                evidence_payload,
                version,
            )
            return CommandResult(task_id, version, TaskState.DONE, data=evidence_payload)

        try:
            result = self._mutate(
                operation="git_integrate",
                actor=actor,
                idempotency_key=idempotency_key,
                payload=payload,
                action=action,
            )
        except Exception:
            self._recover_external_intents()
            raise
        if intent_path is not None:
            intent_path.unlink(missing_ok=True)
        return result

    def git_integrate_plan(
        self,
        plan_id: str,
        revision: int,
        target_branch: str,
        expected_target_sha: str,
        expected_source_sha: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "plan_id": plan_id,
            "revision": revision,
            "target_branch": target_branch,
            "expected_target_sha": expected_target_sha,
            "expected_source_sha": expected_source_sha,
            "expected_version": expected_version,
        }
        intent_path: Path | None = None

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            nonlocal intent_path
            self._require_role(principal, "human")
            plan = self._plan_row(connection, plan_id, revision)
            self._version(plan, expected_version)
            if plan["status"] != PlanStatus.ACTIVE:
                raise PolicyViolationError("Only the active plan revision can be integrated.")
            content = json.loads(plan["content_json"])
            approved_target_branch = content.get("target_branch")
            if not isinstance(approved_target_branch, str) or not approved_target_branch:
                raise PolicyViolationError(
                    "The approved plan does not declare a target branch."
                )
            if target_branch != approved_target_branch:
                raise PolicyViolationError(
                    "The requested target branch differs from the approved plan."
                )
            incomplete = connection.execute(
                """
                SELECT id FROM tasks
                WHERE plan_id=? AND plan_revision=? AND state NOT IN ('DONE','CANCELED')
                ORDER BY id
                """,
                (plan_id, revision),
            ).fetchall()
            if incomplete:
                raise PolicyViolationError("All plan tasks must be terminal before integration.")
            adapter = LocalGitAdapter(self._project_root())
            try:
                plan_branch = adapter.ensure_plan_branch(plan_id, expected_target_sha)
                intent_path = self._write_external_intent(
                    "git_integrate_plan",
                    idempotency_key,
                    {
                        "plan_id": plan_id,
                        "revision": revision,
                        "target_ref": target_branch,
                        "expected_sha": expected_target_sha,
                        "expected_source_sha": expected_source_sha,
                    },
                )
                integrated = adapter.integrate_refs(
                    target_branch=target_branch,
                    source_ref=plan_branch,
                    expected_target_sha=expected_target_sha,
                    expected_source_sha=expected_source_sha,
                    message=f"Integrate AgentBoard plan {plan_id} revision {revision}",
                    actor=principal.id,
                    require_clean_worktree=True,
                )
            except GitPolicyError as exc:
                raise PolicyViolationError(str(exc)) from exc
            self._update_external_intent(
                intent_path,
                effect_completed=True,
                result_sha=integrated.commit_sha,
            )
            operation_id = _id()
            now = _iso()
            connection.execute(
                """
                INSERT INTO git_operations(
                    id, kind, status, target_sha, result_sha, actor_id, created_at
                ) VALUES (?, 'INTEGRATE_PLAN', 'SUCCEEDED', ?, ?, ?, ?)
                """,
                (
                    operation_id,
                    integrated.target_before,
                    integrated.commit_sha,
                    principal.id,
                    now,
                ),
            )
            version = plan["version"] + 1
            connection.execute(
                "UPDATE plans SET status='COMPLETED', version=? WHERE id=? AND revision=?",
                (version, plan_id, revision),
            )
            data = {
                "operation_id": operation_id,
                "target_before": integrated.target_before,
                "source_sha": integrated.source_sha,
                "commit_sha": integrated.commit_sha,
                "target_ref": integrated.target_ref,
            }
            self._event(
                connection,
                "plan",
                plan_id,
                "PLAN_COMPLETED",
                principal,
                data,
                version,
            )
            return CommandResult(plan_id, version, PlanStatus.COMPLETED, data=data)

        try:
            result = self._mutate(
                operation="git_integrate_plan",
                actor=actor,
                idempotency_key=idempotency_key,
                payload=payload,
                action=action,
            )
        except Exception:
            self._recover_external_intents()
            raise
        if intent_path is not None:
            intent_path.unlink(missing_ok=True)
        return result

    def dependency_waive(
        self,
        task_id: str,
        depends_on_task_id: str,
        kind: DependencyType,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "depends_on_task_id": depends_on_task_id,
            "kind": kind, "reason": reason, "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if not principal.has_any_role("orchestrator", "reviewer", "human"):
                raise PolicyViolationError("Dependency waiver requires elevated authority.")
            edge = connection.execute(
                """
                SELECT 1 FROM dependencies
                WHERE task_id=? AND depends_on_task_id=? AND kind=?
                """,
                (task_id, depends_on_task_id, kind),
            ).fetchone()
            if not edge:
                raise NotFoundError("The exact dependency edge does not exist.")
            waiver_id = _id()
            connection.execute(
                """
                INSERT INTO dependency_waivers(
                    id, task_id, depends_on_task_id, kind, reason, actor_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    waiver_id, task_id, depends_on_task_id, kind, reason, principal.id, _iso(),
                ),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, "DEPENDENCY_WAIVED", principal,
                {"depends_on_task_id": depends_on_task_id, "kind": kind, "reason": reason},
                version,
            )
            return CommandResult(task_id, version, task["state"], data={"waiver_id": waiver_id})

        return self._mutate(
            operation="dependency_waive", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def task_cancel(
        self,
        task_id: str,
        reason: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "task_id": task_id, "reason": reason, "expected_version": expected_version
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator", "human")
            task = self._task_row(connection, task_id)
            self._version(task, expected_version)
            if task["state"] in (TaskState.DONE, TaskState.CANCELED):
                raise InvalidTransitionError("Completed or canceled tasks cannot be canceled.")
            assignments = connection.execute(
                """
                SELECT id FROM assignments
                WHERE task_id=? AND status IN ('RESERVED','ACCEPTED')
                """,
                (task_id,),
            ).fetchall()
            for assignment in assignments:
                self._release(connection, assignment["id"])
            abandoned_review_ids = self._abandon_pending_reviews(
                connection,
                task_id,
                f"Task canceled: {reason}",
            )
            connection.execute(
                """
                UPDATE runs SET status='CANCELED', finished_at=?
                WHERE task_id=? AND status='RUNNING'
                """,
                (_iso(), task_id),
            )
            version = task["version"] + 1
            connection.execute(
                "UPDATE tasks SET state='CANCELED', version=?, updated_at=? WHERE id=?",
                (version, _iso(), task_id),
            )
            self._event(
                connection, "task", task_id, "TASK_CANCELED", principal,
                {
                    "reason": reason,
                    "abandoned_review_ids": abandoned_review_ids,
                },
                version,
            )
            return CommandResult(
                task_id,
                version,
                TaskState.CANCELED,
                data={"abandoned_review_ids": abandoned_review_ids},
            )

        return self._mutate(
            operation="task_cancel", actor=actor, idempotency_key=idempotency_key,
            payload=payload, action=action,
        )

    def config_draft_create(
        self,
        yaml_text: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        try:
            raw = yaml.safe_load(yaml_text)
        except yaml.YAMLError as exc:
            raise PolicyViolationError(f"Configuration YAML is invalid: {exc}") from exc
        if not isinstance(raw, dict):
            raise PolicyViolationError("Configuration draft must be a YAML mapping.")
        try:
            config = validate_config(raw)
        except ValueError as exc:
            raise PolicyViolationError(str(exc)) from exc
        fingerprint = config_fingerprint(config)
        canonical = config.model_dump(mode="json")
        payload = {
            "content_hash": fingerprint,
            "expected_version": expected_version,
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "orchestrator", "human")
            latest = connection.execute(
                "SELECT version FROM config_revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
            current_version = latest["version"] if latest else 0
            if current_version != expected_version:
                raise VersionConflictError(
                    f"Expected config version {expected_version}, current is {current_version}."
                )
            revision = connection.execute(
                "SELECT COALESCE(MAX(revision),0)+1 AS value FROM config_revisions"
            ).fetchone()["value"]
            draft_id = _id()
            connection.execute(
                """
                INSERT INTO config_revisions(
                    id, revision, status, version, content_hash, content_json,
                    validation_json, actor_id, created_at
                ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?, ?)
                """,
                (
                    draft_id,
                    revision,
                    current_version + 1,
                    fingerprint,
                    _json(canonical),
                    _json({"valid": True}),
                    principal.id,
                    _iso(),
                ),
            )
            self._event(
                connection,
                "config",
                draft_id,
                "CONFIG_DRAFT_CREATED",
                principal,
                {"revision": revision, "content_hash": fingerprint},
                current_version + 1,
            )
            return CommandResult(
                draft_id,
                current_version + 1,
                "DRAFT",
                data={"revision": revision, "content_hash": fingerprint, "valid": True},
            )

        return self._mutate(
            operation="config_draft_create",
            actor=actor,
            idempotency_key=idempotency_key,
            payload=payload,
            action=action,
        )

    def config_validate(self, draft_id: str) -> dict[str, Any]:
        with connect(self._path()) as connection:
            row = connection.execute(
                "SELECT * FROM config_revisions WHERE id=?", (draft_id,)
            ).fetchone()
        if not row:
            raise NotFoundError(f"Configuration draft {draft_id!r} was not found.")
        config = validate_config(json.loads(row["content_json"]))
        return {
            "draft_id": draft_id,
            "valid": True,
            "content_hash": config_fingerprint(config),
            "revision": row["revision"],
            "version": row["version"],
            "content": config.model_dump(mode="json"),
        }

    def config_apply_draft(
        self,
        draft_id: str,
        expected_fingerprint: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> CommandResult:
        payload = {
            "draft_id": draft_id,
            "expected_fingerprint": expected_fingerprint,
            "expected_version": expected_version,
        }
        apply_state: dict[str, Any] = {
            "replaced": False,
            "target": None,
            "backup": None,
            "target_existed": False,
            "old_policy": (
                self.wip_limit,
                dict(self.profile_wip_limits),
                {key: dict(value) for key, value in self.evidence_profiles.items()},
                self.reservation_ttl_seconds,
                self.lease_ttl_seconds,
                self.transient_max_attempts,
            ),
        }

        def action(connection: sqlite3.Connection, principal: Actor) -> CommandResult:
            self._require_role(principal, "human")
            row = connection.execute(
                "SELECT * FROM config_revisions WHERE id=?", (draft_id,)
            ).fetchone()
            if not row:
                raise NotFoundError(f"Configuration draft {draft_id!r} was not found.")
            self._version(row, expected_version)
            if row["status"] != "DRAFT":
                raise PolicyViolationError("Only a validated draft can be applied.")
            if row["content_hash"] != expected_fingerprint:
                raise VersionConflictError("Configuration draft fingerprint changed.")
            config = validate_config(json.loads(row["content_json"]))
            if config_fingerprint(config) != expected_fingerprint:
                raise VersionConflictError("Configuration draft content no longer matches.")

            database = self._path().resolve()
            project_root = (
                database.parent.parent
                if database.parent.name == ".agentboard"
                else database.parent
            )
            runtime_dir = project_root / ".agentboard"
            backups = runtime_dir / "backups"
            runtime_dir.mkdir(parents=True, exist_ok=True)
            backups.mkdir(parents=True, exist_ok=True)
            target = project_root / config.project.config_file
            temporary = runtime_dir / f"config-{draft_id}.tmp"
            apply_state["target"] = target
            apply_state["target_existed"] = target.exists()
            if target.exists():
                backup = backups / f"agentboard.yaml.{uuid.uuid4().hex}.bak"
                shutil.copy2(target, backup)
                apply_state["backup"] = backup
            serialized = yaml.safe_dump(
                config.model_dump(mode="json"),
                sort_keys=False,
                allow_unicode=True,
            )
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                stream.write(serialized)
                stream.flush()
                os.fsync(stream.fileno())
            marker = write_config_apply_marker(
                project_root,
                database_path=database,
                target=target,
                backup=apply_state["backup"],
                target_existed=apply_state["target_existed"],
                draft_id=draft_id,
                content_hash=expected_fingerprint,
            )
            apply_state["marker"] = marker
            os.replace(temporary, target)
            apply_state["replaced"] = True

            connection.execute(
                "UPDATE config_revisions SET status='SUPERSEDED' WHERE status='ACTIVE'"
            )
            version = row["version"] + 1
            connection.execute(
                """
                UPDATE config_revisions
                SET status='ACTIVE', version=?, applied_at=?
                WHERE id=?
                """,
                (version, _iso(), draft_id),
            )
            self._event(
                connection,
                "config",
                draft_id,
                "CONFIG_APPLIED",
                principal,
                {"content_hash": expected_fingerprint, "path": config.project.config_file},
                version,
            )
            # Set effective policy before releasing the SQLite write lock. The next
            # claim therefore observes the same linearization point as CONFIG_APPLIED.
            self.wip_limit = config.limits.project_wip
            self.profile_wip_limits = dict(config.limits.profile_wip)
            self.evidence_profiles = {
                key: value.model_dump(mode="json")
                for key, value in config.evidence_profiles.items()
            }
            self.reservation_ttl_seconds = config.leases.reservation_ttl_seconds
            self.lease_ttl_seconds = config.leases.run_ttl_seconds
            self.transient_max_attempts = config.retries.transient_max_attempts
            return CommandResult(
                draft_id,
                version,
                "ACTIVE",
                data={
                    "content_hash": expected_fingerprint,
                    "path": config.project.config_file,
                },
            )

        try:
            result = self._mutate(
                operation="config_apply_draft",
                actor=actor,
                idempotency_key=idempotency_key,
                payload=payload,
                action=action,
            )
        except Exception:
            if apply_state["replaced"]:
                target = apply_state["target"]
                backup = apply_state["backup"]
                if backup is not None:
                    shutil.copy2(backup, target)
                elif not apply_state["target_existed"]:
                    target.unlink(missing_ok=True)
            marker = apply_state.get("marker")
            if marker is not None:
                marker.unlink(missing_ok=True)
            (
                self.wip_limit,
                self.profile_wip_limits,
                self.evidence_profiles,
                self.reservation_ttl_seconds,
                self.lease_ttl_seconds,
                self.transient_max_attempts,
            ) = apply_state["old_policy"]
            raise
        marker = apply_state.get("marker")
        if marker is not None:
            try:
                marker.unlink(missing_ok=True)
            except OSError:
                # Startup reconciliation sees the committed ACTIVE revision and
                # safely removes a marker left behind by filesystem failure.
                pass
        return result

    def _task_view(self, connection: sqlite3.Connection, task_id: str) -> TaskView:
        row = self._task_row(connection, task_id)
        ready, reasons = self._readiness(connection, task_id)
        assigned = bool(
            connection.execute(
                """
                SELECT 1 FROM assignments
                WHERE task_id=? AND status IN ('RESERVED','ACCEPTED') AND expires_at>?
                """,
                (task_id, _iso()),
            ).fetchone()
        )
        blocked = bool(
            connection.execute(
                "SELECT 1 FROM blockers WHERE task_id=? AND status='OPEN'", (task_id,)
            ).fetchone()
        )
        latest_review = connection.execute(
            """
            SELECT status FROM reviews
            WHERE task_id=?
            ORDER BY COALESCE(decided_at, created_at) DESC LIMIT 1
            """,
            (task_id,),
        ).fetchone()
        rework = bool(
            row["state"] == TaskState.BACKLOG
            and not assigned
            and latest_review
            and latest_review["status"] == ReviewStatus.CHANGES_REQUESTED
        )
        paths = tuple(
            item["path"]
            for item in connection.execute(
                "SELECT path FROM task_paths WHERE task_id=? ORDER BY path", (task_id,)
            )
        )
        task = Task(
            id=row["id"],
            title=row["title"],
            state=TaskState(row["state"]),
            version=row["version"],
            plan_id=row["plan_id"],
            plan_revision=row["plan_revision"],
            objective=row["objective"],
            scope=row["scope"],
            acceptance=tuple(json.loads(row["acceptance_json"])),
            tests=tuple(json.loads(row["tests_json"])),
            priority=Priority(row["priority"]),
            risk=RiskLevel(row["risk"]),
            suggested_profile=row["suggested_profile"],
            paths=paths,
            plan_order=row["plan_order"],
            critical_path=row["critical_path"],
            downstream_count=row["downstream_count"],
            git_required=bool(row["git_required"]),
        )
        return TaskView(task, ready, assigned, blocked, rework, tuple(reasons))

    def task_get(self, task_id: str) -> TaskView:
        with connect(self._path()) as connection:
            connection.execute("BEGIN")
            return self._task_view(connection, task_id)

    def validate_task_move_intent(
        self,
        task_id: str,
        target_column: str,
        *,
        expected_version: int,
        actor: Actor | str,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Validate a board drag without bypassing workflow-specific commands."""

        principal = _actor(actor)
        self._require_role(principal, "human", "orchestrator", "admin")
        if not idempotency_key.strip():
            raise ValueError("idempotency_key is required.")
        valid_columns = {"backlog", "eligible", "in_progress", "verifying", "done"}
        if target_column not in valid_columns:
            raise PolicyViolationError("Unknown board target column.")
        with connect(self._path()) as connection:
            connection.execute("BEGIN")
            view = self._task_view(connection, task_id)
            if view.task.version != expected_version:
                raise VersionConflictError(
                    f"Expected version {expected_version}, "
                    f"current version is {view.task.version}."
                )
            if view.task.state is TaskState.CANCELED:
                source_column = "canceled"
            elif view.task.state is TaskState.DONE:
                source_column = "done"
            elif view.task.state is TaskState.VERIFYING:
                source_column = "verifying"
            elif view.task.state is TaskState.IN_PROGRESS:
                source_column = "in_progress"
            elif view.ready and not view.assigned:
                source_column = "eligible"
            else:
                source_column = "backlog"

        next_actions = {
            ("eligible", "in_progress"): (
                "task_claim",
                "Select an eligible agent and claim the task.",
            ),
            ("in_progress", "verifying"): (
                "task_report_result",
                "The assigned worker must report evidence and its result.",
            ),
            ("verifying", "done"): (
                "review_decide",
                "Review and any required local integration must complete first.",
            ),
        }
        next_action: str | None = None
        if source_column == target_column:
            accepted = True
            message = "Task is already represented in this board column."
        elif (source_column, target_column) in next_actions:
            accepted = True
            next_action, message = next_actions[(source_column, target_column)]
        elif target_column == "eligible":
            accepted = False
            message = "Eligibility is computed from dependencies, blockers, leases and policy."
        else:
            accepted = False
            message = (
                "This drag cannot represent a valid workflow command from the current state."
            )
        return {
            "task_id": task_id,
            "version": view.task.version,
            "source_column": source_column,
            "target_column": target_column,
            "accepted": accepted,
            "mutation_performed": False,
            "required_command": next_action,
            "message": message,
        }

    def task_list(self, plan_id: str | None = None) -> list[TaskView]:
        with connect(self._path()) as connection:
            query = "SELECT id FROM tasks"
            params: tuple[Any, ...] = ()
            if plan_id is not None:
                query += " WHERE plan_id=?"
                params = (plan_id,)
            connection.execute("BEGIN")
            ids = [row["id"] for row in connection.execute(query, params)]
            return [self._task_view(connection, task_id) for task_id in ids]

    def schedule_next(self, limit: int = 20) -> list[TaskView]:
        tasks = [task for task in self.task_list() if task.ready and not task.assigned]
        priority = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        tasks.sort(
            key=lambda view: (
                priority[view.task.priority],
                -view.task.critical_path,
                -view.task.downstream_count,
                view.task.plan_order,
                view.task.id,
            )
        )
        return tasks[:limit]

    def board_snapshot(self) -> dict[str, Any]:
        with connect(self._path()) as connection:
            connection.execute("BEGIN")
            ids = [row["id"] for row in connection.execute("SELECT id FROM tasks ORDER BY id")]
            tasks = [self._task_view(connection, task_id) for task_id in ids]
            last_sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence),0) AS value FROM events"
            ).fetchone()["value"]
            active_wip = connection.execute(
                """
                SELECT COUNT(*) AS count FROM assignments
                WHERE status IN ('RESERVED','ACCEPTED') AND expires_at>?
                """,
                (_iso(),),
            ).fetchone()["count"]
            return {
                "last_sequence": last_sequence,
                "wip": {"active": active_wip, "limit": self.wip_limit},
                "tasks": [
                    {
                        **asdict(view.task),
                        "state": view.task.state,
                        "priority": view.task.priority,
                        "risk": view.task.risk,
                        "ready": view.ready,
                        "assigned": view.assigned,
                        "blocked": view.blocked,
                        "rework": view.rework,
                        "display_state": view.display_state,
                        "reasons": view.reasons,
                    }
                    for view in tasks
                ],
            }

    def task_detail(self, task_id: str) -> dict[str, Any]:
        with connect(self._path()) as connection:
            connection.execute("BEGIN")
            view = self._task_view(connection, task_id)
            dependencies = [
                {
                    **dict(row),
                    "satisfied": row["state"] == TaskState.DONE,
                    "waived": bool(row["waiver_id"]),
                }
                for row in connection.execute(
                    """
                    SELECT d.depends_on_task_id AS task_id, d.kind AS type, t.title, t.state,
                           w.id AS waiver_id
                    FROM dependencies d
                    JOIN tasks t ON t.id=d.depends_on_task_id
                    LEFT JOIN dependency_waivers w
                      ON w.task_id=d.task_id
                     AND w.depends_on_task_id=d.depends_on_task_id
                     AND w.kind=d.kind
                    WHERE d.task_id=?
                    ORDER BY d.depends_on_task_id
                    """,
                    (task_id,),
                )
            ]
            dependents = [
                dict(row)
                for row in connection.execute(
                    """
                    SELECT d.task_id, d.kind AS type, t.title, t.state
                    FROM dependencies d JOIN tasks t ON t.id=d.task_id
                    WHERE d.depends_on_task_id=? ORDER BY d.task_id
                    """,
                    (task_id,),
                )
            ]
            assignment = connection.execute(
                """
                SELECT a.*, l.id AS lease_id, l.status AS lease_status, l.expires_at
                FROM assignments a JOIN leases l ON l.assignment_id=a.id
                WHERE a.task_id=? AND a.status IN ('RESERVED','ACCEPTED')
                ORDER BY a.reserved_at DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            lease = None
            if assignment:
                lease = {
                    "owner": assignment["agent_id"],
                    "generation": assignment["lease_generation"],
                    "status": assignment["lease_status"],
                    "expires_at": assignment["expires_at"],
                    "paths": [
                        row["path"]
                        for row in connection.execute(
                            "SELECT path FROM lease_paths WHERE lease_id=? ORDER BY path",
                            (assignment["lease_id"],),
                        )
                    ],
                }
            run = connection.execute(
                "SELECT * FROM runs WHERE task_id=? ORDER BY attempt DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            run_payload = dict(run) if run else None
            checkpoints: list[dict[str, Any]] = []
            evidence: list[dict[str, Any]] = []
            review_payload = None
            if run:
                checkpoints = [
                    {**dict(row), "payload": json.loads(row["payload_json"])}
                    for row in connection.execute(
                        "SELECT * FROM checkpoints WHERE run_id=? ORDER BY created_at",
                        (run["id"],),
                    )
                ]
                evidence = [
                    {**dict(row), "payload": json.loads(row["payload_json"])}
                    for row in connection.execute(
                        "SELECT * FROM evidence WHERE run_id=? ORDER BY created_at",
                        (run["id"],),
                    )
                ]
                review = connection.execute(
                    "SELECT * FROM reviews WHERE run_id=? ORDER BY created_at DESC LIMIT 1",
                    (run["id"],),
                ).fetchone()
                review_payload = dict(review) if review else None
            blocker = connection.execute(
                """
                SELECT * FROM blockers WHERE task_id=? AND status='OPEN'
                ORDER BY created_at DESC LIMIT 1
                """,
                (task_id,),
            ).fetchone()
            task = asdict(view.task)
            return {
                **task,
                "phase": view.task.state,
                "status": view.display_state,
                "profile": view.task.suggested_profile,
                "acceptance_criteria": list(view.task.acceptance),
                "dependencies": dependencies,
                "dependents": dependents,
                "checkpoints": checkpoints,
                "evidence": evidence,
                "lease": lease,
                "run": run_payload,
                "review": review_payload,
                "blocked_reason": blocker["reason"] if blocker else None,
                "ready": view.ready,
                "rework": view.rework,
                "reasons": view.reasons,
            }

    def plan_list(self) -> list[dict[str, Any]]:
        with connect(self._path()) as connection:
            rows = connection.execute(
                """
                SELECT id, revision, title, status, version, approved_at, created_at
                FROM plans ORDER BY created_at DESC, revision DESC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def plan_validate(self, plan_id: str, revision: int | None = None) -> dict[str, Any]:
        plan = self.plan_get(plan_id, revision)
        self._validate_plan_content(plan["content"])
        impact: dict[str, Any] | None = None
        parent_revision = plan.get("parent_revision")
        if parent_revision is not None:
            parent = self.plan_get(plan_id, parent_revision)
            before = {task["id"]: task for task in parent["content"]["tasks"]}
            after = {task["id"]: task for task in plan["content"]["tasks"]}
            added = sorted(after.keys() - before.keys())
            removed = sorted(before.keys() - after.keys())
            changed = sorted(
                task_id
                for task_id in before.keys() & after.keys()
                if before[task_id] != after[task_id]
            )
            human_sensitive_fields = {
                "objective",
                "risk",
                "suggested_profile",
                "paths",
                "git_required",
            }
            human_sensitive = sorted(
                task_id
                for task_id in changed
                if any(
                    before[task_id].get(field_name)
                    != after[task_id].get(field_name)
                    for field_name in human_sensitive_fields
                )
            )
            impact = {
                "parent_revision": parent_revision,
                "added": added,
                "removed": removed,
                "changed": changed,
                "active_runs_preserved": True,
                "requires_human_approval": bool(
                    added or removed or human_sensitive
                ),
                "human_sensitive_tasks": human_sensitive,
            }
        return {
            "valid": True,
            "plan_id": plan_id,
            "revision": plan["revision"],
            "task_count": len(plan["content"]["tasks"]),
            "graph_metrics": self._plan_graph_metrics(plan["content"]),
            "impact": impact,
        }

    def agent_list(self) -> list[dict[str, Any]]:
        with connect(self._path()) as connection:
            rows = connection.execute(
                """
                SELECT a.*,
                       x.task_id AS current_task_id,
                       x.status AS assignment_status,
                       x.expires_at
                FROM agents a
                LEFT JOIN assignments x ON x.id=(
                    SELECT id FROM assignments
                    WHERE agent_id=a.id AND status IN ('RESERVED','ACCEPTED')
                    ORDER BY reserved_at DESC LIMIT 1
                )
                ORDER BY a.id
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def run_list(
        self,
        *,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if task_id is not None:
            clauses.append("r.task_id=?")
            params.append(task_id)
        if status is not None:
            clauses.append("r.status=?")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(min(max(limit, 1), 1000))
        with connect(self._path()) as connection:
            rows = connection.execute(
                f"""
                SELECT r.*, t.title AS task_title, a.agent_id
                FROM runs r
                JOIN tasks t ON t.id=r.task_id
                JOIN assignments a ON a.id=r.assignment_id
                {where}
                ORDER BY r.started_at DESC LIMIT ?
                """,
                tuple(params),
            ).fetchall()
        return [dict(row) for row in rows]

    def config_get(self) -> dict[str, Any] | None:
        with connect(self._path()) as connection:
            row = connection.execute(
                """
                SELECT * FROM config_revisions
                ORDER BY CASE status WHEN 'ACTIVE' THEN 0 ELSE 1 END, revision DESC
                LIMIT 1
                """
            ).fetchone()
        if not row:
            return None
        return {
            **dict(row),
            "content": json.loads(row["content_json"]),
            "validation": json.loads(row["validation_json"]),
        }

    def config_state(self) -> dict[str, dict[str, Any] | None]:
        def project(row: sqlite3.Row | None) -> dict[str, Any] | None:
            if row is None:
                return None
            return {
                **dict(row),
                "content": json.loads(row["content_json"]),
                "validation": json.loads(row["validation_json"]),
            }

        with connect(self._path()) as connection:
            active = connection.execute(
                """
                SELECT * FROM config_revisions
                WHERE status='ACTIVE'
                ORDER BY revision DESC
                LIMIT 1
                """
            ).fetchone()
            latest = connection.execute(
                "SELECT * FROM config_revisions ORDER BY revision DESC LIMIT 1"
            ).fetchone()
        return {"active": project(active), "latest": project(latest)}

    def event_list(self, after_sequence: int = 0, limit: int = 200) -> list[dict[str, Any]]:
        with connect(self._path()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM events WHERE sequence>? ORDER BY sequence LIMIT ?
                """,
                (after_sequence, min(limit, 1000)),
            ).fetchall()
        return [
            {
                **dict(row),
                "payload": json.loads(row["payload_json"]),
            }
            for row in rows
        ]

    def run_get(self, run_id: str) -> dict[str, Any]:
        with connect(self._path()) as connection:
            row = connection.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise NotFoundError(f"Run {run_id!r} was not found.")
            evidence = [
                {**dict(item), "payload": json.loads(item["payload_json"])}
                for item in connection.execute(
                    "SELECT * FROM evidence WHERE run_id=? ORDER BY created_at", (run_id,)
                )
            ]
        return {**dict(row), "evidence": evidence}

    def plan_get(self, plan_id: str, revision: int | None = None) -> dict[str, Any]:
        with connect(self._path()) as connection:
            if revision is None:
                row = connection.execute(
                    "SELECT * FROM plans WHERE id=? ORDER BY revision DESC LIMIT 1", (plan_id,)
                ).fetchone()
            else:
                row = self._plan_row(connection, plan_id, revision)
            if not row:
                raise NotFoundError(f"Plan {plan_id!r} was not found.")
        return {**dict(row), "content": json.loads(row["content_json"])}
