import assert from "node:assert/strict";
import test from "node:test";
import React from "react";

import "./setup-dom.ts";
import { cleanup, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { PlansView } from "../src/PlansView.tsx";

test("a valid draft requires confirmation before the dashboard approves it", async () => {
  const previousFetch = globalThis.fetch;
  const previousCookie = document.cookie;
  document.cookie = "agentboard_csrf_local-project=csrf-value";
  const requests: Array<{ path: string; body?: Record<string, unknown> }> = [];
  let approved = 0;
  globalThis.fetch = async (input, init) => {
    const path = String(input);
    const body = init?.body
      ? (JSON.parse(String(init.body)) as Record<string, unknown>)
      : undefined;
    requests.push({ path, body });
    if (path === "/api/v1/plans") {
      return json([
        {
          id: "PLAN-1",
          title: "Draft plan",
          revision: 2,
          status: "DRAFT",
          version: 4,
        },
      ]);
    }
    if (path.includes("/validate")) {
      return json({ valid: true, plan_id: "PLAN-1", revision: 2, task_count: 1 });
    }
    if (path.startsWith("/api/v1/plans/PLAN-1?")) {
      return json({
        id: "PLAN-1",
        title: "Draft plan",
        revision: 2,
        status: "DRAFT",
        version: 4,
        content: {
          objective: "Approve only after review",
          tasks: [{ id: "AB-1", title: "Task", priority: "P1" }],
        },
      });
    }
    if (path === "/api/v1/authorizations") {
      return json({
        capability_token: "abhuman_test",
        operation: "plan_approve",
        resource_id: body?.resource_id,
        expires_in_seconds: 120,
      });
    }
    if (path === "/api/v1/plans/PLAN-1/revisions/2/approve") {
      return json({ entity_id: "PLAN-1", version: 5, state: "ACTIVE", replayed: false, data: {} });
    }
    throw new Error(`Unexpected request: ${path}`);
  };

  const view = render(<PlansView onApproved={() => { approved += 1; }} />);
  const user = userEvent.setup({ document: globalThis.document });
  try {
    const approveButton = await view.findByRole("button", { name: "Autorizar e aprovar" });
    assert.equal((approveButton as HTMLButtonElement).disabled, true);

    await user.click(
      view.getByLabelText("Revisei esta revisão e autorizo sua aprovação."),
    );
    assert.equal((approveButton as HTMLButtonElement).disabled, false);
    await user.click(approveButton);

    await waitFor(() => assert.equal(approved, 1));
    const authorization = requests.find((request) => request.path === "/api/v1/authorizations");
    assert.deepEqual(authorization?.body, {
      operation: "plan_approve",
      resource_id: "plan:PLAN-1:revision:2:version:4",
    });
    const approval = requests.find(
      (request) => request.path === "/api/v1/plans/PLAN-1/revisions/2/approve",
    );
    assert.equal(approval?.body?.expected_version, 4);
    assert.equal(typeof approval?.body?.idempotency_key, "string");
  } finally {
    cleanup();
    globalThis.fetch = previousFetch;
    document.cookie = previousCookie;
  }
});

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
