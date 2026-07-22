import pytest

from agentboard.domain import InvalidTransitionError, Task, TaskState
from agentboard.service import BoardService


def test_transition_increments_version() -> None:
    task = Task(id="AB-1", title="Initial task", state=TaskState.BACKLOG)

    transitioned = task.transition_to(TaskState.READY)

    assert transitioned.state is TaskState.READY
    assert transitioned.version == 1


def test_invalid_transition_is_rejected() -> None:
    task = Task(id="AB-1", title="Initial task", state=TaskState.BACKLOG)

    with pytest.raises(InvalidTransitionError):
        task.transition_to(TaskState.DONE)


def test_stale_version_is_rejected_by_service() -> None:
    service = BoardService()
    task = Task(id="AB-1", title="Initial task", state=TaskState.BACKLOG, version=2)

    with pytest.raises(ValueError, match="stale"):
        service.transition_task(task, TaskState.READY, expected_version=1)

