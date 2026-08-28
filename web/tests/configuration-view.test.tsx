import assert from "node:assert/strict";
import test from "node:test";
import React from "react";

import "./setup-dom.ts";
import { cleanup, render } from "@testing-library/react";

import { ConfigurationView } from "../src/ConfigurationView.tsx";

test("configuration resumes the latest persisted draft in the editor", async () => {
  const previousFetch = globalThis.fetch;
  globalThis.fetch = async (input) => {
    assert.equal(String(input), "/api/v1/config/state");
    return json({
      active: {
        id: "config-active",
        revision: 1,
        status: "ACTIVE",
        version: 1,
        content_hash: "active-hash",
        content: { project: { name: "Active" } },
        validation: { valid: true },
        actor_id: "human",
        created_at: "2026-01-01T00:00:00+00:00",
        applied_at: "2026-01-01T00:00:00+00:00",
      },
      latest: {
        id: "config-draft",
        revision: 2,
        status: "DRAFT",
        version: 2,
        content_hash: "draft-hash",
        content: { project: { name: "Draft" } },
        validation: {},
        actor_id: "human",
        created_at: "2026-01-02T00:00:00+00:00",
        applied_at: null,
      },
    });
  };

  const view = render(<ConfigurationView onApplied={() => {}} />);
  try {
    const editor = await view.findByLabelText(
      "Conteúdo YAML do draft de configuração",
    ) as HTMLTextAreaElement;
    assert.match(editor.value, /"Draft"/);
    assert.ok(view.getByText("Draft config-draft retomado do servidor. Valide-o antes de aplicar."));
    assert.equal(
      (view.getByRole("button", { name: "Criar draft" }) as HTMLButtonElement).disabled,
      true,
    );
    assert.equal(
      (view.getByRole("button", { name: "Validar no servidor" }) as HTMLButtonElement).disabled,
      false,
    );
  } finally {
    cleanup();
    globalThis.fetch = previousFetch;
  }
});

function json(value: unknown): Response {
  return new Response(JSON.stringify(value), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}
