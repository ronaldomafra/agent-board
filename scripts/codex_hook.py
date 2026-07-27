"""Defense-in-depth guard for AgentBoard shell commands.

The domain service and Git adapter are the security boundary. This hook only rejects common
attempts to bypass the local-only workflow and deliberately emits no command contents.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path, PurePath
from typing import Any

BLOCKED_GIT_SUBCOMMANDS = {
    "clone",
    "fetch",
    "lfs",
    "ls-remote",
    "pull",
    "push",
    "reset",
    "stash",
    "submodule",
}
READ_ONLY_GIT_SUBCOMMANDS = {
    "blame",
    "cat-file",
    "describe",
    "diff",
    "diff-index",
    "diff-tree",
    "for-each-ref",
    "grep",
    "help",
    "log",
    "ls-files",
    "merge-base",
    "name-rev",
    "rev-parse",
    "shortlog",
    "show",
    "show-ref",
    "status",
    "version",
}
SHELL_SEPARATORS = re.compile(r"(?:&&|\|\||[;&|\r\n])")
TOKEN_PATTERN = re.compile(r"""(?:"[^"]*"|'[^']*'|[^\s]+)""")
GIT_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--exec-path",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}


def _command_from(payload: dict[str, Any]) -> str:
    tool_input = payload.get("tool_input") or payload.get("toolInput") or payload.get("input") or {}
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "cmd", "script"):
        value = tool_input.get(key)
        if isinstance(value, str):
            return value
    return ""


def _tokens(segment: str) -> list[str]:
    return [token.strip("\"'") for token in TOKEN_PATTERN.findall(segment)]


def _executable(token: str) -> str:
    return PurePath(token.replace("\\", "/")).name.lower()


def _git_subcommand(tokens: list[str], git_index: int) -> str | None:
    index = git_index + 1
    while index < len(tokens):
        token = tokens[index]
        if token == "--":
            index += 1
            break
        if token in GIT_OPTIONS_WITH_VALUE:
            index += 2
            continue
        if token.startswith("-c") and token != "-c":
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        break
    if index >= len(tokens):
        return None
    return tokens[index].lower()


def blocked_reason(command: str) -> str | None:
    for segment in SHELL_SEPARATORS.split(command):
        tokens = _tokens(segment)
        for index, token in enumerate(tokens):
            executable = _executable(token)
            if executable in {"gh", "gh.exe"}:
                return "GitHub CLI is disabled by AgentBoard local-only policy"
            if executable not in {"git", "git.exe"}:
                continue
            subcommand = _git_subcommand(tokens, index)
            if subcommand in BLOCKED_GIT_SUBCOMMANDS:
                return f"git {subcommand} is disabled by AgentBoard local-only policy"
            if subcommand is not None and subcommand not in READ_ONLY_GIT_SUBCOMMANDS:
                return (
                    f"git {subcommand} write/bypass is disabled; "
                    "use the AgentBoard MCP workflow"
                )
    return None


def decision(payload: dict[str, Any]) -> dict[str, Any] | None:
    reason = blocked_reason(_command_from(payload))
    if reason is None:
        return None
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        },
    }


def _project_root(payload: dict[str, Any]) -> Path | None:
    raw = payload.get("cwd")
    if not isinstance(raw, str) or not raw:
        return None
    current = Path(raw).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "agentboard.yaml").is_file() or (candidate / ".git").exists():
            return candidate
    return None


def record_activation(payload: dict[str, Any]) -> Path | None:
    root = _project_root(payload)
    if root is None:
        return None
    runtime = root / ".agentboard"
    runtime.mkdir(parents=True, exist_ok=True)
    marker = runtime / "hook-protection.json"
    temporary = marker.with_suffix(".tmp")
    script_hash = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    document = {
        "schema_version": 1,
        "activated_at": datetime.now(UTC).isoformat(),
        "session_id": payload.get("session_id"),
        "plugin_root": os.environ.get("PLUGIN_ROOT"),
        "script_hash": script_hash,
    }
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(document, stream, sort_keys=True, separators=(",", ":"))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, marker)
    return marker


def _self_test() -> int:
    cases = {
        "git status --short": False,
        "git diff --stat": False,
        "git branch agentboard/plan/demo": True,
        "git worktree add work task": True,
        "git commit -m checkpoint": True,
        "git add src/app.py": True,
        "git checkout -b bypass": True,
        "git switch -c bypass": True,
        "git merge topic": True,
        "git rebase main": True,
        "git cherry-pick HEAD~1": True,
        "git tag v1": True,
        "git config user.name bypass": True,
        "git update-ref refs/heads/bypass HEAD": True,
        "git push origin main": True,
        "git -C repo fetch origin": True,
        "git clone https://example.invalid/repo": True,
        "git ls-remote origin": True,
        "git reset --hard HEAD": True,
        "git stash push": True,
        "git lfs pull": True,
        "git submodule update --init": True,
        "git remote update": True,
        "git remote -v": True,
        "gh pr create": True,
        "Get-Date; git pull": True,
    }
    failures = [
        command
        for command, expected_blocked in cases.items()
        if (blocked_reason(command) is not None) != expected_blocked
    ]
    if failures:
        print(json.dumps({"ok": False, "failed_cases": failures}))
        return 1
    print(json.dumps({"ok": True, "cases": len(cases)}))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--self-test":
        return _self_test()
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        payload = {}
    try:
        record_activation(payload)
    except OSError:
        # Activation is defense in depth. Git-enabled claims remain blocked by
        # the service when a valid marker could not be recorded.
        pass
    if len(sys.argv) > 1 and sys.argv[1] == "--activate":
        return 0
    result = decision(payload)
    if result is not None:
        print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
