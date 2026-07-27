from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest
from fastmcp import Client
from pydantic import ValidationError

from agentboard import mcp_server
from agentboard.schemas import (
    PlanDraftCreate,
    PlanDraftUpdate,
    ReportResult,
    ReviewDecision,
)

mcp = mcp_server.mcp


READ_ONLY_TOOLS = {
    "project_open",
    "plan_get",
    "board_snapshot",
    "task_get",
    "task_list",
    "schedule_next",
    "agent_list",
    "run_get",
    "run_list",
    "event_list",
    "config_get",
    "plan_validate",
    "config_validate",
}
ADDITIVE_WRITE_TOOLS = {
    "plan_draft_create",
    "plan_revision_create",
    "agent_instance_register",
    "config_draft_create",
    "git_checkpoint",
}
NON_IDEMPOTENT_WRITE_TOOLS = {"dashboard_open"}


def _tools_by_name() -> dict[str, Any]:
    return {tool.name: tool for tool in asyncio.run(mcp.list_tools())}


async def _wire_tools_by_name() -> dict[str, Any]:
    async with Client(mcp) as client:
        return {tool.name: tool for tool in await client.list_tools()}


def _array_item_schema(property_schema: dict[str, Any]) -> dict[str, Any]:
    candidates = [property_schema, *property_schema.get("anyOf", [])]
    array_schema = next(candidate for candidate in candidates if candidate.get("type") == "array")
    return array_schema["items"]


@dataclass
class RecordingClient:
    calls: list[tuple[str, dict[str, Any], str | None]] = field(default_factory=list)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        capability_token: str | None = None,
    ) -> dict[str, Any]:
        self.calls.append((path, payload, capability_token))
        return {
            "entity_id": path,
            "version": 0,
            "state": "RECORDED",
            "replayed": False,
            "data": {},
        }


def test_workflow_tool_contract_is_complete_and_never_accepts_actor_text() -> None:
    by_name = _tools_by_name()
    required = {
        "project_open",
        "plan_get",
        "board_snapshot",
        "task_get",
        "task_list",
        "schedule_next",
        "agent_list",
        "run_get",
        "run_list",
        "event_list",
        "config_get",
        "plan_draft_create",
        "plan_draft_update",
        "plan_validate",
        "plan_approve",
        "plan_revision_create",
        "plan_revision_approve",
        "task_claim",
        "task_release_reservation",
        "run_start",
        "task_heartbeat",
        "task_block",
        "task_report_result",
        "run_fail",
        "review_claim",
        "review_start",
        "review_decide",
        "task_cancel",
        "dependency_waive",
        "config_draft_create",
        "config_validate",
        "config_apply_draft",
        "git_checkpoint",
        "git_integrate",
        "dashboard_open",
    }

    assert required <= by_name.keys()
    for tool in by_name.values():
        assert "actor" not in tool.parameters.get("properties", {})


def test_every_tool_declares_complete_closed_world_safety_annotations() -> None:
    by_name = _tools_by_name()
    destructive_writes = (
        by_name.keys()
        - READ_ONLY_TOOLS
        - ADDITIVE_WRITE_TOOLS
        - NON_IDEMPOTENT_WRITE_TOOLS
    )

    for name, tool in by_name.items():
        assert tool.annotations is not None, name
        actual = (
            tool.annotations.readOnlyHint,
            tool.annotations.destructiveHint,
            tool.annotations.idempotentHint,
            tool.annotations.openWorldHint,
        )
        if name in READ_ONLY_TOOLS:
            expected = (True, False, True, False)
        elif name in ADDITIVE_WRITE_TOOLS:
            expected = (False, False, True, False)
        elif name in NON_IDEMPOTENT_WRITE_TOOLS:
            expected = (False, False, False, False)
        else:
            assert name in destructive_writes
            expected = (False, True, True, False)
        assert actual == expected, name


def test_every_tool_declares_a_concrete_closed_output_schema() -> None:
    by_name = asyncio.run(_wire_tools_by_name())

    assert len(by_name) == 39
    for name, tool in by_name.items():
        schema = tool.outputSchema
        assert schema["type"] == "object", name
        assert schema["additionalProperties"] is False, name
        assert schema["properties"], name

    command_schema = by_name["task_claim"].outputSchema
    assert set(command_schema["properties"]) == {
        "entity_id",
        "version",
        "state",
        "replayed",
        "data",
    }
    assert by_name["project_open"].outputSchema["required"] == [
        "name",
        "path",
        "project_key",
        "wip",
        "hook_protection",
    ]


