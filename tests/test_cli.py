from __future__ import annotations

from pathlib import Path

from agentboard.cli import _init
from agentboard.config import load_config


def test_init_creates_a_complete_non_destructive_scaffold(
    tmp_path: Path, capsys
) -> None:
    existing_agents = tmp_path / "AGENTS.md"
    existing_agents.write_text("# Existing project rules\n", encoding="utf-8")
    gitignore = tmp_path / ".gitignore"
    gitignore.write_text(".venv/\n", encoding="utf-8")

    assert _init(tmp_path, "Scaffold test") == 0

    assert existing_agents.read_text(encoding="utf-8") == "# Existing project rules\n"
    assert load_config(tmp_path / "agentboard.yaml").project.name == "Scaffold test"
    for profile in (
        "agentboard_orchestrator.toml",
        "agentboard_worker.toml",
        "agentboard_reviewer.toml",
    ):
        assert (tmp_path / ".codex" / "agents" / profile).is_file()
    assert ".agentboard/" in gitignore.read_text(encoding="utf-8")

    output = capsys.readouterr().out
    assert "created:" in output
    assert "reused:" in output

    assert _init(tmp_path, "Ignored on repeat") == 0
    repeated_output = capsys.readouterr().out
    assert "reused:" in repeated_output
    assert "ignored:" in repeated_output
