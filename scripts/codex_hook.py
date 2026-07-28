"""Compatibility wrapper for the installed AgentBoard Codex hook."""

from __future__ import annotations

from agentboard.codex_hook import main

if __name__ == "__main__":
    raise SystemExit(main())