def test_mcp_adapter_rejects_malformed_http_output(monkeypatch: Any) -> None:
    class MalformedClient:
        def get(
            self,
            path: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            return {"unexpected": f"{path}:{params}"}

        def post(
            self,
            path: str,
            payload: dict[str, Any],
            *,
            capability_token: str | None = None,
        ) -> dict[str, Any]:
            return {
                "unexpected": f"{path}:{payload}:{capability_token}",
            }

    monkeypatch.setattr(mcp_server, "_attached_client", MalformedClient())

    with pytest.raises(ValidationError, match="name"):
        mcp_server.project_open()
    with pytest.raises(ValidationError, match="entity_id"):
        mcp_server.agent_register(
            "worker-1",
            "worker",
            expected_version=0,
            idempotency_key="malformed-output",
        )


def test_mcp_input_schemas_match_http_constraints_and_nested_dtos() -> None:
    by_name = _tools_by_name()

    idempotent_writes = (
        by_name.keys() - READ_ONLY_TOOLS - NON_IDEMPOTENT_WRITE_TOOLS
    )
    for name in idempotent_writes:
        properties = by_name[name].parameters["properties"]
        assert properties["expected_version"]["minimum"] == 0, name
        assert properties["idempotency_key"]["minLength"] == 8, name
        assert properties["idempotency_key"]["maxLength"] == 200, name

    for name in ("run_start", "task_heartbeat", "task_block", "task_report_result", "run_fail"):
        lease_generation = by_name[name].parameters["properties"]["lease_generation"]
        assert lease_generation["minimum"] == 1, name

    plan_create = by_name["plan_draft_create"].parameters["properties"]
    assert plan_create["tasks"]["default"] == []
    assert plan_create["dependencies"]["default"] == []
    assert plan_create["groups"]["default"] == []
    task_schema = _array_item_schema(plan_create["tasks"])
    dependency_schema = _array_item_schema(plan_create["dependencies"])
    group_schema = _array_item_schema(plan_create["groups"])
    assert task_schema["additionalProperties"] is False
    assert task_schema["properties"]["priority"]["enum"] == ["P0", "P1", "P2", "P3"]
    assert task_schema["properties"]["risk"]["enum"] == [
        "LOW",
        "MEDIUM",
        "HIGH",
        "CRITICAL",
    ]
    assert dependency_schema["additionalProperties"] is False
    assert dependency_schema["properties"]["type"]["enum"] == ["REQUIRES", "ORDER_AFTER"]
    assert group_schema["additionalProperties"] is False
    assert group_schema["properties"]["kind"]["enum"] == ["milestone", "gate", "feature"]

    for name in ("task_report_result", "review_decide"):
        evidence_schema = _array_item_schema(
            by_name[name].parameters["properties"]["evidence"]
        )
        assert evidence_schema["additionalProperties"] is False, name
        assert evidence_schema["properties"]["kind"]["minLength"] == 1, name
        assert evidence_schema["properties"]["summary"]["maxLength"] == 4000, name

    assert by_name["run_fail"].parameters["properties"]["failure_kind"]["enum"] == [
        "TRANSIENT",
        "LOGICAL",
        "TEST",
        "CONFLICT",
        "AUTHORIZATION",
        "CANCELED",
    ]
    assert (
        by_name["run_fail"].parameters["properties"]["failure_kind"]["default"]
        == "LOGICAL"
    )
    assert by_name["agent_register"].parameters["properties"]["capacity"] == {
        "default": 1,
        "maximum": 32,
        "minimum": 1,
        "type": "integer",
    }
    assert by_name["review_decide"].parameters["properties"]["decision"]["enum"] == [
        "APPROVED",
        "CHANGES_REQUESTED",
    ]


def test_nested_mcp_dtos_serialize_to_http_compatible_payloads(monkeypatch: Any) -> None:
    client = RecordingClient()
    monkeypatch.setattr(mcp_server, "_attached_client", client)

    asyncio.run(
        mcp.call_tool(
            "plan_draft_create",
            {
                "plan_id": "PLAN-1",
                "title": "Plan",
                "objective": "Ship safely",
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Implement",
                        "objective": "Keep schemas aligned",
                        "acceptance": ["Contract test passes"],
                        "tests": ["Run focused tests"],
                        "priority": "P1",
                        "risk": "HIGH",
                        "profile": "worker",
                        "paths": ["src/agentboard/mcp_server.py"],
                    }
                ],
                "dependencies": [
                    {
                        "upstream_task_id": "TASK-0",
                        "downstream_task_id": "TASK-1",
                        "type": "REQUIRES",
                    }
                ],
                "groups": [
                    {
                        "id": "G-1",
                        "kind": "feature",
                        "title": "MCP contract",
                        "position": 0,
                    }
                ],
                "expected_version": 0,
                "idempotency_key": "plan-create-1",
            },
            run_middleware=False,
        )
    )
    asyncio.run(
        mcp.call_tool(
            "plan_draft_update",
            {
                "plan_id": "PLAN-1",
                "revision": 1,
                "impact_reason": "Clarify contract scope",
                "tasks": [
                    {
                        "id": "TASK-1",
                        "title": "Implement",
                        "objective": "Keep schemas aligned",
                    }
                ],
                "dependencies": [],
                "groups": [
                    {
                        "id": "G-1",
                        "kind": "feature",
                        "title": "MCP contract",
                    }
                ],
                "expected_version": 1,
                "idempotency_key": "plan-update-1",
            },
            run_middleware=False,
        )
    )
    asyncio.run(
        mcp.call_tool(
            "plan_revision_create",
            {
                "plan_id": "PLAN-1",
                "revision": 2,
                "parent_revision": 1,
                "title": "Plan revision",
                "objective": "Ship the revised contract",
                "tasks": [
                    {
                        "id": "TASK-2",
                        "title": "Verify",
                        "objective": "Verify schemas",
                    }
                ],
                "dependencies": [],
                "groups": [],
                "expected_version": 0,
                "idempotency_key": "plan-revision-1",
            },
            run_middleware=False,
        )
    )
    asyncio.run(
        mcp.call_tool(
            "task_report_result",
            {
                "task_id": "TASK-1",
                "run_id": "RUN-1",
                "lease_generation": 1,
                "summary": "Implemented",
                "evidence": [
                    {
                        "kind": "test",
                        "summary": "Focused contract tests passed",
                        "reference": "tests/test_mcp_contract.py",
                        "passed": True,
                    }
                ],
                "expected_version": 2,
                "idempotency_key": "report-result-1",
                "capability_token": "run-capability",
            },
            run_middleware=False,
        )
    )
    asyncio.run(
        mcp.call_tool(
            "review_decide",
            {
                "task_id": "TASK-1",
                "review_id": "REVIEW-1",
                "decision": "APPROVED",
                "summary": "Contract is aligned",
                "evidence": [
                    {
                        "kind": "review",
                        "summary": "Schemas inspected",
                    }
                ],
                "expected_version": 3,
                "idempotency_key": "review-decide-1",
                "capability_token": "review-capability",
            },
            run_middleware=False,
        )
    )

    plan_path, plan_payload, _ = client.calls[0]
    update_path, update_payload, _ = client.calls[1]
    revision_path, revision_payload, _ = client.calls[2]
    result_path, result_payload, result_capability = client.calls[3]
    review_path, review_payload, review_capability = client.calls[4]
    assert plan_path == "/api/v1/plans"
    assert update_path == "/api/v1/plans/PLAN-1/revisions/1"
    assert revision_path == "/api/v1/plans"
    assert result_path == "/api/v1/runs/result"
    assert review_path == "/api/v1/reviews/decide"
    assert result_capability == "run-capability"
    assert review_capability == "review-capability"
    PlanDraftCreate.model_validate(plan_payload)
    PlanDraftUpdate.model_validate(update_payload)
    PlanDraftCreate.model_validate(revision_payload)
    ReportResult.model_validate(result_payload)
    ReviewDecision.model_validate(review_payload)
    assert result_payload["evidence"] == [
        {
            "kind": "test",
            "summary": "Focused contract tests passed",
            "reference": "tests/test_mcp_contract.py",
            "passed": True,
            "metadata": {},
        }
    ]
    assert plan_payload["tasks"][0]["acceptance_criteria"] == [
        "Contract test passes"
    ]
    assert plan_payload["tasks"][0]["test_plan"] == ["Run focused tests"]
