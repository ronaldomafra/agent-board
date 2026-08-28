from __future__ import annotations

import re
import threading
from pathlib import Path

import yaml
from fastapi.testclient import TestClient

from agentboard.config import default_config
from agentboard.domain import Actor
from agentboard.service import BoardService
from agentboard.web import create_app

TOKEN = "runtime-token-" + "x" * 48


def bearer(token: str = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def command(version: int, key: str) -> dict[str, object]:
    return {"expected_version": version, "idempotency_key": key}


def test_packaged_dashboard_is_the_current_server_validated_bundle() -> None:
    static_root = Path(__file__).parents[1] / "src" / "agentboard" / "static"
    index = (static_root / "index.html").read_text(encoding="utf-8")
    match = re.search(r'src="(/assets/[^"]+\.js)"', index)
    assert match is not None
    script = static_root / match.group(1).removeprefix("/")
    assert script.is_file()
    bundle = script.read_text(encoding="utf-8")
    assert "/api/v1/tasks/move-intent" in bundle
    assert "Drag é apenas uma intenção" in bundle
    assert "Validar movimento" in bundle
    assert "Totais de uso das execuções exibidas" in bundle
    assert "retomado do servidor" in bundle


def test_plan_partial_update_preserves_and_replaces_dependencies(
    tmp_path: Path,
) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            service=service,
            testing=True,
        )
    )
    task_payloads = [
        {
            "id": task_id,
            "title": task_id,
            "objective": f"Complete {task_id}",
            "scope": ["src"],
            "acceptance": ["accepted"],
            "tests": ["pytest"],
        }
        for task_id in ("AB-UPSTREAM", "AB-DOWNSTREAM")
    ]
    created = client.post(
        "/api/v1/plans",
        headers=bearer(),
        json={
            **command(0, "partial-plan-create"),
            "plan_id": "PLAN-PARTIAL",
            "title": "Before",
            "objective": "Before objective",
            "tasks": task_payloads,
            "dependencies": [
                {
                    "upstream_task_id": "AB-UPSTREAM",
                    "downstream_task_id": "AB-DOWNSTREAM",
                    "type": "REQUIRES",
                }
            ],
        },
    )
    assert created.status_code == 200, created.text

    metadata_update = client.post(
        "/api/v1/plans/PLAN-PARTIAL/revisions/1",
        headers=bearer(),
        json={
            **command(0, "partial-plan-metadata"),
            "title": "After",
            "objective": "After objective",
            "impact_reason": "Clarify the draft",
        },
    )
    assert metadata_update.status_code == 200, metadata_update.text
    plan_after_metadata = client.get(
        "/api/v1/plans/PLAN-PARTIAL",
        headers=bearer(),
    ).json()
    assert plan_after_metadata["title"] == "After"
    assert plan_after_metadata["content"]["objective"] == "After objective"
    assert plan_after_metadata["content"]["tasks"][1]["dependencies"] == [
        {"kind": "REQUIRES", "task_id": "AB-UPSTREAM"}
    ]

    dependency_update = client.post(
        "/api/v1/plans/PLAN-PARTIAL/revisions/1",
        headers=bearer(),
        json={
            **command(1, "partial-plan-dependencies"),
            "dependencies": [],
            "impact_reason": "Remove the ordering constraint",
        },
    )
    assert dependency_update.status_code == 200, dependency_update.text
    plan_after_dependencies = client.get(
        "/api/v1/plans/PLAN-PARTIAL",
        headers=bearer(),
    ).json()
    assert all(
        not task["dependencies"]
        for task in plan_after_dependencies["content"]["tasks"]
    )


def test_plan_create_defaults_target_branch_from_project_policy(
    tmp_path: Path,
) -> None:
    policy = default_config("Default branch project").model_dump(mode="json")
    policy["project"]["default_target_branch"] = "release/local"
    (tmp_path / "agentboard.yaml").write_text(
        yaml.safe_dump(policy, sort_keys=False),
        encoding="utf-8",
    )
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            service=service,
            testing=True,
        )
    )

    created = client.post(
        "/api/v1/plans",
        headers=bearer(),
        json={
            **command(0, "default-target-branch"),
            "plan_id": "PLAN-DEFAULT-BRANCH",
            "title": "Default branch",
            "objective": "Pin the approved target branch",
            "tasks": [
                {
                    "id": "AB-DEFAULT-BRANCH",
                    "title": "Persist policy branch",
                    "objective": "Use the configured default target",
                    "acceptance": ["target branch is persisted"],
                    "tests": ["pytest"],
                }
            ],
        },
    )
    assert created.status_code == 200, created.text

    service.approve_plan(
        "PLAN-DEFAULT-BRANCH",
        1,
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="approve-default-target",
    )
    plan = client.get(
        "/api/v1/plans/PLAN-DEFAULT-BRANCH",
        headers=bearer(),
    ).json()
    assert plan["status"] == "ACTIVE"
    assert plan["content"]["target_branch"] == "release/local"


