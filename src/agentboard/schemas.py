from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, JsonValue


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Command(ApiModel):
    idempotency_key: str = Field(min_length=8, max_length=200)
    expected_version: int = Field(ge=0)


class HumanAuthorizationRequest(ApiModel):
    operation: Literal[
        "plan_approve",
        "review_human_approve",
        "config_apply_draft",
        "task_cancel",
        "dependency_waive",
        "git_integrate_plan",
    ]
    resource_id: str | None = None


class RuntimeClientCommand(ApiModel):
    client_id: str = Field(min_length=16, max_length=200)


class PlanTaskInput(ApiModel):
    id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1)
    scope: tuple[str, ...] | str = ()
    acceptance_criteria: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("acceptance_criteria", "acceptance")
    )
    test_plan: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("test_plan", "tests")
    )
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    suggested_profile: str = Field(
        default="worker",
        validation_alias=AliasChoices("suggested_profile", "profile"),
    )
    expected_paths: tuple[str, ...] = Field(
        default=(), validation_alias=AliasChoices("expected_paths", "paths")
    )
    evidence_profile: str = "default"
    group_id: str | None = None


class DependencyInput(ApiModel):
    upstream_task_id: str
    downstream_task_id: str
    type: Literal["REQUIRES", "ORDER_AFTER"] = "REQUIRES"


class PlanGroupInput(ApiModel):
    id: str = Field(min_length=1, max_length=120)
    kind: Literal["milestone", "gate", "feature"]
    title: str = Field(min_length=1, max_length=240)
    position: int = Field(default=0, ge=0)


class PlanDraftCreate(Command):
    plan_id: str = Field(min_length=1, max_length=120)
    revision: int = Field(default=1, ge=1)
    parent_revision: int | None = Field(default=None, ge=1)
    title: str = Field(min_length=1, max_length=240)
    objective: str = Field(min_length=1)
    target_branch: str | None = None
    tasks: tuple[PlanTaskInput, ...] = ()
    groups: tuple[PlanGroupInput, ...] = ()
    dependencies: tuple[DependencyInput, ...] = ()


class PlanDraftUpdate(Command):
    title: str | None = None
    objective: str | None = None
    tasks: tuple[PlanTaskInput, ...] | None = None
    groups: tuple[PlanGroupInput, ...] | None = None
    dependencies: tuple[DependencyInput, ...] | None = None
    impact_reason: str = Field(min_length=1)


class RevisionCommand(Command):
    revision: int = Field(ge=1)


class AgentRegister(Command):
    agent_id: str
    profile: str
    capacity: int = Field(default=1, ge=1, le=32)


class AgentInstanceRegister(Command):
    instance_id: str
    agent_id: str
    thread_id: str | None = None


class TaskClaim(Command):
    task_id: str
    agent_id: str
    agent_instance_id: str | None = None
    config_revision: str | None = None


class TaskMoveIntent(Command):
    task_id: str
    target_column: Literal[
        "backlog",
        "eligible",
        "in_progress",
        "verifying",
        "done",
    ]


class TaskReservationRelease(Command):
    task_id: str
    assignment_id: str
    reason: str = Field(min_length=1, max_length=4000)


class RunStart(Command):
    task_id: str
    assignment_id: str
    lease_generation: int = Field(ge=1)
    codex_thread_id: str


class Heartbeat(Command):
    task_id: str
    run_id: str
    lease_generation: int = Field(ge=1)
    checkpoint: str | None = Field(default=None, max_length=2000)


class TokenUsageInput(ApiModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)


class TaskBlock(Command):
    task_id: str
    run_id: str
    lease_generation: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    owner: str | None = None
    usage: TokenUsageInput | None = None


class EvidenceInput(ApiModel):
    kind: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1, max_length=4000)
    reference: str | None = Field(default=None, max_length=2000)
    passed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReportResult(Command):
    task_id: str
    run_id: str
    lease_generation: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=8000)
    evidence: tuple[EvidenceInput, ...]
    checkpoint_sha: str | None = None
    usage: TokenUsageInput | None = None


class RunFail(Command):
    task_id: str
    run_id: str
    lease_generation: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=4000)
    failure_kind: Literal[
        "TRANSIENT",
        "LOGICAL",
        "TEST",
        "CONFLICT",
        "AUTHORIZATION",
        "CANCELED",
    ] = "LOGICAL"
    usage: TokenUsageInput | None = None


class ReviewClaim(Command):
    task_id: str
    review_id: str
    reviewer_id: str
    reviewer_instance_id: str | None = None


class ReviewStart(Command):
    task_id: str
    review_id: str
    codex_thread_id: str = Field(min_length=1, max_length=240)


class ReviewDecision(Command):
    task_id: str
    review_id: str
    decision: Literal["APPROVED", "CHANGES_REQUESTED"]
    summary: str = Field(min_length=1, max_length=8000)
    evidence: tuple[EvidenceInput, ...] = ()


