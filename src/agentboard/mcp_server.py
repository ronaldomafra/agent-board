from __future__ import annotations

import secrets
import threading
from pathlib import Path
from typing import Annotated, Any, Literal, TypeVar

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from agentboard.client import AgentBoardClientError, RuntimeClient
from agentboard.runtime import ensure_runtime
from agentboard.schemas import (
    AgentListOutput,
    ApiModel,
    BoardOutput,
    CommandResultOutput,
    ConfigStateOutput,
    ConfigValidationOutput,
    DashboardOpenOutput,
    DependencyInput,
    EventListOutput,
    EvidenceInput,
    PlanGroupInput,
    PlanOutput,
    PlanTaskInput,
    PlanValidationOutput,
    ProjectOutput,
    RunDetailOutput,
    RunListOutput,
    TaskDetailOutput,
    TaskListOutput,
)

mcp = FastMCP("AgentBoard")
_attached_client: RuntimeClient | None = None

ExpectedVersion = Annotated[int, Field(ge=0)]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=200)]
Revision = Annotated[int, Field(ge=1)]
LeaseGeneration = Annotated[int, Field(ge=1)]
PlanIdentifier = Annotated[str, Field(min_length=1, max_length=120)]
PlanTitle = Annotated[str, Field(min_length=1, max_length=240)]
PlanObjective = Annotated[str, Field(min_length=1)]
ImpactReason = Annotated[str, Field(min_length=1)]
AgentCapacity = Annotated[int, Field(ge=1, le=32)]
Reason = Annotated[str, Field(min_length=1, max_length=4000)]
Checkpoint = Annotated[str, Field(max_length=2000)]
ResultSummary = Annotated[str, Field(min_length=1, max_length=8000)]
ReviewThreadId = Annotated[str, Field(min_length=1, max_length=240)]
ConfigYaml = Annotated[str, Field(min_length=1, max_length=1_000_000)]
GitMessage = Annotated[str, Field(min_length=1, max_length=200)]
FailureKind = Literal[
    "TRANSIENT",
    "LOGICAL",
    "TEST",
    "CONFLICT",
    "AUTHORIZATION",
    "CANCELED",
]
ReviewOutcome = Literal["APPROVED", "CHANGES_REQUESTED"]

_READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_ADDITIVE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
_DESTRUCTIVE_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
_NON_IDEMPOTENT_WRITE = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=False,
)

ModelT = TypeVar("ModelT", bound=ApiModel)
OutputT = TypeVar("OutputT", bound=ApiModel)


def _client() -> RuntimeClient:
    return _attached_client or RuntimeClient.for_project(Path.cwd())


def _dto_payloads(values: list[ModelT] | tuple[ModelT, ...]) -> list[dict[str, Any]]:
    return [value.model_dump(mode="json") for value in values]


def _validated_output(model: type[OutputT], payload: object) -> OutputT:
    """Reject an HTTP payload that does not match the MCP wire contract."""
    return model.model_validate(payload)


def _command_output(payload: object) -> CommandResultOutput:
    return _validated_output(CommandResultOutput, payload)


@mcp.tool(annotations=_READ_ONLY)
def project_open() -> ProjectOutput:
    """Open the current local project and return its AgentBoard identity and policy."""
    return _validated_output(ProjectOutput, _client().get("/api/v1/project"))


@mcp.tool(annotations=_READ_ONLY)
def plan_get(plan_id: str, revision: int | None = None) -> PlanOutput:
    """Read one plan revision without changing operational state."""
    return _validated_output(
        PlanOutput,
        _client().get(f"/api/v1/plans/{plan_id}", {"revision": revision}),
    )


@mcp.tool(annotations=_READ_ONLY)
def board_snapshot() -> BoardOutput:
    """Return one consistent board snapshot and its replay cursor."""
    return _validated_output(BoardOutput, _client().get("/api/v1/board"))


@mcp.tool(annotations=_READ_ONLY)
def task_get(task_id: str) -> TaskDetailOutput:
    """Read task details, dependencies, lease, run, review and evidence."""
    return _validated_output(
        TaskDetailOutput,
        _client().get(f"/api/v1/tasks/{task_id}"),
    )