def test_http_board_move_intent_validates_version_without_transition(
    tmp_path: Path,
) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    service.create_plan_draft(
        "PLAN-DRAG",
        "Drag plan",
        {
            "tasks": [
                {
                    "id": "AB-DRAG",
                    "title": "Drag task",
                    "objective": "Validate drag intent",
                    "acceptance": ["server remains authoritative"],
                    "tests": ["pytest"],
                }
            ]
        },
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="drag-plan-draft",
    )
    service.approve_plan(
        "PLAN-DRAG",
        1,
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="drag-plan-approve",
    )
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            service=service,
            testing=True,
        )
    )

    response = client.post(
        "/api/v1/tasks/move-intent",
        headers=bearer(),
        json={
            **command(0, "drag-http-intent"),
            "task_id": "AB-DRAG",
            "target_column": "in_progress",
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["required_command"] == "task_claim"
    assert response.json()["mutation_performed"] is False
    assert service.task_get("AB-DRAG").task.version == 0

    stale = client.post(
        "/api/v1/tasks/move-intent",
        headers=bearer(),
        json={
            **command(2, "drag-http-stale"),
            "task_id": "AB-DRAG",
            "target_column": "in_progress",
        },
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "VERSION_CONFLICT"

    service.task_cancel(
        "AB-DRAG",
        "No longer required",
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="drag-task-cancel",
    )
    board = client.get("/api/v1/board", headers=bearer()).json()
    assert board["tasks"][0]["state"] == "CANCELED"
    assert all(not column for column in board["columns"].values())


def test_http_flow_derives_actors_and_replays_board_state(tmp_path: Path) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            project_key="test-project",
            service=service,
            testing=True,
        )
    )

    assert client.get("/api/v1/board").status_code == 401
    assert (
        client.get(
            "/api/v1/health",
            headers={**bearer(), "Host": "attacker.invalid"},
        ).status_code
        == 400
    )
    assert client.get("/api/v1/health", headers=bearer()).json()["project_key"] == "test-project"
    assert (
        client.get(
            "/api/v1/health",
            headers={
                **bearer(),
                "Origin": "http://testserver:9999",
            },
        ).status_code
        == 403
    )

    draft_payload = {
        **command(0, "draft-key"),
        "plan_id": "PLAN-HTTP",
        "revision": 1,
        "title": "HTTP plan",
        "objective": "Exercise the adapter",
        "tasks": [
            {
                "id": "AB-HTTP",
                "title": "HTTP task",
                "objective": "Complete through API",
                "scope": ["src"],
                "acceptance": ["result is reviewed"],
                "tests": ["pytest"],
                "priority": "P1",
                "risk": "MEDIUM",
                "profile": "worker",
                "paths": ["src/**"],
            }
        ],
        "dependencies": [],
    }
    draft = client.post("/api/v1/plans", headers=bearer(), json=draft_payload)
    assert draft.status_code == 200, draft.text
    rejected = client.post(
        "/api/v1/plans/PLAN-HTTP/revisions/1/approve",
        headers=bearer(),
        json=command(0, "approve-denied"),
    )
    assert rejected.status_code == 403

    bootstrap = client.post(
        "/api/v1/dashboard/bootstrap", headers=bearer(), json={}
    ).json()["path"]
    assert client.get(bootstrap, follow_redirects=False).status_code == 303
    csrf = client.cookies.get("agentboard_csrf_test-project")
    authorization = client.post(
        "/api/v1/authorizations",
        headers={"X-AgentBoard-CSRF": csrf},
        json={
            "operation": "plan_approve",
            "resource_id": "plan:PLAN-HTTP:revision:1:version:0",
        },
    )
    assert authorization.status_code == 200, authorization.text
    human_capability = authorization.json()["capability_token"]
    approved = client.post(
        "/api/v1/plans/PLAN-HTTP/revisions/1/approve",
        headers={"X-AgentBoard-Capability": human_capability},
        json=command(0, "approve-human"),
    )
    assert approved.status_code == 200, approved.text

    registered = client.post(
        "/api/v1/agents",
        headers=bearer(),
        json={
            **command(0, "agent-register"),
            "agent_id": "worker-http",
            "profile": "worker",
            "capacity": 1,
        },
    )
    assert registered.status_code == 200, registered.text
    claim = client.post(
        "/api/v1/tasks/claim",
        headers=bearer(),
        json={
            **command(0, "claim-http"),
            "task_id": "AB-HTTP",
            "agent_id": "worker-http",
        },
    )
    assert claim.status_code == 200, claim.text
    claim_data = claim.json()
    worker_capability = claim_data["data"]["capability_token"]
    generation = claim_data["data"]["lease_generation"]

    started = client.post(
        "/api/v1/runs/start",
        headers={"X-AgentBoard-Capability": worker_capability},
        json={
            **command(claim_data["version"], "run-start-http"),
            "task_id": "AB-HTTP",
            "assignment_id": claim_data["data"]["assignment_id"],
            "lease_generation": generation,
            "codex_thread_id": "thread-http",
        },
    )
    assert started.status_code == 200, started.text
    run = started.json()
    fenced = client.post(
        "/api/v1/runs/heartbeat",
        headers={"X-AgentBoard-Capability": worker_capability},
        json={
            **command(run["version"], "heartbeat-old-generation"),
            "task_id": "AB-HTTP",
            "run_id": run["data"]["run_id"],
            "lease_generation": generation + 1,
        },
    )
    assert fenced.status_code in {403, 409}

    result = client.post(
        "/api/v1/runs/result",
        headers={"X-AgentBoard-Capability": worker_capability},
        json={
            **command(run["version"], "result-http"),
            "task_id": "AB-HTTP",
            "run_id": run["data"]["run_id"],
            "lease_generation": generation,
            "summary": "complete",
            "evidence": [
                {
                    "kind": "changed_files",
                    "summary": "src adapter updated",
                    "passed": True,
                },
                {
                    "kind": "tests",
                    "summary": "pytest passed",
                    "passed": True,
                },
                {
                    "kind": "acceptance",
                    "summary": "result is reviewed",
                    "passed": True,
                },
            ],
        },
    )
    assert result.status_code == 200, result.text
    verification = result.json()
    review_claim = client.post(
        "/api/v1/reviews/claim",
        headers=bearer(),
        json={
            **command(verification["version"], "review-claim-http"),
            "task_id": "AB-HTTP",
            "review_id": verification["data"]["review_id"],
            "reviewer_id": "codex:orchestrator",
        },
    )
    assert review_claim.status_code == 200, review_claim.text
    review_capability = review_claim.json()["data"]["capability_token"]
    review_start = client.post(
        "/api/v1/reviews/start",
        headers={"X-AgentBoard-Capability": review_capability},
        json={
            **command(review_claim.json()["version"], "review-start-http"),
            "task_id": "AB-HTTP",
            "review_id": verification["data"]["review_id"],
            "codex_thread_id": "thread-review-http",
        },
    )
    assert review_start.status_code == 200, review_start.text
    decision = client.post(
        "/api/v1/reviews/decide",
        headers={"X-AgentBoard-Capability": review_capability},
        json={
            **command(review_start.json()["version"], "review-http"),
            "task_id": "AB-HTTP",
            "review_id": verification["data"]["review_id"],
            "decision": "APPROVED",
            "summary": "accepted",
        },
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["state"] == "DONE"

    board = client.get("/api/v1/board").json()
    assert [task["id"] for task in board["columns"]["done"]] == ["AB-HTTP"]
    assert board["last_event_id"] > 0


def test_write_schema_rejects_caller_supplied_actor(tmp_path: Path) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            service=service,
            testing=True,
        )
    )
    response = client.post(
        "/api/v1/agents",
        headers=bearer(),
        json={
            **command(0, "actor-forge"),
            "agent_id": "worker",
            "profile": "worker",
            "capacity": 1,
            "actor": {"id": "forged", "roles": ["human"]},
        },
    )

    assert response.status_code == 422


