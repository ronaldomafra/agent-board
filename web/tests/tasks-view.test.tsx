import assert from "node:assert/strict";
import test from "node:test";
import React, { useState } from "react";

import "./setup-dom.ts";
import { cleanup, render, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { BoardSnapshot, Task } from "../src/api.ts";
import { App } from "../src/App.tsx";
import {
  TasksView,
  type TaskFilters,
} from "../src/TasksView.tsx";

const snapshot: BoardSnapshot = {
  project: { name: "Local" },
  tasks: [
    {
      id: "AB-CRITICAL",
      title: "Critical migration",
      status: "READY",
      suggested_profile: "worker",
      risk: "CRITICAL",
      priority: "P0",
    },
    {
      id: "AB-CANCELED",
      title: "Archived task",
      status: "CANCELED",
      suggested_profile: "reviewer",
      risk: "LOW",
      priority: "P3",
    },
  ],
  columns: {
    backlog: [],
    eligible: [
      {
        id: "AB-CRITICAL",
        title: "Critical migration",
        status: "READY",
        suggested_profile: "worker",
        risk: "CRITICAL",
        priority: "P0",
      },
    ],
    in_progress: [],
    verifying: [],
    done: [],
  },
  attention: {
    blocked: [{ id: "AB-CRITICAL", title: "Critical migration" }],
    stale: [],
    awaiting_review: [],
    conflicts: [],
  },
  agents: [],
  runs: [],
  wip: { active: 1, limit: 3 },
};

const initialFilters: TaskFilters = {
  text: "",
  status: "all",
  profile: "all",
  risk: "all",
  attention: "all",
};

test("Tasks view renders authoritative rows and applies accessible filters", async () => {
  let openedTask: Task | undefined;

  function Harness() {
    const [filters, setFilters] = useState(initialFilters);
    return (
      <TasksView
        snapshot={snapshot}
        filters={filters}
        onFiltersChange={setFilters}
        onOpenTask={(task) => {
          openedTask = task;
        }}
        focusToken={1}
      />
    );
  }

  const view = render(<Harness />);
  const user = userEvent.setup({ document: globalThis.document });

  try {
    const heading = view.getByRole("heading", { name: "Tasks" });
    await waitFor(() => assert.equal(document.activeElement, heading));

    assert.ok(view.getByText("Critical migration"));
    assert.ok(view.getByText("Archived task"));

    await user.selectOptions(view.getByLabelText("Risco"), "CRITICAL");
    assert.ok(view.getByText("Critical migration"));
    assert.equal(view.queryByText("Archived task"), null);

    await user.click(view.getByRole("button", { name: "Limpar (1)" }));
    await user.selectOptions(view.getByLabelText("Status"), "canceled");
    assert.ok(view.getByText("Archived task"));
    assert.equal(view.queryByText("Critical migration"), null);

    await user.click(view.getByRole("button", { name: "Limpar (1)" }));
    await user.selectOptions(view.getByLabelText("Atenção"), "blocked");
    assert.ok(view.getByText("Critical migration"));
    assert.equal(view.queryByText("Archived task"), null);

    await user.click(
      view.getByRole("button", {
        name: "Abrir detalhes de Critical migration",
      }),
    );
    assert.equal(openedTask?.id, "AB-CRITICAL");
  } finally {
    cleanup();
  }
});

test("dashboard metrics and attention cards navigate to focused task filters", async () => {
  const previousFetch = globalThis.fetch;
  const previousEventSource = globalThis.EventSource;

  class FakeEventSource {
    onopen: EventSource["onopen"] = null;
    onmessage: EventSource["onmessage"] = null;
    onerror: EventSource["onerror"] = null;

    close() {}
  }

  globalThis.fetch = async () =>
    new Response(JSON.stringify(snapshot), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  Object.defineProperty(globalThis, "EventSource", {
    configurable: true,
    value: FakeEventSource,
  });

  const view = render(<App />);
  const user = userEvent.setup({ document: globalThis.document });

  try {
    const wipMetric = await view.findByRole("button", {
      name: "Filtrar 1 tasks em WIP",
    });
    await user.click(wipMetric);

    const heading = await view.findByRole("heading", { name: "Tasks" });
    await waitFor(() => assert.equal(document.activeElement, heading));
    assert.equal(
      (view.getByLabelText("Status") as HTMLSelectElement).value,
      "wip",
    );

    await user.click(view.getByRole("button", { name: "Quadro" }));
    await user.click(
      view.getByRole("button", {
        name: "Filtrar 1 task em Bloqueadas",
      }),
    );
    assert.equal(
      (view.getByLabelText("Atenção") as HTMLSelectElement).value,
      "blocked",
    );
  } finally {
    cleanup();
    globalThis.fetch = previousFetch;
    Object.defineProperty(globalThis, "EventSource", {
      configurable: true,
      value: previousEventSource,
    });
  }
});
