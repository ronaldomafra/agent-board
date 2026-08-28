from __future__ import annotations

import asyncio
import json
import secrets
import threading
import time
import urllib.parse
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from agentboard.auth import AuthenticationError, LocalAuth, Principal
from agentboard.config import default_config, load_config, recover_config_apply
from agentboard.domain import (
    Actor,
    DomainError,
    IdempotencyConflictError,
    InvalidTransitionError,
    LeaseFencedError,
    NotFoundError,
    PolicyViolationError,
    ReviewStatus,
    VersionConflictError,
)
from agentboard.hook_guard import hook_protection_status
from agentboard.schemas import (
    AgentInstanceRegister,
    AgentOutput,
    AgentRegister,
    BoardOutput,
    Command,
    CommandResultOutput,
    ConfigApply,
    ConfigDraftCreate,
    ConfigRevisionOutput,
    ConfigStateOutput,
    ConfigValidationOutput,
    DependencyWaive,
    EventOutput,
    GitCheckpointCommand,
    GitPlanIntegrateCommand,
    GitTaskIntegrateCommand,
    Heartbeat,
    HumanAuthorizationRequest,
    PlanDraftCreate,
    PlanDraftUpdate,
    PlanOutput,
    PlanSummaryOutput,
    PlanValidationOutput,
    ProjectOutput,
    ReportResult,
    ReviewClaim,
    ReviewDecision,
    ReviewHumanApproval,
    ReviewStart,
    RunDetailOutput,
    RunFail,
    RunOutput,
    RunStart,
    RuntimeClientCommand,
    TaskBlock,
    TaskCancel,
    TaskClaim,
    TaskDetailOutput,
    TaskMoveIntent,
    TaskReservationRelease,
    TaskViewOutput,
)
from agentboard.serialization import to_jsonable
from agentboard.service import BoardService
from agentboard.storage import SCHEMA_VERSION


def _load_policy(project_root: Path) -> Any:
    recover_config_apply(project_root)
    path = project_root / "agentboard.yaml"
    return load_config(path) if path.is_file() else default_config(project_root.name)


def _plan_content(command: PlanDraftCreate | PlanDraftUpdate) -> dict[str, Any]:
    tasks = command.tasks or ()
    dependencies = command.dependencies or ()
    by_downstream: dict[str, list[dict[str, str]]] = {}
    for dependency in dependencies:
        by_downstream.setdefault(dependency.downstream_task_id, []).append(
            {
                "task_id": dependency.upstream_task_id,
                "kind": dependency.type,
            }
        )
    content: dict[str, Any] = {
        "groups": [
            group.model_dump(mode="json")
            for group in (command.groups or ())
        ],
        "tasks": [
            {
                "id": task.id,
                "title": task.title,
                "objective": task.objective,
                "scope": task.scope if isinstance(task.scope, str) else "\n".join(task.scope),
                "acceptance": list(task.acceptance_criteria),
                "tests": list(task.test_plan),
                "priority": task.priority,
                "risk": task.risk,
                "suggested_profile": task.suggested_profile,
                "paths": list(task.expected_paths),
                "evidence_profile": task.evidence_profile,
                "group_id": task.group_id,
                "dependencies": by_downstream.get(task.id, []),
            }
            for task in tasks
        ],
    }
    if command.title is not None:
        content["title"] = command.title
    if command.objective is not None:
        content["objective"] = command.objective
    if isinstance(command, PlanDraftCreate):
        content["target_branch"] = command.target_branch
    return content


def _actor(principal: Principal | Actor) -> Actor:
    if isinstance(principal, Actor):
        return principal
    return Actor(principal.actor_id, principal.roles)


def _plan_approval_resource(plan_id: str, revision: int, expected_version: int) -> str:
    """Bind a human approval to exactly the draft revision they inspected."""

    return f"plan:{plan_id}:revision:{revision}:version:{expected_version}"


def _error_status(error: Exception) -> int:
    if isinstance(error, NotFoundError):
        return 404
    if isinstance(error, AuthenticationError):
        return 401
    if isinstance(
        error,
        (
            VersionConflictError,
            IdempotencyConflictError,
            InvalidTransitionError,
            LeaseFencedError,
        ),
    ):
        return 409
    if isinstance(error, PolicyViolationError):
        return 403
    return 400


