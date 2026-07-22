# AgentBoard

Local-first control plane for AI development agents. AgentBoard combines a Codex plugin, an MCP server, a local dashboard and SQLite-backed task orchestration.

## Status

The repository currently contains the architectural foundation and contracts for the first vertical slice. It is intentionally not yet a published package or production-ready plugin.

## Goals

- Coordinate development tasks across Codex agents.
- Enforce dependencies, WIP limits, task leases and valid state transitions.
- Keep configuration in Git and operational history in local SQLite.
- Expose the same domain rules through MCP tools and a local web dashboard.
- Keep all runtime services bound to localhost and alive only while Codex is active in the MVP.

## Repository map

| Path | Purpose |
| --- | --- |
| `src/agentboard/` | Python domain, storage, service, MCP and web adapters |
| `web/` | React/Vite dashboard source |
| `skills/` | Codex workflow for task orchestration |
| `.codex/agents/` | Project-scoped agent profiles |
| `config/` | Versioned orchestration examples |
| `docs/` | Architecture and development guidance |
| `tests/` | Domain and adapter tests |

## Development

```bash
uv sync --all-groups
uv run pytest
uv run ruff check .
uv run agentboard --help
```

Read [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) before implementing a feature. Codex-specific rules are in [AGENTS.md](AGENTS.md).

## License

Apache-2.0

