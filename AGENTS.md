# AgentBoard development rules

## Mission

Build AgentBoard as a local-first, Codex-oriented control plane for software-development agents. Preserve a strict separation between declarative project configuration, operational state and UI presentation.

## Read first

1. `DEVELOPMENT_GUIDE.md` for architecture and milestones.
2. `docs/ARCHITECTURE.md` for boundaries and data ownership.
3. `config/orchestration.example.yaml` for the configuration contract.
4. The relevant custom agent profile under `.codex/agents/` before delegating work.

## Non-negotiable invariants

- Only the domain service may transition task state, acquire a lease or evaluate WIP.
- MCP tools and HTTP endpoints are adapters; they must never implement business rules independently.
- Every state-changing operation requires an actor, an idempotency key and an expected task version.
- SQLite is operational state only. Pipeline, agent and project policy remain versioned files in Git.
- Bind the dashboard/API to `127.0.0.1`; never expose it on a public interface by default.
- Do not make network calls or invoke remote integrations from a task transition without explicit user authorization.
- A task in `DONE` requires recorded evidence; a task in `IN_PROGRESS` requires a valid lease.

## Engineering workflow

1. Inspect the affected contract and add or update a focused test first when behavior changes.
2. Make the smallest complete change that preserves the boundaries above.
3. Run `uv run pytest` and `uv run ruff check .` for Python changes.
4. Run `npm run lint` and `npm run build` inside `web/` for dashboard changes.
5. Report changed files, validation, acceptance evidence and risks.

## Multi-agent policy

- Delegate read-heavy research, test design and isolated UI work only when their file ownership does not overlap.
- Give each worker a task ID, base SHA, explicit file lease and acceptance criteria.
- The orchestrator reviews evidence and decides final state transitions.
- Do not run multiple write agents against `src/agentboard/domain.py`, `src/agentboard/service.py` or migrations concurrently.

## Security and privacy

- Never commit databases, runtime files, tokens, credentials or local dashboard URLs containing secrets.
- Keep tool outputs concise; redact sensitive values from events and logs.
- Treat configuration editing as a draft-and-validate workflow. Applying a draft must be explicit and auditable.

