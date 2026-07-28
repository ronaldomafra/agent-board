from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agentboard")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("mcp", help="Run the thin MCP bridge over stdio.")

    codex_hook = subcommands.add_parser(
        "codex-hook",
        help="Run the Codex lifecycle hook installed with AgentBoard.",
    )
    hook_mode = codex_hook.add_mutually_exclusive_group()
    hook_mode.add_argument("--activate", action="store_true")
    hook_mode.add_argument("--self-test", action="store_true")

    runtime = subcommands.add_parser("runtime", help="Own the local project runtime.")
    runtime.add_argument("--project", type=Path, default=Path.cwd())
    runtime.add_argument("--port", type=int, default=0)

    serve = subcommands.add_parser("serve", help="Alias for the local runtime.")
    serve.add_argument("--project", type=Path, default=Path.cwd())
    serve.add_argument("--port", type=int, default=0)

    dashboard = subcommands.add_parser(
        "dashboard", help="Open a one-use authenticated dashboard URL."
    )
    dashboard.add_argument("--project", type=Path, default=Path.cwd())
    dashboard.add_argument("--no-browser", action="store_true")

    status = subcommands.add_parser("status", help="Inspect the local runtime.")
    status.add_argument("--project", type=Path, default=Path.cwd())

    init = subcommands.add_parser("init", help="Create an agentboard.yaml draft.")
    init.add_argument("--project", type=Path, default=Path.cwd())
    init.add_argument("--name")

    validate = subcommands.add_parser(
        "validate-config", help="Validate agentboard.yaml without applying it."
    )
    validate.add_argument("--project", type=Path, default=Path.cwd())
    return parser


def _runtime(project: Path, port: int) -> int:
    import uvicorn

    from agentboard.domain import Actor, DomainError
    from agentboard.runtime import (
        RuntimeAlreadyRunningError,
        RuntimeLock,
        RuntimeMetadata,
        project_identity,
        remove_metadata,
        write_metadata,
    )
    from agentboard.web import create_app

    identity = project_identity(project)
    nonce = secrets.token_urlsafe(24)
    lock = RuntimeLock(identity, nonce)
    try:
        lock.acquire()
    except RuntimeAlreadyRunningError as exc:
        print(str(exc), file=sys.stderr)
        return 0
    api_token = secrets.token_urlsafe(48)
    selected_port = port
    if not selected_port:
        from agentboard.runtime import _free_loopback_port

        selected_port = _free_loopback_port()
    metadata = RuntimeMetadata(
        schema_version=1,
        project_key=identity.key,
        project_root=str(identity.root),
        pid=os.getpid(),
        nonce=nonce,
        port=selected_port,
        api_token=api_token,
        started_at=time.time(),
    )
    sweeper_stop = threading.Event()
    sweeper_thread: threading.Thread | None = None

    try:
        write_metadata(identity, metadata)
        app = create_app(
            project_root=identity.root,
            api_token=api_token,
            project_key=identity.key,
        )
        server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=selected_port,
                log_level="warning",
                access_log=False,
            )
        )
        app.state.runtime_idle_callback = lambda: setattr(server, "should_exit", True)

        def sweep_stale_assignments() -> None:
            sweeper = Actor("runtime:lease-sweeper", frozenset({"orchestrator"}))
            while not sweeper_stop.wait(1.0):
                for candidate in app.state.board_service.stale_candidates():
                    try:
                        app.state.board_service.expire_stale_assignment(
                            candidate["task_id"],
                            candidate["assignment_id"],
                            expected_version=candidate["version"],
                            actor=sweeper,
                            idempotency_key=(
                                f"stale:{candidate['assignment_id']}:"
                                f"{candidate['lease_generation']}"
                            ),
                        )
                    except DomainError:
                        # A concurrent heartbeat, result or explicit release won the race.
                        continue

        sweeper_thread = threading.Thread(
            target=sweep_stale_assignments,
            name="agentboard-lease-sweeper",
            daemon=True,
        )
        sweeper_thread.start()
        server.run()
    finally:
        sweeper_stop.set()
        if sweeper_thread is not None:
            sweeper_thread.join(timeout=5)
        remove_metadata(identity, nonce)
        lock.release()
    return 0


def _dashboard(project: Path, no_browser: bool) -> int:
    from agentboard.client import RuntimeClient
    from agentboard.runtime import ensure_runtime

    runtime = ensure_runtime(project)
    response = RuntimeClient(runtime).post("/api/v1/dashboard/bootstrap", {})
    url = f"{runtime.base_url}{response['path']}"
    if not no_browser:
        webbrowser.open(url, new=2)
    print(url)
    return 0


def _status(project: Path) -> int:
    from agentboard.runtime import discover_runtime

    runtime = discover_runtime(project)
    if runtime is None:
        print(json.dumps({"status": "stopped"}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "running",
                "project_key": runtime.project_key,
                "project_root": runtime.project_root,
                "pid": runtime.pid,
                "port": runtime.port,
            },
            indent=2,
        )
    )
    return 0


def _init(project: Path, name: str | None) -> int:
    import yaml

    from agentboard.config import default_config, find_project_root

    root = find_project_root(project)
    target = root / "agentboard.yaml"
    if target.exists():
        print(f"Configuration already exists: {target}", file=sys.stderr)
        return 2
    config = default_config(name or root.name)
    target.write_text(
        yaml.safe_dump(
            config.model_dump(mode="json"),
            sort_keys=False,
            allow_unicode=True,
        ),
        encoding="utf-8",
        newline="\n",
    )
    print(target)
    return 0


def _validate_config(project: Path) -> int:
    from agentboard.config import find_project_root, load_config

    root = find_project_root(project)
    config = load_config(root / "agentboard.yaml")
    print(
        json.dumps(
            {
                "valid": True,
                "project": config.project.name,
                "bind": config.runtime.bind_host,
                "local_git_only": config.git.local_only,
            }
        )
    )
    return 0


def main() -> None:
    args = _parser().parse_args()
    if args.command == "codex-hook":
        from agentboard.codex_hook import run as run_codex_hook

        raise SystemExit(
            run_codex_hook(
                activate=args.activate,
                self_test=args.self_test,
            )
        )
    if args.command == "mcp":
        from agentboard.mcp_server import run as run_mcp

        run_mcp()
        return
    if args.command in {"runtime", "serve"}:
        raise SystemExit(_runtime(args.project, args.port))
    if args.command == "dashboard":
        raise SystemExit(_dashboard(args.project, args.no_browser))
    if args.command == "status":
        raise SystemExit(_status(args.project))
    if args.command == "init":
        raise SystemExit(_init(args.project, args.name))
    raise SystemExit(_validate_config(args.project))


if __name__ == "__main__":
    main()
