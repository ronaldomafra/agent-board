from __future__ import annotations

import argparse

import uvicorn

from agentboard.mcp_server import run as run_mcp
from agentboard.web import create_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="agentboard")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("mcp", help="Run the MCP server over stdio.")
    serve = subcommands.add_parser("serve", help="Run the local dashboard API.")
    serve.add_argument("--port", type=int, default=43127)
    args = parser.parse_args()

    if args.command == "mcp":
        run_mcp()
    else:
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port)

