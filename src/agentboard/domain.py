from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import Any


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    COMPLETED = "COMPLETED"
    CANCELED = "CANCELED"


class TaskState(StrEnum):
    """Persisted task phases. READY, ASSIGNED, BLOCKED and REWORK are projections."""

    BACKLOG = "BACKLOG"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFYING = "VERIFYING"
    DONE = "DONE"
    CANCELED = "CANCELED"


class AssignmentStatus(StrEnum):
    RESERVED = "RESERVED"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    RELEASED = "RELEASED"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    STALE = "STALE"
    CANCELED = "CANCELED"


class ReviewStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    CHANGES_REQUESTED = "CHANGES_REQUESTED"
    ABANDONED = "ABANDONED"


class BlockerStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    WAIVED = "WAIVED"


class DependencyType(StrEnum):
    REQUIRES = "REQUIRES"
    ORDER_AFTER = "ORDER_AFTER"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class Priority(StrEnum):
    P0 = "P0"
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class DomainError(RuntimeError):
    code = "domain_error"


class InvalidTransitionError(DomainError):
    code = "invalid_transition"


class VersionConflictError(DomainError):
    code = "version_conflict"


class IdempotencyConflictError(DomainError):
    code = "idempotency_conflict"


class PolicyViolationError(DomainError):
    code = "policy_violation"


class NotFoundError(DomainError):
    code = "not_found"


class LeaseFencedError(DomainError):
    code = "lease_fenced"


@dataclass(frozen=True, slots=True)
class Actor:
    id: str
    roles: frozenset[str] = field(default_factory=frozenset)

    def __post_init__(self) -> None:
        if not self.id.strip():
            raise ValueError("Actor id is required.")

    def has_any_role(self, *roles: str) -> bool:
        return bool(self.roles.intersection(roles))


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    title: str
    state: TaskState = TaskState.BACKLOG
    version: int = 0
    plan_id: str | None = None
    plan_revision: int = 1
    objective: str = ""
    scope: str = ""
    acceptance: tuple[str, ...] = ()
    tests: tuple[str, ...] = ()
    priority: Priority = Priority.P2
    risk: RiskLevel = RiskLevel.MEDIUM
    suggested_profile: str = "worker"
    paths: tuple[str, ...] = ()
    plan_order: int = 0
    critical_path: int = 0
    downstream_count: int = 0
    git_required: bool = False

    def transition_to(self, target: TaskState) -> Task:
        allowed = {
            TaskState.BACKLOG: {
                TaskState.IN_PROGRESS,
                TaskState.CANCELED,
            },
            TaskState.IN_PROGRESS: {
                TaskState.BACKLOG,
                TaskState.VERIFYING,
                TaskState.CANCELED,
            },
            TaskState.VERIFYING: {
                TaskState.BACKLOG,
                TaskState.DONE,
                TaskState.CANCELED,
            },
            TaskState.DONE: set(),
            TaskState.CANCELED: set(),
        }
        if target not in allowed[self.state]:
            raise InvalidTransitionError(f"{self.state} cannot transition to {target}.")
        return replace(self, state=target, version=self.version + 1)


@dataclass(frozen=True, slots=True)
class TaskView:
    task: Task
    ready: bool
    assigned: bool
    blocked: bool
    rework: bool
    reasons: tuple[str, ...] = ()

    @property
    def display_state(self) -> str:
        if self.blocked:
            return "BLOCKED"
        if self.rework:
            return "REWORK"
        if self.task.state is TaskState.BACKLOG and self.assigned:
            return "ASSIGNED"
        if self.task.state is TaskState.BACKLOG and self.ready:
            return "READY"
        return self.task.state.value


@dataclass(frozen=True, slots=True)
class CommandResult:
    entity_id: str
    version: int
    state: str
    replayed: bool = False
    data: dict[str, Any] = field(default_factory=dict)
