from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator


class ConfigurationError(ValueError):
    """Raised when declarative project policy cannot be loaded safely."""


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProjectPolicy(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    config_file: Literal["agentboard.yaml"] = "agentboard.yaml"
    default_target_branch: str = "main"


class RuntimePolicy(StrictModel):
    bind_host: Literal["127.0.0.1"] = "127.0.0.1"
    idle_shutdown_seconds: int = Field(default=30, ge=0, le=3600)
    state_directory: str = ".agentboard"

    @field_validator("state_directory")
    @classmethod
    def state_directory_must_be_project_relative(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("runtime.state_directory must stay inside the project")
        return value


class WorkflowPolicy(StrictModel):
    task_phases: tuple[
        Literal["BACKLOG", "IN_PROGRESS", "VERIFYING", "DONE", "CANCELED"], ...
    ] = ("BACKLOG", "IN_PROGRESS", "VERIFYING", "DONE", "CANCELED")
    dependency_types: tuple[Literal["REQUIRES", "ORDER_AFTER"], ...] = (
        "REQUIRES",
        "ORDER_AFTER",
    )
    require_human_plan_approval: Literal[True] = True
    canceled_dependency_requires_edge_waiver: Literal[True] = True

    @model_validator(mode="after")
    def canonical_phases_only(self) -> WorkflowPolicy:
        expected = {"BACKLOG", "IN_PROGRESS", "VERIFYING", "DONE", "CANCELED"}
        if set(self.task_phases) != expected or len(self.task_phases) != len(expected):
            raise ValueError(f"workflow.task_phases must contain exactly {sorted(expected)}")
        if set(self.dependency_types) != {"REQUIRES", "ORDER_AFTER"}:
            raise ValueError("workflow.dependency_types must contain REQUIRES and ORDER_AFTER")
        return self


class LimitsPolicy(StrictModel):
    project_wip: int = Field(default=3, ge=1, le=64)
    profile_wip: dict[str, int] = Field(default_factory=dict)
    reservations_consume_wip: Literal[True] = True
    verification_consumes_wip: Literal[True] = True

    @field_validator("profile_wip")
    @classmethod
    def profile_limits_are_positive(cls, value: dict[str, int]) -> dict[str, int]:
        if any(limit < 1 or limit > 64 for limit in value.values()):
            raise ValueError("limits.profile_wip values must be between 1 and 64")
        return value


class LeasePolicy(StrictModel):
    reservation_ttl_seconds: int = Field(default=120, ge=15, le=3600)
    run_ttl_seconds: int = Field(default=1800, ge=30, le=86400)
    heartbeat_interval_seconds: int = Field(default=60, ge=5, le=3600)
    file_paths_exclusive: Literal[True] = True
    generation_fencing: Literal[True] = True

    @model_validator(mode="after")
    def heartbeat_precedes_expiry(self) -> LeasePolicy:
        if self.heartbeat_interval_seconds >= self.run_ttl_seconds:
            raise ValueError("leases.heartbeat_interval_seconds must be shorter than run TTL")
        return self


class SchedulerPolicy(StrictModel):
    order: tuple[
        Literal[
            "priority",
            "critical_path",
            "downstream_unblocked",
            "plan_order",
            "task_id",
        ],
        ...,
    ] = (
        "priority",
        "critical_path",
        "downstream_unblocked",
        "plan_order",
        "task_id",
    )
    priorities: tuple[Literal["P0", "P1", "P2", "P3"], ...] = ("P0", "P1", "P2", "P3")

    @field_validator("order")
    @classmethod
    def scheduler_order_is_deterministic(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        required = {
            "priority",
            "critical_path",
            "downstream_unblocked",
            "plan_order",
            "task_id",
        }
        if set(value) != required or len(value) != len(required):
            raise ValueError(f"scheduler.order must contain exactly {sorted(required)}")
        return value


class ProfilesPolicy(StrictModel):
    source: str = ".codex/agents"
    orchestrator: str = "agentboard_orchestrator"
    worker: str = "agentboard_worker"
    reviewer: str = "agentboard_reviewer"


class ReviewTier(StrictModel):
    reviewer: Literal["orchestrator", "independent"]
    human_approval: bool = False


class ReviewPolicy(StrictModel):
    tiers: dict[Literal["low", "medium", "high", "critical"], ReviewTier]
    forbid_self_review: Literal[True] = True
    forbid_risk_downgrade_during_run: Literal[True] = True

    @model_validator(mode="after")
    def risk_tiers_are_complete(self) -> ReviewPolicy:
        required = {"low", "medium", "high", "critical"}
        if set(self.tiers) != required:
            raise ValueError(f"review.tiers must contain exactly {sorted(required)}")
        if self.tiers["high"].reviewer != "independent":
            raise ValueError("high-risk work requires an independent reviewer")
        critical = self.tiers["critical"]
        if critical.reviewer != "independent" or not critical.human_approval:
            raise ValueError("critical work requires independent review and human approval")
        return self


class EvidenceProfilePolicy(StrictModel):
    require_changed_files: bool = True
    require_tests: bool = True
    require_acceptance_evidence: bool = True
    require_local_checkpoint: bool = False
    require_local_integration: bool = False


class RetryPolicy(StrictModel):
    transient_max_attempts: int = Field(default=2, ge=0, le=10)
    automatic_categories: tuple[str, ...] = ()
    decision_required_categories: tuple[str, ...] = ()

    @model_validator(mode="after")
    def categories_do_not_overlap(self) -> RetryPolicy:
        overlap = set(self.automatic_categories).intersection(self.decision_required_categories)
        if overlap:
            raise ValueError(f"retry categories overlap: {sorted(overlap)}")
        return self


class GitPolicy(StrictModel):
    enabled: bool = True
    local_only: Literal[True] = True
    integration_branch_prefix: str = "agentboard/plan/"
    worktree_directory: str = ".agentboard/worktrees"
    checkpoint_commits: bool = True
    disable_hooks: Literal[True] = True
    forbid_remote_commands: tuple[str, ...] = ("push", "pull", "fetch", "clone", "ls-remote")
    forbid_commands: tuple[str, ...] = ("reset", "stash")
    forbid_tools: tuple[str, ...] = ("gh",)
    reject_external_filters: Literal[True] = True
    reject_lfs: Literal[True] = True
    reject_submodules: Literal[True] = True
    require_clean_target: Literal[True] = True
    require_expected_target_sha: Literal[True] = True
    require_expected_source_sha: Literal[True] = True
    require_approved_target_branch: Literal[True] = True
    require_path_lease: Literal[True] = True

    @field_validator("worktree_directory")
    @classmethod
    def worktrees_stay_in_runtime_directory(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts or path.parts[:1] != (".agentboard",):
            raise ValueError("git.worktree_directory must stay below .agentboard")
        return value

    @model_validator(mode="after")
    def remote_git_is_completely_disabled(self) -> GitPolicy:
        required = {"push", "pull", "fetch", "clone", "ls-remote"}
        if not required.issubset(self.forbid_remote_commands):
            raise ValueError(f"git.forbid_remote_commands must include {sorted(required)}")
        if not {"reset", "stash"}.issubset(self.forbid_commands):
            raise ValueError("git.forbid_commands must include reset and stash")
        if "gh" not in self.forbid_tools:
            raise ValueError("git.forbid_tools must include gh")
        return self


class PathsPolicy(StrictModel):
    database: str = ".agentboard/agentboard.db"
    backups: str = ".agentboard/backups"
    runtime_metadata: str = ".agentboard/runtime.json"
    runtime_lock: str = ".agentboard/runtime.lock"
    plan_source: str = "docs/DEVELOPMENT_PLAN.md"

    @field_validator("*")
    @classmethod
    def paths_stay_in_project(cls, value: str) -> str:
        path = Path(value)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("configured paths must stay inside the project")
        return value


class CompatibilityPolicy(StrictModel):
    legacy_state_projection: dict[
        Literal["READY", "ASSIGNED", "BLOCKED", "REWORK"], str
    ]


class OrchestrationConfig(StrictModel):
    schema_version: Literal[1] = 1
    project: ProjectPolicy
    runtime: RuntimePolicy = RuntimePolicy()
    workflow: WorkflowPolicy = WorkflowPolicy()
    limits: LimitsPolicy = LimitsPolicy()
    leases: LeasePolicy = LeasePolicy()
    scheduler: SchedulerPolicy = SchedulerPolicy()
    profiles: ProfilesPolicy = ProfilesPolicy()
    review: ReviewPolicy
    evidence_profiles: dict[str, EvidenceProfilePolicy] = Field(default_factory=dict)
    retries: RetryPolicy = RetryPolicy()
    git: GitPolicy = GitPolicy()
    paths: PathsPolicy = PathsPolicy()
    compatibility: CompatibilityPolicy

    @model_validator(mode="after")
    def required_evidence_profile_exists(self) -> OrchestrationConfig:
        if "default" not in self.evidence_profiles:
            raise ValueError("evidence_profiles.default is required")
        return self


def validate_config(payload: dict[str, Any]) -> OrchestrationConfig:
    """Validate a configuration draft without changing project state."""
    try:
        return OrchestrationConfig.model_validate(payload)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc


def load_config(path: Path) -> OrchestrationConfig:
    """Load the Git-versioned policy file using safe YAML parsing."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ConfigurationError(f"Cannot read configuration: {path}") from exc
    except yaml.YAMLError as exc:
        raise ConfigurationError(f"Invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigurationError("Configuration root must be a mapping")
    return validate_config(raw)


def config_fingerprint(config: OrchestrationConfig) -> str:
    canonical = json.dumps(
        config.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def default_config(project_name: str) -> OrchestrationConfig:
    return OrchestrationConfig(
        project=ProjectPolicy(name=project_name),
        review=ReviewPolicy(
            tiers={
                "low": ReviewTier(reviewer="orchestrator"),
                "medium": ReviewTier(reviewer="orchestrator"),
                "high": ReviewTier(reviewer="independent"),
                "critical": ReviewTier(reviewer="independent", human_approval=True),
            }
        ),
        evidence_profiles={"default": EvidenceProfilePolicy()},
        compatibility=CompatibilityPolicy(
            legacy_state_projection={
                "READY": "computed_eligible",
                "ASSIGNED": "active_assignment",
                "BLOCKED": "open_blocker",
                "REWORK": "changes_requested",
            }
        ),
    )


def find_project_root(start: Path) -> Path:
    """Find an AgentBoard/Git project without invoking network-capable Git commands."""
    current = start.resolve()
    if current.is_file():
        current = current.parent
    for candidate in (current, *current.parents):
        if (candidate / "agentboard.yaml").is_file() or (candidate / ".git").exists():
            return candidate
    return current


def write_config_apply_marker(
    project_root: Path,
    *,
    database_path: Path,
    target: Path,
    backup: Path | None,
    target_existed: bool,
    draft_id: str,
    content_hash: str,
) -> Path:
    runtime_dir = project_root / ".agentboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    marker = runtime_dir / "config-apply-intent.json"
    temporary = marker.with_suffix(".tmp")
    payload = {
        "schema_version": 1,
        "database": str(database_path.resolve().relative_to(project_root.resolve())),
        "target": str(target.resolve().relative_to(project_root.resolve())),
        "backup": (
            str(backup.resolve().relative_to(project_root.resolve()))
            if backup is not None
            else None
        ),
        "target_existed": target_existed,
        "draft_id": draft_id,
        "content_hash": content_hash,
    }
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(payload, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker)
    return marker


def recover_config_apply(project_root: Path) -> None:
    """Reconcile a crash between replacing YAML and committing its SQLite revision."""
    root = project_root.resolve()
    marker = root / ".agentboard" / "config-apply-intent.json"
    if not marker.is_file():
        return
    payload = json.loads(marker.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1:
        raise ConfigurationError("Unsupported configuration recovery marker")

    def local_path(value: str) -> Path:
        candidate = (root / value).resolve()
        if candidate != root and root not in candidate.parents:
            raise ConfigurationError("Configuration recovery path escaped the project")
        return candidate

    database = local_path(str(payload["database"]))
    target = local_path(str(payload["target"]))
    backup = (
        local_path(str(payload["backup"]))
        if payload.get("backup") is not None
        else None
    )
    active = False
    if database.is_file():
        with sqlite3.connect(database) as connection:
            exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type='table' AND name='config_revisions'
                """
            ).fetchone()
            if exists:
                active = (
                    connection.execute(
                        """
                        SELECT 1 FROM config_revisions
                        WHERE id=? AND status='ACTIVE' AND content_hash=?
                        """,
                        (payload["draft_id"], payload["content_hash"]),
                    ).fetchone()
                    is not None
                )
    if not active:
        if backup is not None and backup.is_file():
            shutil.copy2(backup, target)
        elif not payload.get("target_existed", False):
            target.unlink(missing_ok=True)
    marker.unlink(missing_ok=True)
