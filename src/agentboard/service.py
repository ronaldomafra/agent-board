from __future__ import annotations

from dataclasses import dataclass

from agentboard.domain import Task, TaskState


@dataclass(slots=True)
class BoardService:
    """Application boundary shared by MCP and HTTP adapters.

    Persistence, lease management and audit events will be added in Milestone 1.
    Keep all state-changing use cases here rather than in transport handlers.
    """

    def transition_task(self, task: Task, target: TaskState, expected_version: int) -> Task:
        if task.version != expected_version:
            raise ValueError("Task version is stale; load the latest snapshot and retry.")
        return task.transition_to(target)

