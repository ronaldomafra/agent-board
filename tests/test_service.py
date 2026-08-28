import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml

from agentboard.config import default_config
from agentboard.domain import (
    Actor,
    DependencyType,
    IdempotencyConflictError,
    InvalidTransitionError,
    LeaseFencedError,
    PolicyViolationError,
    ReviewStatus,
    VersionConflictError,
)
from agentboard.service import BoardService
from agentboard.storage import connect

HUMAN = Actor("human", frozenset({"human"}))
ORCHESTRATOR = Actor("orchestrator", frozenset({"orchestrator"}))
WORKER_1 = Actor("worker-1", frozenset({"worker"}))
WORKER_2 = Actor("worker-2", frozenset({"worker"}))
REVIEWER_1 = Actor("reviewer-1", frozenset({"reviewer"}))


def worker_actor(worker_id: str) -> Actor:
    return WORKER_1 if worker_id == "worker-1" else WORKER_2


def plan(tasks: list[dict]) -> dict:
    return {"tasks": tasks}


def task(
    task_id: str,
    *,
    paths: list[str] | None = None,
    dependencies: list[dict] | None = None,
    risk: str = "MEDIUM",
    git_required: bool = False,
    priority: str = "P2",
    critical_path: int = 0,
) -> dict:
    return {
        "id": task_id,
        "title": task_id,
        "objective": f"Complete {task_id}",
        "acceptance": ["evidence recorded"],
        "tests": ["pytest"],
        "priority": priority,
        "risk": risk,
        "suggested_profile": "worker",
        "evidence_profile": "code_with_git" if git_required else "default",
        "paths": paths or [],
        "dependencies": dependencies or [],
        "critical_path": critical_path,
    }


def approved_service(
    tmp_path: Path,
    tasks: list[dict],
    *,
    wip_limit: int = 3,
    profile_wip_limits: dict[str, int] | None = None,
    evidence_profiles: dict[str, dict[str, object]] | None = None,
    ttl: int = 300,
) -> BoardService:
    extra: dict[str, object] = {}
    if evidence_profiles is not None:
        extra["evidence_profiles"] = evidence_profiles
    service = BoardService(
        tmp_path / "state.db",
        wip_limit=wip_limit,
        profile_wip_limits=profile_wip_limits or {},
        reservation_ttl_seconds=ttl,
        lease_ttl_seconds=ttl,
        **extra,
    )
    service.create_plan_draft(
        "PLAN-1",
        "Plan",
        plan(tasks),
        expected_version=0,
        actor=HUMAN,
        idempotency_key="draft",
    )
    service.approve_plan(
        "PLAN-1",
        1,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="approve",
    )
    service.register_agent(
        "worker-1",
        "worker",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="agent-1",
    )
    service.register_agent(
        "worker-2",
        "worker",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="agent-2",
    )
    return service


def start(service: BoardService, task_id: str, worker: str, key: str = "") -> tuple[str, int, int]:
    claimed = service.claim_task(
        task_id,
        worker,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key=f"claim-{task_id}-{key}",
    )
    started = service.run_start(
        task_id,
        claimed.data["assignment_id"],
        claimed.data["lease_generation"],
        expected_version=claimed.version,
        actor=worker_actor(worker),
        idempotency_key=f"start-{task_id}-{key}",
    )
    return started.data["run_id"], claimed.data["lease_generation"], started.version


def test_board_move_intent_is_server_validated_without_mutating_state(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-DRAG")])

    intent = service.validate_task_move_intent(
        "AB-DRAG",
        "in_progress",
        expected_version=0,
        actor=HUMAN,
        idempotency_key="drag-intent-ready",
    )
    assert intent == {
        "task_id": "AB-DRAG",
        "version": 0,
        "source_column": "eligible",
        "target_column": "in_progress",
        "accepted": True,
        "mutation_performed": False,
        "required_command": "task_claim",
        "message": "Select an eligible agent and claim the task.",
    }
    assert service.task_get("AB-DRAG").task.state.value == "BACKLOG"
    assert service.task_get("AB-DRAG").task.version == 0

    with pytest.raises(VersionConflictError, match="current version is 0"):
        service.validate_task_move_intent(
            "AB-DRAG",
            "in_progress",
            expected_version=1,
            actor=HUMAN,
            idempotency_key="drag-intent-stale",
        )
    with pytest.raises(PolicyViolationError, match="requires one"):
        service.validate_task_move_intent(
            "AB-DRAG",
            "in_progress",
            expected_version=0,
            actor=WORKER_1,
            idempotency_key="drag-intent-worker",
        )


def start_review(
    service: BoardService,
    task_id: str,
    review_id: str,
    expected_version: int,
    *,
    reviewer: Actor = ORCHESTRATOR,
    key: str,
) -> int:
    claimed = service.review_claim(
        task_id,
        review_id,
        reviewer.id,
        expected_version=expected_version,
        actor=ORCHESTRATOR,
        idempotency_key=f"{key}-claim",
    )
    started = service.review_start(
        task_id,
        review_id,
        expected_version=claimed.version,
        actor=reviewer,
        idempotency_key=f"{key}-start",
        thread_id=f"thread-{key}",
    )
    return started.version


def test_draft_is_immutable_after_approval_and_tasks_materialize(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])

    assert service.plan_get("PLAN-1")["status"] == "ACTIVE"
    assert service.task_get("AB-1").display_state == "READY"
    with pytest.raises(PolicyViolationError, match="immutable"):
        service.update_plan_draft(
            "PLAN-1",
            1,
            plan([task("AB-1")]),
            expected_version=1,
            actor=HUMAN,
            idempotency_key="late-update",
        )