class ReviewHumanApproval(Command):
    task_id: str
    review_id: str


class TaskCancel(Command):
    task_id: str
    reason: str = Field(min_length=1, max_length=4000)


class DependencyWaive(Command):
    upstream_task_id: str
    downstream_task_id: str
    reason: str = Field(min_length=1, max_length=4000)


class ConfigDraftCreate(Command):
    yaml_text: str = Field(min_length=1, max_length=1_000_000)


class ConfigApply(Command):
    draft_id: str
    expected_fingerprint: str


class GitCheckpointCommand(Command):
    task_id: str
    run_id: str
    lease_generation: int = Field(ge=1)
    expected_head: str
    message: str = Field(min_length=1, max_length=200)


class GitIntegrateCommand(Command):
    plan_id: str
    source_ref: str
    target_branch: str
    expected_target_sha: str
    message: str = Field(min_length=1, max_length=200)
    human_authorized: bool = False


class GitTaskIntegrateCommand(Command):
    task_id: str
    run_id: str
    expected_target_sha: str


class GitPlanIntegrateCommand(Command):
    plan_id: str
    revision: int = Field(ge=1)
    target_branch: str
    expected_target_sha: str
    expected_source_sha: str


JsonObject = dict[str, JsonValue]


class HookProtectionOutput(ApiModel):
    active: bool
    reason: str | None
    activated_at: str | None = None
    script_hash: str | None = None


class WipOutput(ApiModel):
    active: int | None = None
    limit: int


class ProjectOutput(ApiModel):
    name: str
    path: str
    project_key: str
    wip: WipOutput
    hook_protection: HookProtectionOutput


class PlanDependencyOutput(ApiModel):
    task_id: str
    kind: Literal["REQUIRES", "ORDER_AFTER"]


class PlanContentTaskOutput(ApiModel):
    id: str
    title: str
    objective: str
    scope: str = ""
    acceptance: list[str]
    tests: list[str]
    priority: Literal["P0", "P1", "P2", "P3"] = "P2"
    risk: Literal["LOW", "MEDIUM", "HIGH", "CRITICAL"] = "MEDIUM"
    suggested_profile: str = "worker"
    paths: list[str] = Field(default_factory=list)
    evidence_profile: str = "default"
    group_id: str | None = None
    dependencies: list[PlanDependencyOutput] = Field(default_factory=list)
    critical_path: int | None = None
    downstream_count: int | None = None
    plan_order: int | None = None
    git_required: bool | None = None


class PlanContentOutput(ApiModel):
    title: str | None = None
    objective: str | None = None
    target_branch: str | None = None
    groups: list[PlanGroupInput] = Field(default_factory=list)
    tasks: list[PlanContentTaskOutput]
    impact_reason: str | None = None


class PlanOutput(ApiModel):
    id: str
    revision: int
    parent_revision: int | None
    title: str
    status: str
    version: int
    content_json: str
    approved_by: str | None
    approved_at: str | None
    created_at: str
    content: PlanContentOutput


class PlanSummaryOutput(ApiModel):
    id: str
    revision: int
    title: str
    status: str
    version: int
    approved_at: str | None
    created_at: str


class PlanImpactOutput(ApiModel):
    parent_revision: int
    added: list[str]
    removed: list[str]
    changed: list[str]
    active_runs_preserved: bool
    requires_human_approval: bool
    human_sensitive_tasks: list[str]


class PlanValidationOutput(ApiModel):
    valid: bool
    plan_id: str
    revision: int
    task_count: int
    graph_metrics: dict[str, tuple[int, int]]
    impact: PlanImpactOutput | None


class TaskOutput(ApiModel):
    id: str
    title: str
    state: str
    version: int
    plan_id: str | None
    plan_revision: int
    objective: str
    scope: str
    acceptance: list[str]
    tests: list[str]
    priority: str
    risk: str
    suggested_profile: str
    paths: list[str]
    plan_order: int
    critical_path: int
    downstream_count: int
    git_required: bool


class TaskViewOutput(ApiModel):
    task: TaskOutput
    ready: bool
    assigned: bool
    blocked: bool
    rework: bool
    reasons: list[str]


class TaskListOutput(ApiModel):
    tasks: list[TaskViewOutput]


class TaskDependencyOutput(ApiModel):
    task_id: str
    type: str
    title: str
    state: str
    waiver_id: str | None
    satisfied: bool
    waived: bool


class TaskDependentOutput(ApiModel):
    task_id: str
    type: str
    title: str
    state: str


class LeaseOutput(ApiModel):
    owner: str
    generation: int
    status: str
    expires_at: str
    paths: list[str]