@mcp.tool(annotations=_READ_ONLY)
def task_list(plan_id: str | None = None) -> TaskListOutput:
    """List tasks, optionally restricted to one plan."""
    return _validated_output(
        TaskListOutput,
        {"tasks": _client().get("/api/v1/tasks", {"plan_id": plan_id})},
    )


@mcp.tool(annotations=_READ_ONLY)
def schedule_next(limit: int = 20) -> TaskListOutput:
    """Return the deterministic ordered set of eligible tasks."""
    return _validated_output(
        TaskListOutput,
        {"tasks": _client().get("/api/v1/schedule", {"limit": limit})},
    )


@mcp.tool(annotations=_READ_ONLY)
def agent_list() -> AgentListOutput:
    """List configured agents and their active reservations."""
    return _validated_output(
        AgentListOutput,
        {"agents": _client().get("/api/v1/agents")},
    )


@mcp.tool(annotations=_READ_ONLY)
def run_get(run_id: str) -> RunDetailOutput:
    """Read one run and its evidence."""
    return _validated_output(
        RunDetailOutput,
        _client().get(f"/api/v1/runs/{run_id}"),
    )


@mcp.tool(annotations=_READ_ONLY)
def run_list(
    task_id: str | None = None,
    status: str | None = None,
    limit: int = 200,
) -> RunListOutput:
    """List local run attempts."""
    return _validated_output(
        RunListOutput,
        {
            "runs": _client().get(
                "/api/v1/runs",
                {"task_id": task_id, "status": status, "limit": limit},
            )
        },
    )


@mcp.tool(annotations=_READ_ONLY)
def event_list(after_sequence: int = 0, limit: int = 200) -> EventListOutput:
    """Replay ordered audit events after a sequence cursor."""
    return _validated_output(
        EventListOutput,
        {
            "events": _client().get(
                "/api/v1/event-log",
                {"after_sequence": after_sequence, "limit": limit},
            )
        },
    )


@mcp.tool(annotations=_READ_ONLY)
def config_get() -> ConfigStateOutput:
    """Read the active or latest validated project configuration."""
    return _validated_output(
        ConfigStateOutput,
        _client().get("/api/v1/config/state"),
    )