def test_plan_groups_are_validated_and_materialized_with_tasks(tmp_path: Path) -> None:
    service = BoardService(tmp_path / "state.db")
    grouped_task = task("AB-GROUPED")
    grouped_task["group_id"] = "feature-core"
    content = {
        "groups": [
            {
                "id": "feature-core",
                "kind": "feature",
                "title": "Core",
                "position": 1,
            }
        ],
        "tasks": [grouped_task],
    }

    service.create_plan_draft(
        "PLAN-GROUPS",
        "Grouped plan",
        content,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="grouped-draft",
    )
    service.approve_plan(
        "PLAN-GROUPS",
        1,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="grouped-approve",
    )

    with connect(tmp_path / "state.db") as connection:
        group = connection.execute(
            "SELECT * FROM plan_groups WHERE plan_id='PLAN-GROUPS'"
        ).fetchone()
        stored_task = connection.execute(
            "SELECT group_id FROM tasks WHERE id='AB-GROUPED'"
        ).fetchone()
    assert group["kind"] == "feature"
    assert group["position"] == 1
    assert stored_task["group_id"] == group["id"]

    invalid = {
        "groups": [
            {
                "id": "bad",
                "kind": "feature",
                "title": "Invalid",
                "position": -1,
            }
        ],
        "tasks": [task("AB-BAD")],
    }
    with pytest.raises(PolicyViolationError, match="position"):
        service.create_plan_draft(
            "PLAN-BAD-GROUP",
            "Invalid group",
            invalid,
            expected_version=0,
            actor=HUMAN,
            idempotency_key="invalid-group-draft",
        )


def test_idempotent_replay_and_payload_conflict(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])

    first = service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="same",
    )
    replay = service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="same",
    )

    assert replay.replayed is True
    assert replay.data == first.data
    with pytest.raises(IdempotencyConflictError):
        service.claim_task(
            "AB-1",
            "worker-2",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="same",
        )


def test_stale_version_and_wip_are_atomic_under_concurrent_claim(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path, [task("AB-1"), task("AB-2")], wip_limit=1
    )

    def claim(task_id: str, worker: str) -> str:
        try:
            return service.claim_task(
                task_id,
                worker,
                expected_version=0,
                actor=ORCHESTRATOR,
                idempotency_key=f"parallel-{task_id}",
            ).entity_id
        except PolicyViolationError:
            return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda pair: claim(*pair), [("AB-1", "worker-1"), ("AB-2", "worker-2")]))

    assert results.count("rejected") == 1
    assert service.board_snapshot()["wip"]["active"] == 1


def test_dependency_requires_done_or_exact_waiver(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path,
        [
            task("AB-1"),
            task(
                "AB-2",
                dependencies=[{"task_id": "AB-1", "kind": "REQUIRES"}],
            ),
        ],
    )

    assert service.task_get("AB-2").ready is False
    with pytest.raises(PolicyViolationError, match="not eligible"):
        service.claim_task(
            "AB-2",
            "worker-1",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="blocked-claim",
        )
    waived = service.dependency_waive(
        "AB-2",
        "AB-1",
        DependencyType.REQUIRES,
        "Approved exception",
        expected_version=0,
        actor=REVIEWER_1,
        idempotency_key="waiver",
    )
    assert waived.version == 1
    assert service.task_get("AB-2").ready is True