class RunOutput(ApiModel):
    id: str
    task_id: str
    assignment_id: str
    actor_id: str
    status: str
    lease_generation: int
    plan_revision: int
    attempt: int
    started_at: str
    heartbeat_at: str
    finished_at: str | None
    failure_kind: str | None
    failure_reason: str | None
    result_summary: str | None
    worktree_path: str | None
    branch_ref: str | None
    start_sha: str | None
    checkpoint_sha: str | None
    integration_sha: str | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    completed_at: str | None
    duration_seconds: int | None
    task_title: str | None = None
    agent_id: str | None = None


class EvidenceOutput(ApiModel):
    id: str
    task_id: str
    run_id: str
    kind: str
    summary: str
    payload_json: str
    created_at: str
    payload: JsonObject


class CheckpointOutput(ApiModel):
    id: str
    run_id: str
    kind: str
    summary: str
    payload_json: str
    created_at: str
    payload: JsonObject


class ReviewOutput(ApiModel):
    id: str
    task_id: str
    run_id: str
    reviewer_id: str | None
    reviewer_instance_id: str | None
    reviewer_thread_id: str | None
    status: str
    decision_reason: str | None
    human_approved: int
    created_at: str
    started_at: str | None
    decided_at: str | None


class TaskDetailOutput(TaskOutput):
    phase: str
    status: str
    profile: str
    acceptance_criteria: list[str]
    dependencies: list[TaskDependencyOutput]
    dependents: list[TaskDependentOutput]
    checkpoints: list[CheckpointOutput]
    evidence: list[EvidenceOutput]
    lease: LeaseOutput | None
    run: RunOutput | None
    review: ReviewOutput | None
    blocked_reason: str | None
    ready: bool
    rework: bool
    reasons: list[str]


class BoardTaskOutput(TaskOutput):
    ready: bool
    assigned: bool
    blocked: bool
    rework: bool
    display_state: str
    reasons: list[str]
    phase: str
    status: str
    profile: str
    acceptance_criteria: list[str]


class BoardColumnsOutput(ApiModel):
    backlog: list[BoardTaskOutput]
    eligible: list[BoardTaskOutput]
    in_progress: list[BoardTaskOutput]
    verifying: list[BoardTaskOutput]
    done: list[BoardTaskOutput]


class BoardAttentionOutput(ApiModel):
    blocked: list[BoardTaskOutput]
    stale: list[BoardTaskOutput]
    awaiting_review: list[BoardTaskOutput]
    conflicts: list[BoardTaskOutput]


class AgentOutput(ApiModel):
    id: str
    profile: str
    capacity: int
    enabled: int
    version: int
    last_heartbeat_at: str | None
    current_task_id: str | None
    assignment_status: str | None
    expires_at: str | None


class AgentListOutput(ApiModel):
    agents: list[AgentOutput]


class RunDetailOutput(RunOutput):
    evidence: list[EvidenceOutput]


class RunListOutput(ApiModel):
    runs: list[RunOutput]


class EventOutput(ApiModel):
    sequence: int
    event_id: str
    entity_type: str
    entity_id: str
    event_type: str
    actor_id: str
    task_version: int | None
    payload_json: str
    created_at: str
    payload: JsonObject


class EventListOutput(ApiModel):
    events: list[EventOutput]


class ConfigRevisionOutput(ApiModel):
    id: str
    revision: int
    status: str
    version: int
    content_hash: str
    content_json: str
    validation_json: str
    actor_id: str
    created_at: str
    applied_at: str | None
    content: JsonObject
    validation: JsonObject


class ConfigStateOutput(ApiModel):
    active: ConfigRevisionOutput | None
    latest: ConfigRevisionOutput | None


class ConfigValidationOutput(ApiModel):
    draft_id: str
    valid: bool
    content_hash: str
    revision: int
    version: int
    content: JsonObject


class BoardProjectOutput(ApiModel):
    name: str
    path: str


class UsageTotalsOutput(ApiModel):
    input_tokens: int
    output_tokens: int
    total_tokens: int
    completed_duration_seconds: int
    reported_runs: int
    completed_runs: int


class BoardOutput(ApiModel):
    project: BoardProjectOutput
    hook_protection: HookProtectionOutput
    plan: PlanSummaryOutput | None
    plans: list[PlanSummaryOutput]
    tasks: list[BoardTaskOutput]
    columns: BoardColumnsOutput
    attention: BoardAttentionOutput
    agents: list[AgentOutput]
    runs: list[RunOutput]
    usage_totals: UsageTotalsOutput
    last_event_id: int
    wip: WipOutput


class CommandResultOutput(ApiModel):
    entity_id: str
    version: int
    state: str
    replayed: bool = False
    data: JsonObject = Field(default_factory=dict)


class DashboardOpenOutput(ApiModel):
    url: str


class ErrorBody(ApiModel):
    code: str
    message: str
    details: dict[str, Any] | None = None
    retryable: bool = False


class ErrorResponse(ApiModel):
    error: ErrorBody