@mcp.tool(annotations=_ADDITIVE_WRITE)
def plan_draft_create(
    plan_id: PlanIdentifier,
    title: PlanTitle,
    objective: PlanObjective,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    target_branch: str | None = None,
    tasks: tuple[PlanTaskInput, ...] = (),
    dependencies: tuple[DependencyInput, ...] = (),
    groups: tuple[PlanGroupInput, ...] = (),
) -> CommandResultOutput:
    """Create a plan draft; it remains non-executable until human approval."""
    return _command_output(
        _client().post(
            "/api/v1/plans",
            {
                "plan_id": plan_id,
                "revision": 1,
                "title": title,
                "objective": objective,
                "tasks": _dto_payloads(tasks),
                "groups": _dto_payloads(groups),
                "dependencies": _dto_payloads(dependencies),
                "target_branch": target_branch,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def plan_draft_update(
    plan_id: PlanIdentifier,
    revision: Revision,
    impact_reason: ImpactReason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    title: PlanTitle | None = None,
    objective: PlanObjective | None = None,
    tasks: list[PlanTaskInput] | None = None,
    dependencies: list[DependencyInput] | None = None,
    groups: list[PlanGroupInput] | None = None,
) -> CommandResultOutput:
    """Update a mutable draft and record the reason for its impact."""
    payload: dict[str, Any] = {
        "impact_reason": impact_reason,
        "expected_version": expected_version,
        "idempotency_key": idempotency_key,
    }
    if title is not None:
        payload["title"] = title
    if objective is not None:
        payload["objective"] = objective
    if tasks is not None:
        payload["tasks"] = _dto_payloads(tasks)
    if dependencies is not None:
        payload["dependencies"] = _dto_payloads(dependencies)
    if groups is not None:
        payload["groups"] = _dto_payloads(groups)
    return _command_output(
        _client().post(
            f"/api/v1/plans/{plan_id}/revisions/{revision}",
            payload,
        )
    )


@mcp.tool(annotations=_READ_ONLY)
def plan_validate(
    plan_id: str, revision: int | None = None
) -> PlanValidationOutput:
    """Validate a draft's tasks, paths and dependency graph."""
    return _validated_output(
        PlanValidationOutput,
        _client().get(
            f"/api/v1/plans/{plan_id}/validate",
            {"revision": revision},
        ),
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def plan_approve(
    plan_id: str,
    revision: Revision,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str | None = None,
) -> CommandResultOutput:
    """Approve a plan using an authenticated human capability."""
    return _command_output(
        _client().post(
            f"/api/v1/plans/{plan_id}/revisions/{revision}/approve",
            {
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_ADDITIVE_WRITE)
def plan_revision_create(
    plan_id: PlanIdentifier,
    revision: Revision,
    parent_revision: Revision,
    title: PlanTitle,
    objective: PlanObjective,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    target_branch: str | None = None,
    tasks: tuple[PlanTaskInput, ...] = (),
    dependencies: tuple[DependencyInput, ...] = (),
    groups: tuple[PlanGroupInput, ...] = (),
) -> CommandResultOutput:
    """Create an immutable-successor draft linked to an approved revision."""
    return _command_output(
        _client().post(
            "/api/v1/plans",
            {
                "plan_id": plan_id,
                "revision": revision,
                "parent_revision": parent_revision,
                "title": title,
                "objective": objective,
                "target_branch": target_branch,
                "tasks": _dto_payloads(tasks),
                "groups": _dto_payloads(groups),
                "dependencies": _dto_payloads(dependencies),
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def plan_revision_approve(
    plan_id: str,
    revision: Revision,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str | None = None,
) -> CommandResultOutput:
    """Approve a successor revision with authenticated human authority."""
    return plan_approve(
        plan_id,
        revision,
        expected_version,
        idempotency_key,
        human_capability,
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def agent_register(
    agent_id: str,
    profile: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capacity: AgentCapacity = 1,
) -> CommandResultOutput:
    """Register or update an ephemeral agent profile for this project."""
    return _command_output(
        _client().post(
            "/api/v1/agents",
            {
                "agent_id": agent_id,
                "profile": profile,
                "capacity": capacity,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_ADDITIVE_WRITE)
def agent_instance_register(
    instance_id: str,
    agent_id: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    thread_id: str | None = None,
) -> CommandResultOutput:
    """Bind a native Codex agent instance/thread before claiming work."""
    return _command_output(
        _client().post(
            "/api/v1/agent-instances",
            {
                "instance_id": instance_id,
                "agent_id": agent_id,
                "thread_id": thread_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_claim(
    task_id: str,
    agent_id: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    agent_instance_id: str | None = None,
    config_revision: str | None = None,
) -> CommandResultOutput:
    """Atomically reserve WIP, capacity and file paths for an eligible task."""
    return _command_output(
        _client().post(
            "/api/v1/tasks/claim",
            {
                "task_id": task_id,
                "agent_id": agent_id,
                "agent_instance_id": agent_instance_id,
                "config_revision": config_revision,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_release_reservation(
    task_id: str,
    assignment_id: str,
    reason: Reason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
) -> CommandResultOutput:
    """Release an unaccepted reservation when native Codex spawn fails."""
    return _command_output(
        _client().post(
            "/api/v1/tasks/release-reservation",
            {
                "task_id": task_id,
                "assignment_id": assignment_id,
                "reason": reason,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def run_start(
    task_id: str,
    assignment_id: str,
    lease_generation: LeaseGeneration,
    codex_thread_id: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
) -> CommandResultOutput:
    """Accept a reservation and bind the native Codex thread to a fenced run."""
    return _command_output(
        _client().post(
            "/api/v1/runs/start",
            {
                "task_id": task_id,
                "assignment_id": assignment_id,
                "lease_generation": lease_generation,
                "codex_thread_id": codex_thread_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_heartbeat(
    task_id: str,
    run_id: str,
    lease_generation: LeaseGeneration,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
    checkpoint: Checkpoint | None = None,
) -> CommandResultOutput:
    """Renew an owned run and optionally record an objective checkpoint."""
    return _command_output(
        _client().post(
            "/api/v1/runs/heartbeat",
            {
                "task_id": task_id,
                "run_id": run_id,
                "lease_generation": lease_generation,
                "checkpoint": checkpoint,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_block(
    task_id: str,
    run_id: str,
    lease_generation: LeaseGeneration,
    reason: Reason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
    owner: str | None = None,
) -> CommandResultOutput:
    """Record a blocker, end the run and release its WIP/file lease."""
    return _command_output(
        _client().post(
            "/api/v1/tasks/block",
            {
                "task_id": task_id,
                "run_id": run_id,
                "lease_generation": lease_generation,
                "reason": reason,
                "owner": owner,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_report_result(
    task_id: str,
    run_id: str,
    lease_generation: LeaseGeneration,
    summary: ResultSummary,
    evidence: list[EvidenceInput],
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
    checkpoint_sha: str | None = None,
) -> CommandResultOutput:
    """Submit evidence and move owned work into verification."""
    return _command_output(
        _client().post(
            "/api/v1/runs/result",
            {
                "task_id": task_id,
                "run_id": run_id,
                "lease_generation": lease_generation,
                "summary": summary,
                "evidence": _dto_payloads(evidence),
                "checkpoint_sha": checkpoint_sha,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def run_fail(
    task_id: str,
    run_id: str,
    lease_generation: LeaseGeneration,
    reason: Reason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
    failure_kind: FailureKind = "LOGICAL",
) -> CommandResultOutput:
    """Fail an owned run and release its operational lease."""
    return _command_output(
        _client().post(
            "/api/v1/runs/fail",
            {
                "task_id": task_id,
                "run_id": run_id,
                "lease_generation": lease_generation,
                "reason": reason,
                "failure_kind": failure_kind,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def review_claim(
    task_id: str,
    review_id: str,
    reviewer_id: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    reviewer_instance_id: str | None = None,
) -> CommandResultOutput:
    """Assign an eligible independent reviewer and issue a scoped capability."""
    return _command_output(
        _client().post(
            "/api/v1/reviews/claim",
            {
                "task_id": task_id,
                "review_id": review_id,
                "reviewer_id": reviewer_id,
                "reviewer_instance_id": reviewer_instance_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def review_start(
    task_id: str,
    review_id: str,
    codex_thread_id: ReviewThreadId,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
) -> CommandResultOutput:
    """Bind a reviewer thread and renew the verification lease."""
    return _command_output(
        _client().post(
            "/api/v1/reviews/start",
            {
                "task_id": task_id,
                "review_id": review_id,
                "codex_thread_id": codex_thread_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def review_decide(
    task_id: str,
    review_id: str,
    decision: ReviewOutcome,
    summary: ResultSummary,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str | None = None,
    evidence: tuple[EvidenceInput, ...] = (),
) -> CommandResultOutput:
    """Approve or request changes; critical human approval is a separate gate."""
    return _command_output(
        _client().post(
            "/api/v1/reviews/decide",
            {
                "task_id": task_id,
                "review_id": review_id,
                "decision": decision,
                "summary": summary,
                "evidence": _dto_payloads(evidence),
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def review_human_approve(
    task_id: str,
    review_id: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str,
) -> CommandResultOutput:
    """Record the separate authenticated human gate for critical work."""
    return _command_output(
        _client().post(
            "/api/v1/reviews/human-approve",
            {
                "task_id": task_id,
                "review_id": review_id,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def task_cancel(
    task_id: str,
    reason: Reason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str | None = None,
) -> CommandResultOutput:
    """Cancel non-terminal work with elevated local authority."""
    return _command_output(
        _client().post(
            "/api/v1/tasks/cancel",
            {
                "task_id": task_id,
                "reason": reason,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def dependency_waive(
    upstream_task_id: str,
    downstream_task_id: str,
    reason: Reason,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str | None = None,
) -> CommandResultOutput:
    """Waive one exact dependency edge with elevated authority."""
    return _command_output(
        _client().post(
            "/api/v1/dependencies/waive",
            {
                "upstream_task_id": upstream_task_id,
                "downstream_task_id": downstream_task_id,
                "reason": reason,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_ADDITIVE_WRITE)
def config_draft_create(
    yaml_text: ConfigYaml,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
) -> CommandResultOutput:
    """Create and validate a configuration draft without applying it."""
    return _command_output(
        _client().post(
            "/api/v1/config/drafts",
            {
                "yaml_text": yaml_text,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_READ_ONLY)
def config_validate(draft_id: str) -> ConfigValidationOutput:
    """Revalidate a stored configuration draft."""
    return _validated_output(
        ConfigValidationOutput,
        _client().get(f"/api/v1/config/drafts/{draft_id}/validate"),
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def config_apply_draft(
    draft_id: str,
    expected_fingerprint: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str | None = None,
) -> CommandResultOutput:
    """Explicitly apply a validated config using authenticated human authority."""
    return _command_output(
        _client().post(
            "/api/v1/config/apply",
            {
                "draft_id": draft_id,
                "expected_fingerprint": expected_fingerprint,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_ADDITIVE_WRITE)
def git_checkpoint(
    task_id: str,
    run_id: str,
    lease_generation: LeaseGeneration,
    expected_head: str,
    message: GitMessage,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    capability_token: str,
) -> CommandResultOutput:
    """Create a local-only checkpoint commit restricted to leased paths."""
    return _command_output(
        _client().post(
            "/api/v1/git/checkpoint",
            {
                "task_id": task_id,
                "run_id": run_id,
                "lease_generation": lease_generation,
                "expected_head": expected_head,
                "message": message,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=capability_token,
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def git_integrate(
    task_id: str,
    run_id: str,
    expected_target_sha: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
) -> CommandResultOutput:
    """Integrate approved task work into its local plan branch."""
    return _command_output(
        _client().post(
            "/api/v1/git/integrate-task",
            {
                "task_id": task_id,
                "run_id": run_id,
                "expected_target_sha": expected_target_sha,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
        )
    )


@mcp.tool(annotations=_DESTRUCTIVE_WRITE)
def git_integrate_plan(
    plan_id: str,
    revision: Revision,
    target_branch: str,
    expected_target_sha: str,
    expected_source_sha: str,
    expected_version: ExpectedVersion,
    idempotency_key: IdempotencyKey,
    human_capability: str,
) -> CommandResultOutput:
    """Integrate a completed plan branch into a clean local target branch."""
    return _command_output(
        _client().post(
            "/api/v1/git/integrate-plan",
            {
                "plan_id": plan_id,
                "revision": revision,
                "target_branch": target_branch,
                "expected_target_sha": expected_target_sha,
                "expected_source_sha": expected_source_sha,
                "expected_version": expected_version,
                "idempotency_key": idempotency_key,
            },
            capability_token=human_capability,
        )
    )


@mcp.tool(annotations=_NON_IDEMPOTENT_WRITE)
def dashboard_open() -> DashboardOpenOutput:
    """Mint a one-use authenticated URL for the local dashboard."""
    client = _client()
    response = client.post("/api/v1/dashboard/bootstrap", {})
    path = response.get("path")
    return _validated_output(
        DashboardOpenOutput,
        {
            "url": (
                f"{client.metadata.base_url}{path}"
                if isinstance(path, str)
                else None
            )
        },
    )


def run() -> None:
    """Run the thin AgentBoard MCP bridge over stdio."""
    global _attached_client
    client_id = f"mcp-{secrets.token_urlsafe(24)}"
    _attached_client = RuntimeClient(ensure_runtime(Path.cwd()))
    attached = _attached_client.post(
        "/api/v1/runtime/attach", {"client_id": client_id}
    )
    heartbeat_stop = threading.Event()

    def keep_runtime_client_alive() -> None:
        interval = max(1.0, float(attached["client_ttl_seconds"]) / 3)
        while not heartbeat_stop.wait(interval):
            try:
                if _attached_client is not None:
                    _attached_client.post(
                        "/api/v1/runtime/heartbeat",
                        {"client_id": client_id},
                    )
            except AgentBoardClientError:
                return

    heartbeat_thread = threading.Thread(
        target=keep_runtime_client_alive,
        name="agentboard-mcp-heartbeat",
        daemon=True,
    )
    heartbeat_thread.start()
    try:
        mcp.run()
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=5)
        try:
            try:
                _attached_client.post(
                    "/api/v1/runtime/detach", {"client_id": client_id}
                )
            except AgentBoardClientError:
                pass
        finally:
            _attached_client = None
