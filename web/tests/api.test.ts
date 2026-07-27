import assert from "node:assert/strict";
import test from "node:test";

import {
  getBoard,
  validateTaskMoveIntent,
} from "../src/api.ts";

test("board snapshots receive stable empty projections", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async () =>
    new Response(JSON.stringify({ project: { name: "Local" } }), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  try {
    const snapshot = await getBoard();
    assert.equal(snapshot.project.name, "Local");
    assert.deepEqual(snapshot.columns, {
      backlog: [],
      eligible: [],
      in_progress: [],
      verifying: [],
      done: [],
    });
    assert.deepEqual(snapshot.attention.blocked, []);
    assert.deepEqual(snapshot.tasks, []);
  } finally {
    globalThis.fetch = previousFetch;
  }
});

test("drag sends a versioned server intent and never mutates locally", async () => {
  const previousFetch = globalThis.fetch;
  const previousDocument = globalThis.document;
  Object.defineProperty(globalThis, "document", {
    configurable: true,
    value: { cookie: "agentboard_csrf=csrf-value" },
  });
  let requestBody: Record<string, unknown> | undefined;
  let requestHeaders: HeadersInit | undefined;
  globalThis.fetch = async (_input, init) => {
    requestBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
    requestHeaders = init?.headers;
    return new Response(
      JSON.stringify({
        task_id: "AB-1",
        version: 7,
        source_column: "eligible",
        target_column: "in_progress",
        accepted: true,
        mutation_performed: false,
        required_command: "task_claim",
        message: "Select an eligible agent and claim the task.",
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  };
  try {
    const result = await validateTaskMoveIntent(
      { id: "AB-1", version: 7 },
      "in_progress",
      "dashboard:drag:stable-key",
    );
    assert.equal(result.mutation_performed, false);
    assert.equal(result.required_command, "task_claim");
    assert.deepEqual(requestBody, {
      task_id: "AB-1",
      target_column: "in_progress",
      expected_version: 7,
      idempotency_key: "dashboard:drag:stable-key",
    });
    assert.equal(
      (requestHeaders as Record<string, string>)["X-AgentBoard-CSRF"],
      "csrf-value",
    );
  } finally {
    globalThis.fetch = previousFetch;
    Object.defineProperty(globalThis, "document", {
      configurable: true,
      value: previousDocument,
    });
  }
});