def test_claim_replay_after_runtime_restart_returns_a_usable_capability(
    tmp_path: Path,
) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    service.create_plan_draft(
        "PLAN-REPLAY",
        "Replay plan",
        {
            "tasks": [
                {
                    "id": "AB-REPLAY",
                    "title": "Replay claim",
                    "objective": "Keep a capability stable across restarts",
                    "acceptance": ["replay is accepted"],
                    "tests": ["pytest"],
                }
            ]
        },
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="replay-plan-draft",
    )
    service.approve_plan(
        "PLAN-REPLAY",
        1,
        expected_version=0,
        actor=Actor("human", frozenset({"human"})),
        idempotency_key="replay-plan-approve",
    )
    service.register_agent(
        "worker-replay",
        "worker",
        1,
        expected_version=0,
        actor=Actor("orchestrator", frozenset({"orchestrator"})),
        idempotency_key="replay-agent-register",
    )
    capability_secret = "stable-capability-secret-" * 2
    first_runtime = TestClient(
        create_app(
            project_root=tmp_path,
            api_token="a" * 64,
            capability_secret=capability_secret,
            service=service,
            testing=True,
        )
    )
    first_claim = first_runtime.post(
        "/api/v1/tasks/claim",
        headers=bearer("a" * 64),
        json={
            **command(0, "replay-claim-key"),
            "task_id": "AB-REPLAY",
            "agent_id": "worker-replay",
        },
    )
    assert first_claim.status_code == 200, first_claim.text

    restarted_runtime = TestClient(
        create_app(
            project_root=tmp_path,
            api_token="b" * 64,
            capability_secret=capability_secret,
            service=service,
            testing=True,
        )
    )
    replay = restarted_runtime.post(
        "/api/v1/tasks/claim",
        headers=bearer("b" * 64),
        json={
            **command(0, "replay-claim-key"),
            "task_id": "AB-REPLAY",
            "agent_id": "worker-replay",
        },
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True
    assert replay.json()["data"]["capability_token"] == first_claim.json()["data"][
        "capability_token"
    ]

    started = restarted_runtime.post(
        "/api/v1/runs/start",
        headers={"X-AgentBoard-Capability": replay.json()["data"]["capability_token"]},
        json={
            **command(replay.json()["version"], "replay-run-start"),
            "task_id": "AB-REPLAY",
            "assignment_id": replay.json()["data"]["assignment_id"],
            "lease_generation": replay.json()["data"]["lease_generation"],
            "codex_thread_id": "replay-thread",
        },
    )
    assert started.status_code == 200, started.text


def test_dashboard_sessions_are_namespaced_by_project_key(tmp_path: Path) -> None:
    first = TestClient(
        create_app(
            project_root=tmp_path / "first",
            api_token="a" * 64,
            project_key="project-one",
            testing=True,
        )
    )
    second = TestClient(
        create_app(
            project_root=tmp_path / "second",
            api_token="b" * 64,
            project_key="project-two",
            testing=True,
        )
    )

    first_bootstrap = first.post(
        "/api/v1/dashboard/bootstrap", headers=bearer("a" * 64), json={}
    ).json()["path"]
    assert first.get(first_bootstrap, follow_redirects=False).status_code == 303
    second.cookies.update(first.cookies)
    second_bootstrap = second.post(
        "/api/v1/dashboard/bootstrap", headers=bearer("b" * 64), json={}
    ).json()["path"]
    assert second.get(second_bootstrap, follow_redirects=False).status_code == 303

    assert second.cookies.get("agentboard_session_project-one") is not None
    assert second.cookies.get("agentboard_csrf_project-one") is not None
    assert second.cookies.get("agentboard_session_project-two") is not None
    assert second.cookies.get("agentboard_csrf_project-two") is not None
    assert second.get("/api/v1/board").status_code == 200


def test_runtime_lifecycle_requires_runtime_credential(tmp_path: Path) -> None:
    service = BoardService(tmp_path / ".agentboard" / "state.db")
    client = TestClient(
        create_app(
            project_root=tmp_path,
            api_token=TOKEN,
            service=service,
            testing=True,
        )
    )
    client_id = "mcp-client-for-lifecycle-test"

    attached = client.post(
        "/api/v1/runtime/attach",
        headers=bearer(),
        json={"client_id": client_id},
    )
    assert attached.json()["attached"] is True
    assert attached.json()["client_count"] == 1

    detached = client.post(
        "/api/v1/runtime/detach",
        headers=bearer(),
        json={"client_id": client_id},
    )
    assert detached.status_code == 200
    assert detached.json()["client_count"] == 0
    assert (
        client.post(
            "/api/v1/runtime/attach",
            json={"client_id": client_id},
        ).status_code
        == 401
    )


def test_dead_runtime_client_expires_and_allows_idle_shutdown(
    tmp_path: Path,
) -> None:
    payload = default_config("Liveness test").model_dump(mode="json")
    payload["runtime"]["idle_shutdown_seconds"] = 1
    (tmp_path / "agentboard.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    app = create_app(
        project_root=tmp_path,
        api_token=TOKEN,
        testing=True,
        runtime_client_ttl_seconds=1,
    )
    shutdown_requested = threading.Event()
    app.state.runtime_idle_callback = shutdown_requested.set
    client = TestClient(app)

    attached = client.post(
        "/api/v1/runtime/attach",
        headers=bearer(),
        json={"client_id": "mcp-client-that-dies-without-detach"},
    )
    assert attached.status_code == 200
    assert shutdown_requested.wait(timeout=4)