def create_app(
    *,
    project_root: Path | None = None,
    api_token: str | None = None,
    capability_secret: str | None = None,
    project_key: str = "local-project",
    service: BoardService | None = None,
    testing: bool = False,
    runtime_client_ttl_seconds: int = 30,
) -> FastAPI:
    root = (project_root or Path.cwd()).resolve()
    policy = _load_policy(root)
    board_service = service or BoardService(
        root / policy.paths.database,
        wip_limit=policy.limits.project_wip,
        profile_wip_limits=policy.limits.profile_wip,
        evidence_profiles={
            key: value.model_dump(mode="json")
            for key, value in policy.evidence_profiles.items()
        },
        reservation_ttl_seconds=policy.leases.reservation_ttl_seconds,
        lease_ttl_seconds=policy.leases.run_ttl_seconds,
        transient_max_attempts=policy.retries.transient_max_attempts,
    )
    auth = LocalAuth(
        api_token or secrets.token_urlsafe(48),
        capability_secret=capability_secret,
    )
    app = FastAPI(title="AgentBoard", version="1", docs_url=None, redoc_url=None)
    app.state.board_service = board_service
    app.state.local_auth = auth
    app.state.project_root = root
    app.state.project_key = project_key
    app.state.runtime_idle_callback = None
    runtime_clients: dict[str, float] = {}
    runtime_clients_lock = threading.RLock()
    idle_timer: threading.Timer | None = None
    client_watchdog: threading.Timer | None = None
    cookie_suffix = "".join(
        character
        if character.isascii() and (character.isalnum() or character in "-_")
        else "_"
        for character in project_key
    )
    session_cookie_name = f"agentboard_session_{cookie_suffix}"
    csrf_cookie_name = f"agentboard_csrf_{cookie_suffix}"

    def schedule_idle_shutdown() -> None:
        nonlocal idle_timer
        with runtime_clients_lock:
            if runtime_clients or policy.runtime.idle_shutdown_seconds == 0:
                return
            if idle_timer is not None:
                idle_timer.cancel()

            def request_shutdown() -> None:
                with runtime_clients_lock:
                    if runtime_clients:
                        return
                callback = app.state.runtime_idle_callback
                if callback is not None:
                    callback()

            idle_timer = threading.Timer(
                policy.runtime.idle_shutdown_seconds,
                request_shutdown,
            )
            idle_timer.daemon = True
            idle_timer.start()

    def schedule_client_watchdog() -> None:
        nonlocal client_watchdog
        with runtime_clients_lock:
            if not runtime_clients:
                return
            if client_watchdog is not None:
                client_watchdog.cancel()

            def prune_clients() -> None:
                nonlocal client_watchdog
                now = time.monotonic()
                with runtime_clients_lock:
                    expired = [
                        client_id
                        for client_id, expires_at in runtime_clients.items()
                        if expires_at <= now
                    ]
                    for client_id in expired:
                        runtime_clients.pop(client_id, None)
                    remaining = bool(runtime_clients)
                    client_watchdog = None
                if remaining:
                    schedule_client_watchdog()
                else:
                    schedule_idle_shutdown()

            client_watchdog = threading.Timer(
                max(0.25, min(5.0, runtime_client_ttl_seconds / 3)),
                prune_clients,
            )
            client_watchdog.daemon = True
            client_watchdog.start()

    @app.middleware("http")
    async def localhost_security(request: Request, call_next: Any) -> Any:
        host = request.headers.get("host", "")
        parsed_host = urllib.parse.urlsplit(f"http://{host}")
        hostname = parsed_host.hostname
        host_port = parsed_host.port or 80
        allowed = {"127.0.0.1", "localhost", "::1"}
        if testing:
            allowed.add("testserver")
        if hostname not in allowed:
            return JSONResponse(
                {"error": {"code": "INVALID_HOST", "message": "Loopback Host required."}},
                status_code=400,
            )
        origin = request.headers.get("origin")
        if origin:
            parsed_origin = urllib.parse.urlsplit(origin)
            origin_port = parsed_origin.port or 80
            if (
                parsed_origin.scheme != "http"
                or parsed_origin.hostname not in allowed
                or parsed_origin.hostname != hostname
                or origin_port != host_port
            ):
                return JSONResponse(
                    {
                        "error": {
                            "code": "INVALID_ORIGIN",
                            "message": "Loopback Origin required.",
                        }
                    },
                    status_code=403,
                )
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
            "base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
        return JSONResponse(
            {
                "error": {
                    "code": error.code.upper(),
                    "message": str(error),
                    "retryable": isinstance(error, VersionConflictError),
                }
            },
            status_code=_error_status(error),
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(
        _: Request, error: AuthenticationError
    ) -> JSONResponse:
        return JSONResponse(
            {"error": {"code": "UNAUTHORIZED", "message": str(error)}},
            status_code=401,
        )

    def principal(
        request: Request,
        operation: str,
        *,
        task_id: str | None = None,
        run_id: str | None = None,
        lease_generation: int | None = None,
        resource_id: str | None = None,
        write: bool = False,
    ) -> Principal | Actor:
        capability = request.headers.get("x-agentboard-capability")
        if capability:
            if capability.startswith("abhuman_"):
                return auth.human_authorization_principal(
                    capability,
                    operation=operation,
                    resource_id=resource_id or task_id,
                )
            return board_service.authenticate_capability(
                capability,
                operation,
                task_id=task_id,
                run_id=run_id,
                lease_generation=lease_generation,
            )
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            return auth.runtime_principal(authorization.removeprefix("Bearer ").strip())
        session = request.cookies.get(session_cookie_name)
        if session:
            return auth.browser_principal(
                session,
                csrf_token=request.headers.get("x-agentboard-csrf"),
                require_csrf=write,
            )
        raise AuthenticationError("Local AgentBoard authentication is required")

    @app.get("/api/v1/health")
    def health(request: Request) -> dict[str, Any]:
        principal(request, "health")
        return {
            "status": "ok",
            "service": "agentboard",
            "project_key": project_key,
            "schema": SCHEMA_VERSION,
            "hook_protection": hook_protection_status(root),
        }

    @app.post("/api/v1/runtime/attach")
    def runtime_attach(
        request: Request, command: RuntimeClientCommand
    ) -> dict[str, Any]:
        nonlocal idle_timer
        authenticated = principal(request, "runtime_attach", write=True)
        if not isinstance(authenticated, Principal) or authenticated.authentication != "runtime":
            raise AuthenticationError("Runtime lifecycle requires the local runtime credential")
        with runtime_clients_lock:
            if idle_timer is not None:
                idle_timer.cancel()
                idle_timer = None
            runtime_clients[command.client_id] = (
                time.monotonic() + runtime_client_ttl_seconds
            )
            count = len(runtime_clients)
        schedule_client_watchdog()
        return {
            "attached": True,
            "client_count": count,
            "client_ttl_seconds": runtime_client_ttl_seconds,
        }

    @app.post("/api/v1/runtime/heartbeat")
    def runtime_heartbeat(
        request: Request, command: RuntimeClientCommand
    ) -> dict[str, Any]:
        authenticated = principal(request, "runtime_heartbeat", write=True)
        if not isinstance(authenticated, Principal) or authenticated.authentication != "runtime":
            raise AuthenticationError("Runtime lifecycle requires the local runtime credential")
        with runtime_clients_lock:
            if command.client_id not in runtime_clients:
                raise AuthenticationError("Runtime client is not attached or has expired")
            runtime_clients[command.client_id] = (
                time.monotonic() + runtime_client_ttl_seconds
            )
        schedule_client_watchdog()
        return {"alive": True, "client_ttl_seconds": runtime_client_ttl_seconds}

    @app.post("/api/v1/runtime/detach")
    def runtime_detach(
        request: Request, command: RuntimeClientCommand
    ) -> dict[str, Any]:
        nonlocal client_watchdog
        authenticated = principal(request, "runtime_detach", write=True)
        if not isinstance(authenticated, Principal) or authenticated.authentication != "runtime":
            raise AuthenticationError("Runtime lifecycle requires the local runtime credential")
        with runtime_clients_lock:
            runtime_clients.pop(command.client_id, None)
            count = len(runtime_clients)
            if count == 0 and client_watchdog is not None:
                client_watchdog.cancel()
                client_watchdog = None
        if count == 0:
            schedule_idle_shutdown()
        return {
            "detached": True,
            "client_count": count,
            "idle_shutdown_seconds": policy.runtime.idle_shutdown_seconds,
        }

    @app.post("/api/v1/dashboard/bootstrap")
    def dashboard_bootstrap(request: Request) -> dict[str, str]:
        principal(request, "dashboard_open", write=True)
        token = auth.mint_bootstrap()
        return {
            "path": "/bootstrap?"
            + urllib.parse.urlencode({"token": token, "project": project_key})
        }

    @app.get("/bootstrap")
    def consume_bootstrap(token: str, project: str) -> RedirectResponse:
        if project != project_key:
            raise AuthenticationError("Dashboard bootstrap belongs to another project")
        session, csrf = auth.consume_bootstrap(token)
        response = RedirectResponse(
            f"/?{urllib.parse.urlencode({'project': project_key})}", status_code=303
        )
        response.set_cookie(
            session_cookie_name,
            session,
            httponly=True,
            secure=False,
            samesite="strict",
            path="/",
            max_age=auth.session_ttl_seconds,
        )
        response.set_cookie(
            csrf_cookie_name,
            csrf,
            httponly=False,
            secure=False,
            samesite="strict",
            path="/",
            max_age=auth.session_ttl_seconds,
        )
        return response

    @app.post("/api/v1/authorizations")
    def human_authorization(
        request: Request, command: HumanAuthorizationRequest
    ) -> dict[str, Any]:
        human = principal(request, "human_authorization", write=True)
        if "human" not in human.roles:
            raise AuthenticationError("An authenticated dashboard human is required")
        token = auth.mint_human_authorization(
            operation=command.operation,
            resource_id=command.resource_id,
        )
        return {
            "capability_token": token,
            "operation": command.operation,
            "resource_id": command.resource_id,
            "expires_in_seconds": 120,
        }

    @app.get("/api/v1/project", response_model=ProjectOutput)
    def project_open(request: Request) -> dict[str, Any]:
        principal(request, "project_open")
        return {
            "name": policy.project.name,
            "path": str(root),
            "project_key": project_key,
            "wip": {"limit": board_service.wip_limit},
            "hook_protection": hook_protection_status(root),
        }

    @app.get("/api/v1/plans", response_model=list[PlanSummaryOutput])
    def plan_list(request: Request) -> list[dict[str, Any]]:
        principal(request, "plan_get")
        return to_jsonable(board_service.plan_list())

    @app.get("/api/v1/plans/{plan_id}", response_model=PlanOutput)
    def plan_get(
        request: Request, plan_id: str, revision: int | None = None
    ) -> dict[str, Any]:
        principal(request, "plan_get")
        return to_jsonable(board_service.plan_get(plan_id, revision))

    @app.get(
        "/api/v1/plans/{plan_id}/validate",
        response_model=PlanValidationOutput,
    )
    def plan_validate(
        request: Request, plan_id: str, revision: int | None = None
    ) -> dict[str, Any]:
        principal(request, "plan_validate")
        return board_service.plan_validate(plan_id, revision)

    @app.get("/api/v1/tasks", response_model=list[TaskViewOutput])
    def task_list(request: Request, plan_id: str | None = None) -> list[dict[str, Any]]:
        principal(request, "task_list")
        return to_jsonable(board_service.task_list(plan_id))

    @app.get("/api/v1/tasks/{task_id}", response_model=TaskDetailOutput)
    def task_get(request: Request, task_id: str) -> dict[str, Any]:
        principal(request, "task_get")
        return to_jsonable(board_service.task_detail(task_id))

    @app.get("/api/v1/schedule", response_model=list[TaskViewOutput])
    def schedule_next(request: Request, limit: int = 20) -> list[dict[str, Any]]:
        principal(request, "schedule_next")
        return to_jsonable(board_service.schedule_next(limit))

    @app.get("/api/v1/agents", response_model=list[AgentOutput])
    def agent_list(request: Request) -> list[dict[str, Any]]:
        principal(request, "agent_list")
        return to_jsonable(board_service.agent_list())

    @app.get("/api/v1/runs", response_model=list[RunOutput])
    def run_list(
        request: Request,
        task_id: str | None = None,
        status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        principal(request, "run_list")
        return to_jsonable(
            board_service.run_list(task_id=task_id, status=status, limit=limit)
        )

    @app.get("/api/v1/runs/{run_id}", response_model=RunDetailOutput)
    def run_get(request: Request, run_id: str) -> dict[str, Any]:
        principal(request, "run_get")
        return to_jsonable(board_service.run_get(run_id))

    @app.get(
        "/api/v1/config",
        response_model=ConfigRevisionOutput | None,
    )
    def config_get(request: Request) -> dict[str, Any] | None:
        principal(request, "config_get")
        return to_jsonable(board_service.config_get())

    @app.get("/api/v1/config/state", response_model=ConfigStateOutput)
    def config_state(request: Request) -> dict[str, Any]:
        principal(request, "config_get")
        return to_jsonable(board_service.config_state())

    def board_payload() -> dict[str, Any]:
        snapshot = to_jsonable(board_service.board_snapshot())
        tasks = snapshot["tasks"]
        columns: dict[str, list[dict[str, Any]]] = {
            "backlog": [],
            "eligible": [],
            "in_progress": [],
            "verifying": [],
            "done": [],
        }
        for task in tasks:
            phase = task["state"]
            display = task["display_state"]
            task["phase"] = phase
            task["status"] = display
            task["profile"] = task.get("suggested_profile")
            task["acceptance_criteria"] = task.get("acceptance", [])
            if phase == "CANCELED":
                continue
            if phase == "DONE":
                columns["done"].append(task)
            elif phase == "VERIFYING":
                columns["verifying"].append(task)
            elif phase == "IN_PROGRESS":
                columns["in_progress"].append(task)
            elif display == "READY":
                columns["eligible"].append(task)
            else:
                columns["backlog"].append(task)
        plans = to_jsonable(board_service.plan_list())
        active = next((plan for plan in plans if plan["status"] == "ACTIVE"), None)
        agents = to_jsonable(board_service.agent_list())
        runs = to_jsonable(board_service.run_list(limit=200))
        usage_totals = {
            "input_tokens": sum(
                run["input_tokens"] or 0 for run in runs
            ),
            "output_tokens": sum(
                run["output_tokens"] or 0 for run in runs
            ),
            "total_tokens": sum(
                run["total_tokens"] or 0 for run in runs
            ),
            "completed_duration_seconds": sum(
                run["duration_seconds"] or 0 for run in runs
            ),
            "reported_runs": sum(
                run["total_tokens"] is not None for run in runs
            ),
            "completed_runs": sum(
                run["completed_at"] is not None for run in runs
            ),
        }
        by_task = {task["id"]: task for task in tasks}
        stale = [
            by_task[run["task_id"]]
            for run in runs
            if run["status"] == "STALE" and run["task_id"] in by_task
        ]
        return {
            "project": {"name": policy.project.name, "path": str(root)},
            "hook_protection": hook_protection_status(root),
            "plan": active,
            "plans": plans,
            "tasks": tasks,
            "columns": columns,
            "attention": {
                "blocked": [task for task in tasks if task["blocked"]],
                "stale": stale,
                "awaiting_review": columns["verifying"],
                "conflicts": [
                    task
                    for task in tasks
                    if any("conflict" in reason for reason in task.get("reasons", []))
                ],
            },
            "agents": agents,
            "runs": runs,
            "usage_totals": usage_totals,
            "last_event_id": snapshot["last_sequence"],
            "wip": snapshot["wip"],
        }

    @app.get("/api/v1/board", response_model=BoardOutput)
    def board_snapshot(request: Request) -> dict[str, Any]:
        principal(request, "board_snapshot")
        return board_payload()

    @app.get("/api/v1/event-log", response_model=list[EventOutput])
    def event_list(
        request: Request, after_sequence: int = 0, limit: int = 200
    ) -> list[dict[str, Any]]:
        principal(request, "event_list")
        return to_jsonable(board_service.event_list(after_sequence, limit))

    @app.get("/api/v1/events")
    @app.get("/api/v1/events/stream")
    async def event_stream(
        request: Request, last_event_id: int | None = None
    ) -> StreamingResponse:
        principal(request, "event_list")
        header = request.headers.get("last-event-id")
        cursor = last_event_id or (int(header) if header and header.isdigit() else 0)

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            keepalive = 0
            while not await request.is_disconnected():
                events = await asyncio.to_thread(board_service.event_list, cursor, 200)
                if events:
                    for event in events:
                        cursor = event["sequence"]
                        payload = to_jsonable(event)
                        yield (
                            f"id: {cursor}\n"
                            f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
                        )
                    keepalive = 0
                else:
                    keepalive += 1
                    if keepalive >= 20:
                        yield ": keepalive\n\n"
                        keepalive = 0
                await asyncio.sleep(0.5)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.post("/api/v1/plans", response_model=CommandResultOutput)
    def plan_draft_create(
        request: Request, command: PlanDraftCreate
    ) -> dict[str, Any]:
        actor = _actor(principal(request, "plan_draft_create", write=True))
        content = _plan_content(command)
        if content["target_branch"] is None:
            content["target_branch"] = policy.project.default_target_branch
        result = board_service.create_plan_draft(
            command.plan_id,
            command.title,
            content,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=command.idempotency_key,
            revision=command.revision,
            parent_revision=command.parent_revision,
        )
        return to_jsonable(result)

    @app.post(
        "/api/v1/plans/{plan_id}/revisions/{revision}",
        response_model=CommandResultOutput,
    )
    def plan_draft_update(
        request: Request,
        plan_id: str,
        revision: int,
        command: PlanDraftUpdate,
    ) -> dict[str, Any]:
        actor = _actor(principal(request, "plan_draft_update", write=True))
        current = board_service.plan_get(plan_id, revision)
        content = current["content"]
        update = _plan_content(command)
        if command.tasks is not None:
            existing_dependencies = {
                task["id"]: task.get("dependencies", [])
                for task in content.get("tasks", [])
            }
            content["tasks"] = update["tasks"]
            if command.dependencies is None:
                for task in content["tasks"]:
                    task["dependencies"] = existing_dependencies.get(task["id"], [])
        if command.dependencies is not None:
            dependencies_by_task: dict[str, list[dict[str, str]]] = {}
            for dependency in command.dependencies:
                dependencies_by_task.setdefault(
                    dependency.downstream_task_id, []
                ).append(
                    {
                        "task_id": dependency.upstream_task_id,
                        "kind": dependency.type,
                    }
                )
            for task in content["tasks"]:
                task["dependencies"] = dependencies_by_task.get(task["id"], [])
        if command.groups is not None:
            content["groups"] = update["groups"]
        if command.title is not None:
            content["title"] = command.title
        if command.objective is not None:
            content["objective"] = command.objective
        content["impact_reason"] = command.impact_reason
        result = board_service.update_plan_draft(
            plan_id,
            revision,
            content,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=command.idempotency_key,
        )
        return to_jsonable(result)

    @app.post(
        "/api/v1/plans/{plan_id}/revisions/{revision}/approve",
        response_model=CommandResultOutput,
    )
    def plan_approve(
        request: Request, plan_id: str, revision: int, command: Command
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "plan_approve",
                resource_id=_plan_approval_resource(plan_id, revision, command.expected_version),
                write=True,
            )
        )
        return to_jsonable(
            board_service.approve_plan(
                plan_id,
                revision,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/agents", response_model=CommandResultOutput)
    def agent_register(request: Request, command: AgentRegister) -> dict[str, Any]:
        actor = _actor(principal(request, "agent_register", write=True))
        return to_jsonable(
            board_service.register_agent(
                command.agent_id,
                command.profile,
                command.capacity,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/agent-instances", response_model=CommandResultOutput)
    def agent_instance_register(
        request: Request, command: AgentInstanceRegister
    ) -> dict[str, Any]:
        actor = _actor(principal(request, "agent_instance_register", write=True))
        return to_jsonable(
            board_service.register_agent_instance(
                command.instance_id,
                command.agent_id,
                thread_id=command.thread_id,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/tasks/claim", response_model=CommandResultOutput)
    def task_claim(request: Request, command: TaskClaim) -> dict[str, Any]:
        actor = _actor(principal(request, "task_claim", task_id=command.task_id, write=True))
        protection = hook_protection_status(root)
        if policy.git.enabled and not testing and not protection["active"]:
            raise PolicyViolationError(
                "Codex hook protection is not active for this project; "
                "review and trust the AgentBoard plugin hooks before claiming tasks."
            )
        capability = auth.derive_capability(
            idempotency_key=command.idempotency_key,
            operation="task_claim",
            actor_id=command.agent_id,
            task_id=command.task_id,
        )
        result = board_service.claim_task(
            command.task_id,
            command.agent_id,
            instance_id=command.agent_instance_id,
            config_revision=command.config_revision,
            capability_token=capability,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=command.idempotency_key,
        )
        payload = to_jsonable(result)
        payload["data"]["capability_token"] = capability
        return payload

    @app.post("/api/v1/tasks/move-intent")
    def task_move_intent(request: Request, command: TaskMoveIntent) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "task_move_intent",
                task_id=command.task_id,
                write=True,
            )
        )
        return board_service.validate_task_move_intent(
            command.task_id,
            command.target_column,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=command.idempotency_key,
        )

    @app.post(
        "/api/v1/tasks/release-reservation",
        response_model=CommandResultOutput,
    )
    def task_release_reservation(
        request: Request, command: TaskReservationRelease
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "task_release_reservation",
                task_id=command.task_id,
                write=True,
            )
        )
        return to_jsonable(
            board_service.task_release_reservation(
                command.task_id,
                command.assignment_id,
                command.reason,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/runs/start", response_model=CommandResultOutput)
    def run_start(request: Request, command: RunStart) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "run_start",
                task_id=command.task_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.run_start(
                command.task_id,
                command.assignment_id,
                command.lease_generation,
                thread_id=command.codex_thread_id,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/runs/heartbeat", response_model=CommandResultOutput)
    def task_heartbeat(request: Request, command: Heartbeat) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "task_heartbeat",
                task_id=command.task_id,
                run_id=command.run_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        checkpoint = (
            {"kind": "progress", "summary": command.checkpoint}
            if command.checkpoint
            else None
        )
        return to_jsonable(
            board_service.task_heartbeat(
                command.task_id,
                command.run_id,
                command.lease_generation,
                checkpoint=checkpoint,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/runs/result", response_model=CommandResultOutput)
    def task_report_result(request: Request, command: ReportResult) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "task_report_result",
                task_id=command.task_id,
                run_id=command.run_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        evidence = [
            {
                "kind": item.kind,
                "summary": item.summary,
                "payload": {
                    **item.metadata,
                    "reference": item.reference,
                    "passed": item.passed,
                },
            }
            for item in command.evidence
        ]
        return to_jsonable(
            board_service.task_report_result(
                command.task_id,
                command.run_id,
                command.lease_generation,
                evidence,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
                summary=command.summary,
                checkpoint_sha=command.checkpoint_sha,
                usage=(
                    command.usage.model_dump(mode="json")
                    if command.usage is not None
                    else None
                ),
            )
        )

    @app.post("/api/v1/runs/fail", response_model=CommandResultOutput)
    def run_fail(request: Request, command: RunFail) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "run_fail",
                task_id=command.task_id,
                run_id=command.run_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.run_fail(
                command.task_id,
                command.run_id,
                command.lease_generation,
                command.failure_kind,
                command.reason,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
                usage=(
                    command.usage.model_dump(mode="json")
                    if command.usage is not None
                    else None
                ),
            )
        )

    @app.post("/api/v1/tasks/block", response_model=CommandResultOutput)
    def task_block(request: Request, command: TaskBlock) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "task_block",
                task_id=command.task_id,
                run_id=command.run_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.task_block(
                command.task_id,
                command.run_id,
                command.lease_generation,
                command.reason,
                owner=command.owner,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
                usage=(
                    command.usage.model_dump(mode="json")
                    if command.usage is not None
                    else None
                ),
            )
        )

    @app.post("/api/v1/reviews/claim", response_model=CommandResultOutput)
    def review_claim(request: Request, command: ReviewClaim) -> dict[str, Any]:
        actor = _actor(principal(request, "review_claim", task_id=command.task_id, write=True))
        capability = auth.derive_capability(
            idempotency_key=command.idempotency_key,
            operation="review_claim",
            actor_id=command.reviewer_id,
            task_id=command.task_id,
        )
        result = board_service.review_claim(
            command.task_id,
            command.review_id,
            command.reviewer_id,
            reviewer_instance_id=command.reviewer_instance_id,
            capability_token=capability,
            expected_version=command.expected_version,
            actor=actor,
            idempotency_key=command.idempotency_key,
        )
        payload = to_jsonable(result)
        payload["data"]["capability_token"] = capability
        return payload

    @app.post("/api/v1/reviews/start", response_model=CommandResultOutput)
    def review_start(request: Request, command: ReviewStart) -> dict[str, Any]:
        capability = request.headers.get("x-agentboard-capability")
        detail = board_service.task_detail(command.task_id)
        lease = detail.get("lease")
        latest_run = detail.get("run")
        generation = None
        if capability and lease:
            generation = lease.get("generation")
        actor = _actor(
            principal(
                request,
                "review_start",
                task_id=command.task_id,
                run_id=latest_run.get("id") if latest_run else None,
                lease_generation=generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.review_start(
                command.task_id,
                command.review_id,
                thread_id=command.codex_thread_id,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/reviews/decide", response_model=CommandResultOutput)
    def review_decide(request: Request, command: ReviewDecision) -> dict[str, Any]:
        detail = board_service.task_detail(command.task_id)
        generation = detail.get("lease", {}).get("generation") if detail.get("lease") else None
        run_id = detail.get("run", {}).get("id") if detail.get("run") else None
        actor = _actor(
            principal(
                request,
                "review_decide",
                task_id=command.task_id,
                run_id=run_id,
                lease_generation=generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.review_decide(
                command.task_id,
                command.review_id,
                ReviewStatus(command.decision),
                command.summary,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
                evidence=[
                    {
                        "kind": item.kind,
                        "summary": item.summary,
                        "payload": {
                            **item.metadata,
                            "reference": item.reference,
                            "passed": item.passed,
                        },
                    }
                    for item in command.evidence
                ],
            )
        )

    @app.post(
        "/api/v1/reviews/human-approve",
        response_model=CommandResultOutput,
    )
    def review_human_approve(
        request: Request, command: ReviewHumanApproval
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "review_human_approve",
                task_id=command.task_id,
                resource_id=command.review_id,
                write=True,
            )
        )
        return to_jsonable(
            board_service.review_human_approve(
                command.task_id,
                command.review_id,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/tasks/cancel", response_model=CommandResultOutput)
    def task_cancel(request: Request, command: TaskCancel) -> dict[str, Any]:
        actor = _actor(principal(request, "task_cancel", task_id=command.task_id, write=True))
        return to_jsonable(
            board_service.task_cancel(
                command.task_id,
                command.reason,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/dependencies/waive", response_model=CommandResultOutput)
    def dependency_waive(
        request: Request, command: DependencyWaive
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "dependency_waive",
                task_id=command.downstream_task_id,
                write=True,
            )
        )
        from agentboard.domain import DependencyType

        return to_jsonable(
            board_service.dependency_waive(
                command.downstream_task_id,
                command.upstream_task_id,
                DependencyType.REQUIRES,
                command.reason,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/config/drafts", response_model=CommandResultOutput)
    def config_draft_create(
        request: Request, command: ConfigDraftCreate
    ) -> dict[str, Any]:
        actor = _actor(principal(request, "config_draft_create", write=True))
        return to_jsonable(
            board_service.config_draft_create(
                command.yaml_text,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.get(
        "/api/v1/config/drafts/{draft_id}/validate",
        response_model=ConfigValidationOutput,
    )
    def config_validate(request: Request, draft_id: str) -> dict[str, Any]:
        principal(request, "config_validate")
        return board_service.config_validate(draft_id)

    @app.post("/api/v1/config/apply", response_model=CommandResultOutput)
    def config_apply(request: Request, command: ConfigApply) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "config_apply_draft",
                resource_id=command.draft_id,
                write=True,
            )
        )
        return to_jsonable(
            board_service.config_apply_draft(
                command.draft_id,
                command.expected_fingerprint,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/git/checkpoint", response_model=CommandResultOutput)
    def git_checkpoint(
        request: Request, command: GitCheckpointCommand
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "git_checkpoint",
                task_id=command.task_id,
                run_id=command.run_id,
                lease_generation=command.lease_generation,
                write=True,
            )
        )
        return to_jsonable(
            board_service.git_checkpoint(
                command.task_id,
                command.run_id,
                command.lease_generation,
                command.expected_head,
                command.message,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/git/integrate-task", response_model=CommandResultOutput)
    def git_integrate_task(
        request: Request, command: GitTaskIntegrateCommand
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "git_integrate",
                task_id=command.task_id,
                run_id=command.run_id,
                write=True,
            )
        )
        return to_jsonable(
            board_service.git_integrate(
                command.task_id,
                command.run_id,
                command.expected_target_sha,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    @app.post("/api/v1/git/integrate-plan", response_model=CommandResultOutput)
    def git_integrate_plan(
        request: Request, command: GitPlanIntegrateCommand
    ) -> dict[str, Any]:
        actor = _actor(
            principal(
                request,
                "git_integrate_plan",
                resource_id=command.plan_id,
                write=True,
            )
        )
        return to_jsonable(
            board_service.git_integrate_plan(
                command.plan_id,
                command.revision,
                command.target_branch,
                command.expected_target_sha,
                command.expected_source_sha,
                expected_version=command.expected_version,
                actor=actor,
                idempotency_key=command.idempotency_key,
            )
        )

    static_root = Path(__file__).with_name("static")
    source_dist = root / "web" / "dist"
    dashboard_root = static_root if (static_root / "index.html").is_file() else source_dist
    if (dashboard_root / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dashboard_root / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def dashboard(path: str) -> Any:
        if path.startswith("api/"):
            return JSONResponse(
                {"error": {"code": "NOT_FOUND", "message": "API route not found."}},
                status_code=404,
            )
        index = dashboard_root / "index.html"
        if not index.is_file():
            return JSONResponse(
                {
                    "error": {
                        "code": "DASHBOARD_NOT_BUILT",
                        "message": "Run npm run build inside web/.",
                    }
                },
                status_code=503,
            )
        return FileResponse(index)

    def cancel_runtime_timers() -> None:
        nonlocal idle_timer, client_watchdog
        with runtime_clients_lock:
            if idle_timer is not None:
                idle_timer.cancel()
                idle_timer = None
            if client_watchdog is not None:
                client_watchdog.cancel()
                client_watchdog = None

    app.router.add_event_handler("startup", schedule_idle_shutdown)
    app.router.add_event_handler("shutdown", cancel_runtime_timers)
    return app
