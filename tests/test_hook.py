from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentboard.hook_guard import hook_protection_status

CODEX_HOOK_PATH = Path(__file__).parents[1] / "scripts" / "codex_hook.py"
CODEX_HOOK_SPEC = importlib.util.spec_from_file_location(
    "agentboard_codex_hook",
    CODEX_HOOK_PATH,
)
assert CODEX_HOOK_SPEC is not None
assert CODEX_HOOK_SPEC.loader is not None
codex_hook = importlib.util.module_from_spec(CODEX_HOOK_SPEC)
CODEX_HOOK_SPEC.loader.exec_module(codex_hook)


def run_hook(
    project: Path,
    command: str,
) -> subprocess.CompletedProcess[str]:
    plugin_root = Path(__file__).parents[1]
    environment = os.environ.copy()
    environment["PLUGIN_ROOT"] = str(plugin_root)
    payload = {
        "session_id": "hook-test-session",
        "cwd": str(project),
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": command},
    }
    return subprocess.run(
        [sys.executable, str(plugin_root / "scripts" / "codex_hook.py")],
        cwd=project,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def test_installed_hook_uses_plugin_root_and_emits_supported_denial(
    tmp_path: Path,
) -> None:
    (tmp_path / ".git").mkdir()
    hook_config = json.loads(
        (Path(__file__).parents[1] / "hooks" / "hooks.json").read_text(
            encoding="utf-8"
        )
    )
    handlers = hook_config["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "$PLUGIN_ROOT" in handlers["command"]
    assert "%PLUGIN_ROOT%" in handlers["commandWindows"]

    blocked = run_hook(tmp_path, "git commit -m bypass")

    assert blocked.returncode == 0
    output = json.loads(blocked.stdout)
    assert set(output) == {"hookSpecificOutput"}
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "continue" not in output
    assert "stopReason" not in output
    assert hook_protection_status(tmp_path)["active"] is True

    allowed = run_hook(tmp_path, "git status --short")
    assert allowed.returncode == 0
    assert allowed.stdout == ""


def test_hook_protection_rejects_a_marker_after_installed_script_changes(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    plugin_copy = tmp_path / "plugin"
    (plugin_copy / "scripts").mkdir(parents=True)
    shutil.copy2(
        Path(__file__).parents[1] / "scripts" / "codex_hook.py",
        plugin_copy / "scripts" / "codex_hook.py",
    )
    environment = os.environ.copy()
    environment["PLUGIN_ROOT"] = str(plugin_copy)
    payload = {
        "session_id": "hook-test-session",
        "cwd": str(project),
        "hook_event_name": "SessionStart",
    }
    subprocess.run(
        [sys.executable, str(plugin_copy / "scripts" / "codex_hook.py"), "--activate"],
        cwd=project,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=True,
    )
    assert hook_protection_status(project)["active"] is True

    with (plugin_copy / "scripts" / "codex_hook.py").open("a", encoding="utf-8") as stream:
        stream.write("\n# changed after activation\n")

    assert hook_protection_status(project) == {
        "active": False,
        "reason": "hook_script_changed",
    }


def test_session_start_survives_unwritable_activation_marker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "session_id": "hook-test-session",
        "cwd": "C:/project",
        "hook_event_name": "SessionStart",
    }

    def fail_activation(_payload: dict[str, object]) -> None:
        raise PermissionError("activation marker is not writable")

    monkeypatch.setattr(codex_hook, "record_activation", fail_activation)
    monkeypatch.setattr(sys, "argv", ["codex_hook.py", "--activate"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.main() == 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_pre_tool_use_still_denies_when_activation_marker_is_unwritable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = {
        "session_id": "hook-test-session",
        "cwd": "C:/project",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "git push origin main"},
    }

    def fail_activation(_payload: dict[str, object]) -> None:
        raise PermissionError("activation marker is not writable")

    monkeypatch.setattr(codex_hook, "record_activation", fail_activation)
    monkeypatch.setattr(sys, "argv", ["codex_hook.py"])
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.main() == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert captured.err == ""
