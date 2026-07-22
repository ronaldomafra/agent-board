from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class TaskState(StrEnum):
    BACKLOG = "BACKLOG"
    READY = "READY"
    ASSIGNED = "ASSIGNED"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFYING = "VERIFYING"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    REWORK = "REWORK"
    CANCELED = "CANCELED"


ALLOWED_TRANSITIONS: dict[TaskState, set[TaskState]] = {
    TaskState.BACKLOG: {TaskState.READY, TaskState.CANCELED},
    TaskState.READY: {TaskState.ASSIGNED, TaskState.BLOCKED, TaskState.CANCELED},
    TaskState.ASSIGNED: {TaskState.IN_PROGRESS, TaskState.READY, TaskState.BLOCKED},
    TaskState.IN_PROGRESS: {TaskState.VERIFYING, TaskState.BLOCKED, TaskState.REWORK},
    TaskState.VERIFYING: {TaskState.DONE, TaskState.REWORK, TaskState.BLOCKED},
    TaskState.REWORK: {TaskState.IN_PROGRESS, TaskState.BLOCKED, TaskState.CANCELED},
    TaskState.BLOCKED: {TaskState.READY, TaskState.IN_PROGRESS, TaskState.CANCELED},
    TaskState.DONE: set(),
    TaskState.CANCELED: set(),
}


class InvalidTransitionError(ValueError):
    """Raised when a requested task state change violates the workflow."""


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
    state: TaskState = TaskState.BACKLOG
    version: int = 0

    def transition_to(self, target: TaskState) -> "Task":
        if target not in ALLOWED_TRANSITIONS[self.state]:
            raise InvalidTransitionError(f"{self.state} cannot transition to {target}.")
        return Task(id=self.id, title=self.title, state=target, version=self.version + 1)

