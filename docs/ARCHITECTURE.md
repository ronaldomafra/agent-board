# Architecture

## Bounded components

| Component | Owns | Must not own |
| --- | --- | --- |
| `domain.py` | States, transitions and domain value objects | SQL, HTTP or MCP transport |
| `service.py` | Transactional use cases and policy evaluation | Browser rendering |
| `storage.py` | SQLite connection and schema lifecycle | Business decisions |
| `mcp_server.py` | MCP schemas and tool registration | Duplicate orchestration rules |
| `web.py` | Local HTTP, SSE and static asset delivery | Direct SQLite writes |
| `web/` | Human-facing interaction | Server authority |

## Runtime topology

One local AgentBoard process owns the HTTP dashboard and is launched by Codex as an MCP stdio child process. It binds dashboard traffic to loopback only. A future persistent daemon mode is optional and must not change core contracts.

## Data consistency

Commands include a task version and idempotency key. The service starts an immediate SQLite transaction, validates policy, updates the aggregate and appends an event. Readers obtain snapshots; the dashboard receives invalidation events through SSE.

