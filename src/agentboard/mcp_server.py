from __future__ import annotations

from fastmcp import FastMCP


mcp = FastMCP("AgentBoard")


@mcp.tool()
def board_snapshot() -> dict[str, object]:
    """Return a compact local AgentBoard snapshot for the active project."""
    return {
        "project": "uninitialized",
        "tasks": [],
        "message": "Milestone 1 will load the project SQLite state.",
    }


def run() -> None:
    """Run the MCP server over stdio for Codex."""
    mcp.run()

