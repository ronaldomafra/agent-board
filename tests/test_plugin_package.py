from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_package_local_plugin_creates_compact_marketplace(tmp_path: Path) -> None:
    output = tmp_path / "agentboard-marketplace"

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "package_local_plugin.py"),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr

    marketplace_path = output / ".agents" / "plugins" / "marketplace.json"
    plugin_root = output / "plugins" / "agent-board"
    marketplace = json.loads(marketplace_path.read_text(encoding="utf-8"))
    manifest = json.loads(
        (plugin_root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8")
    )

    assert marketplace["name"] == "agentboard-local"
    assert marketplace["plugins"] == [
        {
            "name": "agent-board",
            "source": {
                "source": "local",
                "path": "./plugins/agent-board",
            },
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": "Productivity",
        }
    ]
    assert manifest["name"] == "agent-board"
    assert (plugin_root / ".mcp.json").is_file()
    assert (plugin_root / "hooks" / "hooks.json").is_file()
    assert (plugin_root / "scripts" / "codex_hook.py").is_file()
    assert (plugin_root / "skills" / "agentboard-orchestrate" / "SKILL.md").is_file()
    assert not (plugin_root / "src").exists()
    assert not (plugin_root / "tests").exists()


def test_package_local_plugin_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    output = tmp_path / "agentboard-marketplace"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(PROJECT_ROOT / "scripts" / "package_local_plugin.py"),
            "--output",
            str(output),
        ],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert marker.read_text(encoding="utf-8") == "preserve"