def test_path_overlap_and_agent_capacity_are_enforced(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path,
        [task("AB-1", paths=["src/core"]), task("AB-2", paths=["src/core/api.py"])],
    )
    service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="lease-core",
    )

    with pytest.raises(PolicyViolationError, match="conflicts"):
        service.claim_task(
            "AB-2",
            "worker-2",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="lease-child",
        )


def test_fencing_evidence_review_and_rework_flow(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1")

    with pytest.raises(LeaseFencedError):
        service.task_heartbeat(
            "AB-1",
            run_id,
            generation + 1,
            expected_version=version,
            actor=WORKER_1,
            idempotency_key="bad-heartbeat",
        )
    heartbeat = service.task_heartbeat(
        "AB-1",
        run_id,
        generation,
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="heartbeat",
        checkpoint={"kind": "tests", "summary": "unit suite green"},
    )
    with pytest.raises(PolicyViolationError, match="required"):
        service.task_report_result(
            "AB-1",
            run_id,
            generation,
            [],
            expected_version=heartbeat.version,
            actor=WORKER_1,
            idempotency_key="empty-result",
        )
    result = service.task_report_result(
        "AB-1",
        run_id,
        generation,
        [{"kind": "tests", "summary": "pytest passed"}],
        expected_version=heartbeat.version,
        actor=WORKER_1,
        idempotency_key="result",
    )
    with pytest.raises(PolicyViolationError, match="own"):
        service.review_decide(
            "AB-1",
            result.data["review_id"],
            ReviewStatus.APPROVED,
            "looks good",
            expected_version=result.version,
            actor=WORKER_1,
            idempotency_key="self-review",
        )
    review_version = start_review(
        service,
        "AB-1",
        result.data["review_id"],
        result.version,
        key="rework-review",
    )
    rework = service.review_decide(
        "AB-1",
        result.data["review_id"],
        ReviewStatus.CHANGES_REQUESTED,
        "cover edge case",
        expected_version=review_version,
        actor=ORCHESTRATOR,
        idempotency_key="rework",
    )
    assert rework.state == "BACKLOG"
    assert service.task_get("AB-1").display_state == "REWORK"
    assert service.board_snapshot()["wip"]["active"] == 0


def test_blocking_releases_lease_and_resolution_restores_readiness(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1")

    blocked = service.task_block(
        "AB-1",
        run_id,
        generation,
        "Waiting for a local fixture",
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="block",
    )

    assert service.task_get("AB-1").display_state == "BLOCKED"
    assert service.board_snapshot()["wip"]["active"] == 0
    resolved = service.blocker_resolve(
        "AB-1",
        blocked.data["blocker_id"],
        "Fixture added",
        expected_version=blocked.version,
        actor=ORCHESTRATOR,
        idempotency_key="resolve",
    )
    assert resolved.version == blocked.version + 1
    assert service.task_get("AB-1").display_state == "READY"


def test_git_required_task_rejects_project_without_local_repository(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1", git_required=True)])

    with pytest.raises(PolicyViolationError, match="not a Git worktree"):
        service.claim_task(
            "AB-1",
            "worker-1",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="claim-git",
        )


def test_approved_review_releases_wip_and_records_done(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1")
    result = service.task_report_result(
        "AB-1",
        run_id,
        generation,
        [{"kind": "tests", "summary": "all tests passed"}],
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="done-result",
    )

    review_version = start_review(
        service,
        "AB-1",
        result.data["review_id"],
        result.version,
        key="done-review-lifecycle",
    )
    approved = service.review_decide(
        "AB-1",
        result.data["review_id"],
        ReviewStatus.APPROVED,
        "acceptance met",
        expected_version=review_version,
        actor=ORCHESTRATOR,
        idempotency_key="done-review",
    )

    assert approved.state == "DONE"
    assert service.task_get("AB-1").display_state == "DONE"
    assert service.board_snapshot()["wip"]["active"] == 0
    sequences = [event["sequence"] for event in service.event_list()]
    assert sequences == list(range(1, len(sequences) + 1))


def test_run_usage_is_persisted_and_completion_duration_ends_at_done(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-USAGE")])
    run_id, generation, version = start(service, "AB-USAGE", "worker-1")
    with connect(tmp_path / "state.db") as connection:
        connection.execute(
            "UPDATE runs SET started_at='2000-01-01T00:00:00+00:00' WHERE id=?",
            (run_id,),
        )

    result = service.task_report_result(
        "AB-USAGE",
        run_id,
        generation,
        [{"kind": "tests", "summary": "usage contract passed"}],
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="usage-result",
        usage={"input_tokens": 120, "output_tokens": 45},
    )
    before_done = service.run_get(run_id)
    assert before_done["input_tokens"] == 120
    assert before_done["output_tokens"] == 45
    assert before_done["total_tokens"] == 165
    assert before_done["duration_seconds"] is None

    review_version = start_review(
        service,
        "AB-USAGE",
        result.data["review_id"],
        result.version,
        key="usage-review",
    )
    service.review_decide(
        "AB-USAGE",
        result.data["review_id"],
        ReviewStatus.APPROVED,
        "usage accepted",
        expected_version=review_version,
        actor=ORCHESTRATOR,
        idempotency_key="usage-done",
    )

    completed = service.run_get(run_id)
    assert completed["completed_at"] is not None
    assert completed["duration_seconds"] is not None
    assert completed["duration_seconds"] > 0


def test_terminal_run_without_usage_keeps_metrics_unreported(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-NO-USAGE")])
    run_id, generation, version = start(service, "AB-NO-USAGE", "worker-1")

    service.task_block(
        "AB-NO-USAGE",
        run_id,
        generation,
        "Waiting for input",
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="no-usage-block",
    )

    run = service.run_get(run_id)
    assert run["input_tokens"] is None
    assert run["output_tokens"] is None
    assert run["total_tokens"] is None
    assert run["duration_seconds"] is None


def test_scheduler_order_is_deterministic(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path,
        [
            task(
                "AB-3",
                priority="P1",
                dependencies=[
                    {"task_id": "AB-1", "kind": DependencyType.REQUIRES}
                ],
            ),
            task("AB-2", priority="P0"),
            task("AB-1", priority="P0"),
        ],
    )

    assert [view.task.id for view in service.schedule_next()] == ["AB-1", "AB-2"]


def test_version_conflict_rolls_back_without_event(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    claimed = service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="claim",
    )
    before = len(service.event_list())

    with pytest.raises(VersionConflictError):
        service.run_start(
            "AB-1",
            claimed.data["assignment_id"],
            claimed.data["lease_generation"],
            expected_version=0,
            actor=WORKER_1,
            idempotency_key="stale-start",
        )

    assert len(service.event_list()) == before


def test_profile_wip_limit_is_enforced_atomically(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path,
        [task("AB-1"), task("AB-2")],
        profile_wip_limits={"worker": 1},
    )
    service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="profile-claim-1",
    )

    with pytest.raises(PolicyViolationError, match="Profile WIP"):
        service.claim_task(
            "AB-2",
            "worker-2",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="profile-claim-2",
        )


def test_spawn_failure_releases_unaccepted_reservation_immediately(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    claimed = service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="claim-before-spawn-failure",
    )

    released = service.task_release_reservation(
        "AB-1",
        claimed.data["assignment_id"],
        "Native Codex spawn failed",
        expected_version=claimed.version,
        actor=ORCHESTRATOR,
        idempotency_key="release-after-spawn-failure",
    )

    assert released.data["released"] is True
    assert service.task_get("AB-1").display_state == "READY"
    assert service.board_snapshot()["wip"]["active"] == 0


def test_only_transient_failures_retry_automatically_with_attempt_limit(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1", "attempt-1")
    first = service.run_fail(
        "AB-1",
        run_id,
        generation,
        "TRANSIENT",
        "Runtime process exited before producing a result",
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="fail-transient-1",
    )
    assert first.data["automatic_retry"] is True
    assert service.task_get("AB-1").display_state == "READY"

    claimed = service.claim_task(
        "AB-1",
        "worker-1",
        expected_version=first.version,
        actor=ORCHESTRATOR,
        idempotency_key="claim-attempt-2",
    )
    started = service.run_start(
        "AB-1",
        claimed.data["assignment_id"],
        claimed.data["lease_generation"],
        expected_version=claimed.version,
        actor=WORKER_1,
        idempotency_key="start-attempt-2",
    )
    exhausted = service.run_fail(
        "AB-1",
        started.data["run_id"],
        claimed.data["lease_generation"],
        "TRANSIENT",
        "Runtime process exited again",
        expected_version=started.version,
        actor=WORKER_1,
        idempotency_key="fail-transient-2",
    )
    assert exhausted.data["automatic_retry"] is False
    assert exhausted.data["blocker_id"]
    assert service.task_get("AB-1").display_state == "BLOCKED"


def test_logical_failure_requires_explicit_decision(tmp_path: Path) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1")

    failed = service.run_fail(
        "AB-1",
        run_id,
        generation,
        "LOGICAL",
        "Acceptance condition cannot be met",
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="fail-logical",
    )

    assert failed.data["automatic_retry"] is False
    assert service.task_get("AB-1").display_state == "BLOCKED"


def test_order_after_prioritizes_upstream_without_blocking_downstream(
    tmp_path: Path,
) -> None:
    service = approved_service(
        tmp_path,
        [
            task("AB-1"),
            task(
                "AB-2",
                dependencies=[
                    {"task_id": "AB-1", "kind": DependencyType.ORDER_AFTER}
                ],
            ),
        ],
    )

    assert [view.task.id for view in service.schedule_next()] == ["AB-1", "AB-2"]
    assert service.task_get("AB-2").display_state == "READY"
    claimed = service.claim_task(
        "AB-2",
        "worker-2",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="claim-soft-order-after",
    )
    assert claimed.state == "ASSIGNED"


def test_new_plan_revision_reuses_logical_ids_without_rebinding_active_run(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    old_run_id, old_generation, old_version = start(
        service, "AB-1", "worker-1", "old-revision"
    )
    revised_task = task("AB-1")
    revised_task["objective"] = "Expanded objective"
    service.create_plan_draft(
        "PLAN-1",
        "Plan revision 2",
        plan([revised_task, task("AB-2")]),
        revision=2,
        parent_revision=1,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="revision-2-draft",
    )
    impact = service.plan_validate("PLAN-1", 2)["impact"]
    assert impact["human_sensitive_tasks"] == ["AB-1"]
    approved = service.approve_plan(
        "PLAN-1",
        2,
        expected_version=0,
        actor=HUMAN,
        idempotency_key="revision-2-approve",
    )

    assert approved.data["materialized_task_ids"]["AB-1"] == "PLAN-1@2:AB-1"
    assert service.task_detail("AB-1")["run"]["id"] == old_run_id
    heartbeat = service.task_heartbeat(
        "AB-1",
        old_run_id,
        old_generation,
        expected_version=old_version,
        actor=WORKER_1,
        idempotency_key="old-revision-heartbeat",
    )
    assert heartbeat.version == old_version + 1
    assert service.task_get("PLAN-1@2:AB-1").display_state == "READY"


def test_expired_verification_lease_cannot_be_reclaimed_for_review(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, generation, version = start(service, "AB-1", "worker-1")
    result = service.task_report_result(
        "AB-1",
        run_id,
        generation,
        [{"kind": "tests", "summary": "passed"}],
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="result-before-review-expiry",
    )
    with connect(service.database_path) as connection:
        connection.execute(
            "UPDATE assignments SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE task_id='AB-1'"
        )

    with pytest.raises(LeaseFencedError, match="no longer active"):
        service.review_claim(
            "AB-1",
            result.data["review_id"],
            "orchestrator",
            expected_version=result.version,
            actor=ORCHESTRATOR,
            idempotency_key="expired-review-claim",
        )


def test_stale_sweeper_command_releases_wip_with_versioned_event(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1")])
    run_id, _, version = start(service, "AB-1", "worker-1")
    with connect(service.database_path) as connection:
        connection.execute(
            "UPDATE assignments SET expires_at='2000-01-01T00:00:00+00:00' "
            "WHERE task_id='AB-1'"
        )
    candidate = service.stale_candidates()[0]

    expired = service.expire_stale_assignment(
        "AB-1",
        candidate["assignment_id"],
        expected_version=version,
        actor=ORCHESTRATOR,
        idempotency_key="expire-stale-assignment",
    )

    assert expired.data["expired"] is True
    assert service.task_get("AB-1").display_state == "READY"
    assert service.run_get(run_id)["status"] == "STALE"
    assert service.board_snapshot()["wip"]["active"] == 0


def test_applied_configuration_is_effective_at_the_next_claim(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1"), task("AB-2")])
    payload = default_config("Configured project").model_dump(mode="json")
    payload["limits"]["project_wip"] = 1
    payload["limits"]["profile_wip"] = {"worker": 1}
    payload["leases"]["reservation_ttl_seconds"] = 45
    payload["leases"]["run_ttl_seconds"] = 90
    payload["leases"]["heartbeat_interval_seconds"] = 30
    payload["retries"]["transient_max_attempts"] = 1
    draft = service.config_draft_create(
        yaml.safe_dump(payload, sort_keys=False),
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="config-draft",
    )
    pending_state = service.config_state()
    validation = service.config_validate(draft.entity_id)
    assert pending_state["active"] is None
    assert pending_state["latest"]["id"] == draft.entity_id
    assert validation["content"]["limits"]["project_wip"] == 1
    applied = service.config_apply_draft(
        draft.entity_id,
        draft.data["content_hash"],
        expected_version=draft.version,
        actor=HUMAN,
        idempotency_key="config-apply",
    )

    assert applied.state == "ACTIVE"
    applied_state = service.config_state()
    assert applied_state["active"]["id"] == draft.entity_id
    assert applied_state["latest"]["id"] == draft.entity_id
    assert service.wip_limit == 1
    assert service.profile_wip_limits == {"worker": 1}
    assert service.reservation_ttl_seconds == 45
    assert service.lease_ttl_seconds == 90
    assert service.transient_max_attempts == 1
    with pytest.raises(VersionConflictError, match="configuration revision"):
        service.claim_task(
            "AB-1",
            "worker-1",
            config_revision="stale-config-id",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="claim-with-stale-config",
        )
    claimed = service.claim_task(
        "AB-1",
        "worker-1",
        config_revision=draft.entity_id,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="claim-after-config",
    )
    assert claimed.data["config_revision_id"] == draft.entity_id
    with connect(tmp_path / "state.db") as connection:
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE id=?",
            (claimed.data["assignment_id"],),
        ).fetchone()
    snapshot = json.loads(assignment["policy_snapshot_json"])
    assert assignment["config_revision_id"] == draft.entity_id
    assert snapshot["lease_ttl_seconds"] == 90
    assert snapshot["transient_max_attempts"] == 1

    replacement_payload = default_config("Configured project").model_dump(mode="json")
    replacement_payload["limits"]["project_wip"] = 1
    replacement_payload["limits"]["profile_wip"] = {"worker": 1}
    replacement_payload["leases"]["reservation_ttl_seconds"] = 45
    replacement_payload["leases"]["run_ttl_seconds"] = 300
    replacement_payload["leases"]["heartbeat_interval_seconds"] = 30
    replacement = service.config_draft_create(
        yaml.safe_dump(replacement_payload, sort_keys=False),
        expected_version=applied.version,
        actor=ORCHESTRATOR,
        idempotency_key="replacement-config-draft",
    )
    service.config_apply_draft(
        replacement.entity_id,
        replacement.data["content_hash"],
        expected_version=replacement.version,
        actor=HUMAN,
        idempotency_key="replacement-config-apply",
    )
    started = service.run_start(
        "AB-1",
        claimed.data["assignment_id"],
        claimed.data["lease_generation"],
        expected_version=claimed.version,
        actor=WORKER_1,
        idempotency_key="start-with-claimed-config",
    )
    expiry = datetime.fromisoformat(started.data["expires_at"])
    assert 70 <= (expiry - datetime.now(UTC)).total_seconds() <= 95
    with pytest.raises(PolicyViolationError, match="Project WIP"):
        service.claim_task(
            "AB-2",
            "worker-2",
            expected_version=0,
            actor=ORCHESTRATOR,
            idempotency_key="claim-over-new-limit",
        )


def test_evidence_profile_rejects_missing_or_failed_requirements(
    tmp_path: Path,
) -> None:
    strict_profile = {
        "require_changed_files": True,
        "require_tests": True,
        "require_acceptance_evidence": True,
        "require_local_checkpoint": False,
        "require_local_integration": False,
    }
    strict_task = task("AB-1")
    strict_task["evidence_profile"] = "strict"
    service = approved_service(
        tmp_path,
        [strict_task],
        evidence_profiles={"strict": strict_profile},
    )
    run_id, generation, version = start(service, "AB-1", "worker-1")

    with pytest.raises(PolicyViolationError, match="changed_files, acceptance"):
        service.task_report_result(
            "AB-1",
            run_id,
            generation,
            [{"kind": "tests", "summary": "passed"}],
            expected_version=version,
            actor=WORKER_1,
            idempotency_key="evidence-missing",
        )
    with pytest.raises(PolicyViolationError, match="Failed evidence"):
        service.task_report_result(
            "AB-1",
            run_id,
            generation,
            [
                {"kind": "changed_files", "summary": "src/app.py"},
                {"kind": "tests", "summary": "passed"},
                {
                    "kind": "acceptance",
                    "summary": "not met",
                    "payload": {"passed": False},
                },
            ],
            expected_version=version,
            actor=WORKER_1,
            idempotency_key="evidence-failed",
        )

    accepted = service.task_report_result(
        "AB-1",
        run_id,
        generation,
        [
            {"kind": "changed_files", "summary": "src/app.py"},
            {"kind": "tests", "summary": "passed"},
            {"kind": "acceptance", "summary": "met"},
        ],
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="evidence-complete",
    )
    assert accepted.state == "VERIFYING"


def test_unknown_evidence_profile_is_rejected_in_plan_draft(tmp_path: Path) -> None:
    service = BoardService(tmp_path / "state.db")
    unknown = task("AB-1")
    unknown["evidence_profile"] = "does-not-exist"

    with pytest.raises(PolicyViolationError, match="unknown evidence profile"):
        service.create_plan_draft(
            "PLAN-1",
            "Invalid evidence plan",
            plan([unknown]),
            expected_version=0,
            actor=HUMAN,
            idempotency_key="unknown-evidence-profile",
        )


def test_reviewer_instance_and_capacity_are_enforced(tmp_path: Path) -> None:
    service = approved_service(
        tmp_path,
        [task("AB-1", risk="HIGH"), task("AB-2", risk="HIGH")],
    )
    run_1, generation_1, version_1 = start(service, "AB-1", "worker-1")
    run_2, generation_2, version_2 = start(service, "AB-2", "worker-2")
    result_1 = service.task_report_result(
        "AB-1",
        run_1,
        generation_1,
        [{"kind": "tests", "summary": "passed"}],
        expected_version=version_1,
        actor=WORKER_1,
        idempotency_key="review-capacity-result-1",
    )
    result_2 = service.task_report_result(
        "AB-2",
        run_2,
        generation_2,
        [{"kind": "tests", "summary": "passed"}],
        expected_version=version_2,
        actor=WORKER_2,
        idempotency_key="review-capacity-result-2",
    )
    service.register_agent(
        "reviewer-1",
        "agentboard_reviewer",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="reviewer-register",
    )
    service.register_agent_instance(
        "reviewer-instance-1",
        "reviewer-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="reviewer-instance-register",
    )
    claimed = service.review_claim(
        "AB-1",
        result_1.data["review_id"],
        "reviewer-1",
        reviewer_instance_id="reviewer-instance-1",
        expected_version=result_1.version,
        actor=ORCHESTRATOR,
        idempotency_key="review-capacity-claim-1",
    )
    assert claimed.data["reviewer_id"] == "reviewer-1"

    with pytest.raises(PolicyViolationError, match="capacity"):
        service.review_claim(
            "AB-2",
            result_2.data["review_id"],
            "reviewer-1",
            expected_version=result_2.version,
            actor=ORCHESTRATOR,
            idempotency_key="review-capacity-claim-2",
        )

    with pytest.raises(PolicyViolationError, match="started"):
        service.review_decide(
            "AB-1",
            result_1.data["review_id"],
            ReviewStatus.APPROVED,
            "too early",
            expected_version=claimed.version,
            actor=REVIEWER_1,
            idempotency_key="review-decision-before-start",
        )
    started_review = service.review_start(
        "AB-1",
        result_1.data["review_id"],
        expected_version=claimed.version,
        actor=REVIEWER_1,
        idempotency_key="review-start-once",
        thread_id="thread-reviewer-1",
    )
    with pytest.raises(InvalidTransitionError, match="already"):
        service.review_start(
            "AB-1",
            result_1.data["review_id"],
            expected_version=started_review.version,
            actor=REVIEWER_1,
            idempotency_key="review-start-twice",
            thread_id="thread-reviewer-replacement",
        )


def test_cancel_abandons_review_and_releases_reviewer_capacity(
    tmp_path: Path,
) -> None:
    service = approved_service(
        tmp_path,
        [task("AB-1", risk="HIGH"), task("AB-2", risk="HIGH")],
    )
    service.register_agent_instance(
        "worker-instance-1",
        "worker-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-worker-instance",
    )
    claim_1 = service.claim_task(
        "AB-1",
        "worker-1",
        instance_id="worker-instance-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-claim-1",
    )
    started_1 = service.run_start(
        "AB-1",
        claim_1.data["assignment_id"],
        claim_1.data["lease_generation"],
        thread_id="thread-worker-1",
        expected_version=claim_1.version,
        actor=WORKER_1,
        idempotency_key="cancel-start-1",
    )
    run_2, generation_2, version_2 = start(service, "AB-2", "worker-2")
    result_1 = service.task_report_result(
        "AB-1",
        started_1.data["run_id"],
        claim_1.data["lease_generation"],
        [{"kind": "tests", "summary": "passed"}],
        expected_version=started_1.version,
        actor=WORKER_1,
        idempotency_key="cancel-review-result-1",
    )
    result_2 = service.task_report_result(
        "AB-2",
        run_2,
        generation_2,
        [{"kind": "tests", "summary": "passed"}],
        expected_version=version_2,
        actor=WORKER_2,
        idempotency_key="cancel-review-result-2",
    )
    service.register_agent(
        "reviewer-1",
        "agentboard_reviewer",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-reviewer-register",
    )
    service.register_agent_instance(
        "reviewer-instance-1",
        "reviewer-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-reviewer-instance",
    )
    claimed_review = service.review_claim(
        "AB-1",
        result_1.data["review_id"],
        "reviewer-1",
        reviewer_instance_id="reviewer-instance-1",
        expected_version=result_1.version,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-review-claim",
    )
    started_review = service.review_start(
        "AB-1",
        result_1.data["review_id"],
        expected_version=claimed_review.version,
        actor=REVIEWER_1,
        idempotency_key="cancel-review-start",
        thread_id="thread-reviewer-1",
    )

    canceled = service.task_cancel(
        "AB-1",
        "Plan scope changed",
        expected_version=started_review.version,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-during-review",
    )

    assert canceled.data["abandoned_review_ids"] == [result_1.data["review_id"]]
    with connect(service.database_path) as connection:
        review = connection.execute(
            "SELECT * FROM reviews WHERE id=?",
            (result_1.data["review_id"],),
        ).fetchone()
        instances = {
            row["id"]: row["status"]
            for row in connection.execute(
                """
                SELECT id, status FROM agent_instances
                WHERE id IN ('worker-instance-1','reviewer-instance-1')
                """
            ).fetchall()
        }
    assert review["status"] == ReviewStatus.ABANDONED
    assert review["decision_reason"] == "Task canceled: Plan scope changed"
    assert review["decided_at"] is not None
    assert instances == {
        "worker-instance-1": "IDLE",
        "reviewer-instance-1": "IDLE",
    }

    reclaimed = service.review_claim(
        "AB-2",
        result_2.data["review_id"],
        "reviewer-1",
        reviewer_instance_id="reviewer-instance-1",
        expected_version=result_2.version,
        actor=ORCHESTRATOR,
        idempotency_key="cancel-review-capacity-reclaimed",
    )
    assert reclaimed.data["reviewer_id"] == "reviewer-1"


def test_expired_verification_abandons_started_review(
    tmp_path: Path,
) -> None:
    service = approved_service(tmp_path, [task("AB-1", risk="HIGH")])
    run_id, generation, version = start(service, "AB-1", "worker-1")
    result = service.task_report_result(
        "AB-1",
        run_id,
        generation,
        [{"kind": "tests", "summary": "passed"}],
        expected_version=version,
        actor=WORKER_1,
        idempotency_key="expire-review-result",
    )
    service.register_agent(
        "reviewer-1",
        "agentboard_reviewer",
        1,
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="expire-reviewer-register",
    )
    service.register_agent_instance(
        "reviewer-instance-1",
        "reviewer-1",
        expected_version=0,
        actor=ORCHESTRATOR,
        idempotency_key="expire-reviewer-instance",
    )
    claimed = service.review_claim(
        "AB-1",
        result.data["review_id"],
        "reviewer-1",
        reviewer_instance_id="reviewer-instance-1",
        expected_version=result.version,
        actor=ORCHESTRATOR,
        idempotency_key="expire-review-claim",
    )
    started = service.review_start(
        "AB-1",
        result.data["review_id"],
        expected_version=claimed.version,
        actor=REVIEWER_1,
        idempotency_key="expire-review-start",
        thread_id="thread-reviewer-1",
    )
    with connect(service.database_path) as connection:
        connection.execute(
            """
            UPDATE assignments SET expires_at='2000-01-01T00:00:00+00:00'
            WHERE task_id='AB-1' AND status='ACCEPTED'
            """
        )
    candidate = service.stale_candidates()[0]

    expired = service.expire_stale_assignment(
        "AB-1",
        candidate["assignment_id"],
        expected_version=started.version,
        actor=ORCHESTRATOR,
        idempotency_key="expire-started-review",
    )

    assert expired.data["abandoned_review_ids"] == [result.data["review_id"]]
    assert service.task_get("AB-1").display_state == "READY"
    assert service.run_get(run_id)["status"] == "STALE"
    assert service.board_snapshot()["wip"]["active"] == 0
    with connect(service.database_path) as connection:
        review = connection.execute(
            "SELECT status, decision_reason, decided_at FROM reviews WHERE id=?",
            (result.data["review_id"],),
        ).fetchone()
        instance = connection.execute(
            "SELECT status FROM agent_instances WHERE id='reviewer-instance-1'"
        ).fetchone()
    assert review["status"] == ReviewStatus.ABANDONED
    assert review["decision_reason"] == (
        "Assignment lease expired before review completed."
    )
    assert review["decided_at"] is not None
    assert instance["status"] == "IDLE"
