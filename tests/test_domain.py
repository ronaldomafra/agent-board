import pytest

from agentboard.domain import (
    AssignmentStatus,
    BlockerStatus,
    InvalidTransitionError,
    PlanStatus,
    ReviewStatus,
    RunStatus,
    Task,
    TaskState,
    VersionConflictError,
)
from agentboard.service import BoardService


def test_canonical_states_do_not_persist_projection_states() -> None:
    assert set(TaskState) == {
        TaskState.BACKLOG,
        TaskState.IN_PROGRESS,
        TaskState.VERIFYING,
        TaskState.DONE,
        TaskState.CANCELED,
    }
    assert {value.value for value in PlanStatus} == {
        "DRAFT", "ACTIVE", "SUPERSEDED", "COMPLETED", "CANCELED"
    }
    assert {value.value for value in AssignmentStatus} == {
        "RESERVED", "ACCEPTED", "EXPIRED", "RELEASED"
    }
    assert {value.value for value in RunStatus} == {
        "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "STALE", "CANCELED"
    }
    assert {value.value for value in ReviewStatus} == {
        "PENDING", "APPROVED", "CHANGES_REQUESTED", "ABANDONED"
    }
    assert {value.value for value in BlockerStatus} == {"OPEN", "RESOLVED", "WAIVED"}


def test_transition_increments_version_without_losing_metadata() -> None:
    task = Task(
        id="AB-1",
        title="Initial task",
        state=TaskState.BACKLOG,
        objective="Ship",
        paths=("src/",),
    )

    transitioned = task.transition_to(TaskState.IN_PROGRESS)

    assert transitioned.state is TaskState.IN_PROGRESS
    assert transitioned.version == 1
    assert transitioned.objective == "Ship"
    assert transitioned.paths == ("src/",)


def test_invalid_transition_is_rejected() -> None:
    task = Task(id="AB-1", title="Initial task", state=TaskState.BACKLOG)

    with pytest.raises(InvalidTransitionError):
        task.transition_to(TaskState.DONE)


def test_stale_version_is_rejected_by_service() -> None:
    service = BoardService()
    task = Task(id="AB-1", title="Initial task", state=TaskState.BACKLOG, version=2)

    with pytest.raises(VersionConflictError, match="stale"):
        service.transition_task(task, TaskState.IN_PROGRESS, expected_version=1)
