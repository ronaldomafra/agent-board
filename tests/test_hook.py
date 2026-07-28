from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agentboard import codex_hook, hook_guard

PROJECT_ROOT = Path(__file__).parents[1]
HOOK_CONFIG_PATH = PROJECT_ROOT / "hooks" / "hooks.json"


def _project(path: Path) -> Path:
    path.mkdir()
    (path / "agentboard.yaml").write_text("schema_version: 1\n", encoding="utf-8")
    return path


def run_hook(
    project: Path,
    command: str,
    *,
    plugin_root: Path = PROJECT_ROOT,
) -> subprocess.CompletedProcess[str]:
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
        [sys.executable, "-m", "agentboard.cli", "codex-hook"],
        cwd=project,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
        check=False,
    )


def test_installed_hook_uses_agentboard_cli_and_emits_supported_denial(
    tmp_path: Path,
) -> None:
    project = _project(tmp_path / "project")
    hook_config = json.loads(HOOK_CONFIG_PATH.read_text(encoding="utf-8"))
    session_handler = hook_config["hooks"]["SessionStart"][0]["hooks"][0]
    pre_tool_handler = hook_config["hooks"]["PreToolUse"][0]["hooks"][0]
    assert session_handler["command"] == "agentboard codex-hook --activate"
    assert session_handler["commandWindows"] == "agentboard codex-hook --activate"
    assert pre_tool_handler["command"] == "agentboard codex-hook"
    assert pre_tool_handler["commandWindows"] == "agentboard codex-hook"

    blocked = run_hook(project, "git commit -m bypass")

    assert blocked.returncode == 0
    output = json.loads(blocked.stdout)
    assert set(output) == {"hookSpecificOutput"}
    assert output["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "continue" not in output
    assert "stopReason" not in output
    protection = hook_guard.hook_protection_status(project)
    assert protection["active"] is True
    marker = json.loads(
        (project / ".agentboard" / "hook-protection.json").read_text(encoding="utf-8")
    )
    assert marker["schema_version"] == 2
    assert marker["handler_path"] == str(Path(codex_hook.__file__).resolve())
    assert len(marker["script_hash"]) == 64
    assert len(marker["hook_config_hash"]) == 64

    allowed = run_hook(project, "git status --short")
    assert allowed.returncode == 0
    assert allowed.stdout == ""


def test_hook_protection_rejects_changed_installed_handler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path / "project")
    plugin_copy = tmp_path / "plugin"
    (plugin_copy / "hooks").mkdir(parents=True)
    shutil.copy2(HOOK_CONFIG_PATH, plugin_copy / "hooks" / "hooks.json")
    handler_copy = tmp_path / "codex_hook.py"
    shutil.copy2(Path(codex_hook.__file__), handler_copy)
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_copy))
    monkeypatch.setattr(codex_hook, "_handler_path", lambda: handler_copy)
    monkeypatch.setattr(hook_guard, "_handler_path", lambda: handler_copy)

    codex_hook.record_activation(
        {
            "session_id": "hook-test-session",
            "cwd": str(project),
            "hook_event_name": "SessionStart",
        }
    )
    assert hook_guard.hook_protection_status(project)["active"] is True

    with handler_copy.open("a", encoding="utf-8") as stream:
        stream.write("\n# changed after activation\n")

    assert hook_guard.hook_protection_status(project) == {
        "active": False,
        "reason": "hook_script_changed",
    }


def test_hook_protection_rejects_changed_hook_configuration(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _project(tmp_path / "project")
    plugin_copy = tmp_path / "plugin"
    (plugin_copy / "hooks").mkdir(parents=True)
    copied_config = plugin_copy / "hooks" / "hooks.json"
    shutil.copy2(HOOK_CONFIG_PATH, copied_config)
    monkeypatch.setenv("PLUGIN_ROOT", str(plugin_copy))

    codex_hook.record_activation(
        {
            "session_id": "hook-test-session",
            "cwd": str(project),
            "hook_event_name": "SessionStart",
        }
    )
    assert hook_guard.hook_protection_status(project)["active"] is True

    copied_config.write_text("{}\n", encoding="utf-8")

    assert hook_guard.hook_protection_status(project) == {
        "active": False,
        "reason": "hook_config_changed",
    }


def test_schema_one_marker_is_rejected_as_outdated(tmp_path: Path) -> None:
    project = _project(tmp_path / "project")
    runtime = project / ".agentboard"
    runtime.mkdir()
    (runtime / "hook-protection.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "activated_at": "2026-07-28T12:00:00+00:00",
                "session_id": "old",
                "plugin_root": str(PROJECT_ROOT),
                "script_hash": "0" * 64,
            }
        ),
        encoding="utf-8",
    )

    assert hook_guard.hook_protection_status(project) == {
        "active": False,
        "reason": "activation_marker_outdated",
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
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.run(activate=True) == 0
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
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(payload)))

    assert codex_hook.run() == 0
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert captured.err == ""


def test_codex_hook_self_test_is_available_through_main_cli() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "agentboard.cli", "codex-hook", "--self-test"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"ok": True, "cases": 26}
