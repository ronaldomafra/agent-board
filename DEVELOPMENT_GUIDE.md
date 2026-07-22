# AgentBoard development guide

## 1. Product definition

AgentBoard is a local-first control plane for AI development agents. A Codex plugin supplies orchestration guidance and MCP configuration. A Python process exposes MCP over stdio and a localhost dashboard over HTTP. The dashboard exists while the Codex session is active in the MVP; SQLite preserves operational state between sessions.

## 2. Product boundaries

### In scope

- Task board, dependencies, WIP, leases, agent heartbeats and execution evidence.
- Project-local SQLite persistence and Git-versioned configuration.
- MCP tools for Codex and an HTTP/SSE dashboard for humans.
- Optional Git/worktree and GitHub adapters after the core flow is stable.

### Out of scope for v1

- A cloud control plane, multi-tenant accounts, public dashboard hosting and remote databases.
- Replacing issue trackers such as GitHub Projects, Jira or Linear.
- Autonomous merging, deployment or destructive source-control operations.

## 3. Architecture

```text
Codex / subagents --stdio MCP--> MCP adapter --+
                                             +--> domain service --> SQLite
Dashboard --------HTTP + SSE--> web adapter --+
Configuration files ---------------------------> validation + domain policy
```

The Python `domain` and `service` layers own state-machine rules. `mcp_server` and `web` are thin adapters. React renders data and submits commands to HTTP; it never accesses SQLite directly.

## 4. State model

Normal flow:

`BACKLOG -> READY -> ASSIGNED -> IN_PROGRESS -> VERIFYING -> DONE`

Exception states: `BLOCKED`, `REWORK`, `CANCELED`.

`deviation` is a computed alert, not a task state. Examples: expired lease, WIP violation, missing dependency, invalid transition, stale heartbeat or missing evidence.

## 5. Persistence ownership

| Data | Canonical location | Notes |
| --- | --- | --- |
| Pipelines, limits and agent profiles | Git-versioned YAML/TOML | Human-reviewed and portable |
| Tasks, dependencies and task versions | SQLite | Operational snapshot per project |
| Leases, heartbeats and runs | SQLite | Ephemeral but auditable |
| State transitions and evidence | SQLite event log | Append-only audit trail |
| Dashboard state | Browser | Never canonical |

Use SQLite WAL mode. Every state-changing operation runs in one transaction and checks `expected_version`. Persist an `idempotency_key` to make retries safe.

## 6. MCP tool contract

Prioritize workflow tools instead of raw table CRUD:

- `project_open`, `board_snapshot`, `task_get`, `task_list`
- `task_claim`, `task_heartbeat`, `task_transition`, `task_block`
- `task_report_result`, `run_start`, `run_finish`
- `config_get`, `config_validate`, `config_apply_draft`, `dashboard_open`

Read tools are safe. Write tools must use explicit schemas, actionable errors and accurate MCP safety annotations. `task_claim` validates dependency resolution, WIP and an exclusive lease atomically.

## 7. Dashboard

The dashboard is a React/Vite SPA served by the Python process in production. Development uses Vite separately with a proxy to the local API.

Required views:

1. Kanban board: columns, WIP, drag intent and computed attention rail.
2. Task detail: dependency graph, lease, run history, evidence and events.
3. Agents: profile, health, current task and latest heartbeat.
4. Executions: timeline, tool actions, tests and failures.
5. Configuration: draft editor, validation result and diff before apply.

Use SSE for server-to-browser events in v1. Drag-and-drop requests a transition; the server remains the authority.

## 8. Incremental delivery plan

### Milestone 0 — Foundation

- Package setup, plugin manifest, project rules, domain types and test harness.
- Acceptance: imports work; plugin/skill manifests validate; domain transition tests pass.

### Milestone 1 — Core state and SQLite

- Schema, migrations, task CRUD through service, transitions, WIP and lease semantics.
- Acceptance: concurrent claim and stale-version tests pass; event log is produced.

### Milestone 2 — MCP vertical slice

- `project_open`, `board_snapshot`, `task_claim`, `task_heartbeat`, `task_transition` and result reporting.
- Acceptance: an MCP client can complete one task flow without direct database access.

### Milestone 3 — Dashboard vertical slice

- API, SSE, board and task-detail views using the same domain service.
- Acceptance: a state change from MCP appears in the browser without refresh.

### Milestone 4 — Configuration and agent profiles

- YAML/TOML validation, draft application, agent discovery and audit events.
- Acceptance: invalid configuration never changes runtime policy.

### Milestone 5 — Integrations and distribution

- Git/worktree adapter, optional GitHub evidence adapter, packaging, documentation and evaluation scenarios.
- Acceptance: plugin install and local project bootstrap work from a clean environment.

## 9. Quality gates

- Unit tests cover transitions, WIP, dependency checks, lease expiry and idempotency.
- Integration tests cover MCP and HTTP adapters against a temporary database.
- UI tests cover rendering, live updates, keyboard access and forbidden transitions.
- Run formatting/linting, type checks, tests and web build before merging.
- Any schema migration includes upgrade and rollback reasoning.

## 10. Decisions requiring explicit approval

- Adding cloud synchronization, authentication, telemetry or public network listeners.
- Changing the task state graph or relaxing evidence/lease rules.
- Executing Git commands that write, create pull requests, merge or deploy.
- Replacing SQLite with a server database.

