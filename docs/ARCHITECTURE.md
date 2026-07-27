# Architecture

## Bounded components

| Component | Owns | Must not own |
| --- | --- | --- |
| `domain.py` | Canonical states and domain value objects | SQL, HTTP or MCP transport |
| `service.py` | Transitions, policy, WIP, leases and transactional use cases | Browser rendering or caller-supplied identity |
| `storage.py` | SQLite connections, numbered migrations and local backups | Business decisions |
| `config.py` | Strict validation of Git-versioned project policy | Operational task state |
| `git_local.py` | Narrow local-only branches, worktrees, checkpoints and integrations | Remote commands or task transitions |
| `runtime.py` | Project identity, singleton discovery, lock and protected metadata | Domain policy |
| `auth.py` | Runtime, browser, CSRF and scoped capability authentication | State transitions |
| `mcp_server.py` | Workflow tool registration and loopback client calls | Duplicate orchestration rules |
| `web.py` | Authenticated HTTP/SSE adapters and static asset delivery | Direct SQLite writes |
| `web/` | Human-facing projections and explicit command intent | Canonical state |

## Runtime topology

```text
Codex / subagents --stdio MCP--> thin MCP bridge --authenticated loopback--+
Dashboard ----------------------HTTP/SSE----------------------------------+--> project runtime
                                                                            +--> domain service
                                                                            +--> SQLite
Versioned YAML/TOML ------------------------------------------------------+--> policy validation
```

Exactly one `agentboard runtime` owns the project database, HTTP API, SSE stream and dashboard.
`agentboard mcp` discovers or starts that runtime, attaches for the lifetime of the stdio client and
detaches when it exits. With zero attached clients—including a runtime that never received its
first attachmentâ€”the runtime observes the configured idle grace period and shuts down. Project
identity combines the real project path with the Git common directory.

The runtime binds only to `127.0.0.1`. Lock, PID, nonce, port and bearer token are stored with
restricted permissions below `.agentboard/`; they are operational secrets and never versioned.
Dashboard access starts with a one-use bootstrap and becomes an `HttpOnly`/`SameSite` session.
Browser writes require CSRF, while agent commands use hashed, scoped, expiring capabilities.

## State and authority

Persisted task phases are `BACKLOG`, `IN_PROGRESS`, `VERIFYING`, `DONE` and `CANCELED`.
`READY`, `ASSIGNED`, `BLOCKED` and `REWORK` are calculated projections or records, not extra task
phases. An `IN_PROGRESS` task requires an accepted assignment, active run and current lease.
`VERIFYING` retains WIP and its lease. `DONE` requires evidence, review and local integration when
the evidence profile requires Git. Canceling a task or expiring its verification lease marks any
pending review `ABANDONED`, records the reason and releases agent-instance capacity.

Only `BoardService` may transition a task, acquire/release a lease or evaluate WIP. HTTP and MCP
derive actors from credentials and pass authenticated `Actor` values inward; they never accept an
actor or role from a command payload. Every mutation carries an idempotency key and expected
aggregate version.

## Data ownership

| Data | Canonical owner |
| --- | --- |
| Pipeline, limits, review policy and project settings | `agentboard.yaml` in Git |
| Agent profiles | `.codex/agents/*.toml` in Git |
| Plans, tasks, assignments, leases, runs, reviews and evidence | Project-local SQLite |
| Transition audit and SSE replay cursor | Append-only SQLite event log |
| Dashboard selection and filters | Browser only |
| Runtime metadata, worktrees and database backups | Ignored `.agentboard/` |

Readers obtain one SQLite snapshot containing task projections and the corresponding event cursor.
SSE `/api/v1/events` resumes from `Last-Event-ID`; clients reload `/api/v1/board` after each
invalidation.

## Local Git boundary

The Git adapter exposes no remote operation. It disables hooks and prompts, rejects external
filters, drivers, includes, LFS and submodules, isolates inherited `GIT_*` configuration, restricts
checkpoint paths to the active lease and requires expected SHAs. Plan integration requires the
approved target branch, pinned reviewed source SHA, clean target and explicit human authorization.
It never performs automatic stash or destructive reset.

Hooks supplied by the plugin are defense in depth. The authoritative guarantees remain the domain
service, scoped capabilities and the narrow local Git adapter.
