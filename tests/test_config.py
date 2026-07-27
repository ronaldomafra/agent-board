from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agentboard.config import (
    ConfigurationError,
    config_fingerprint,
    default_config,
    load_config,
    recover_config_apply,
    validate_config,
    write_config_apply_marker,
)
from agentboard.domain import Actor
from agentboard.service import BoardService


def test_example_configuration_is_valid_and_local_only() -> None:
    config = load_config(Path("config/orchestration.example.yaml"))

    assert config.runtime.bind_host == "127.0.0.1"
    assert config.workflow.task_phases == (
        "BACKLOG",
        "IN_PROGRESS",
        "VERIFYING",
        "DONE",
        "CANCELED",
    )
    assert config.git.local_only is True
    assert config.git.require_expected_source_sha is True
    assert config.git.require_approved_target_branch is True
    assert "push" in config.git.forbid_remote_commands
    assert len(config_fingerprint(config)) == 64


def test_configuration_rejects_public_bind_and_relaxed_remote_policy() -> None:
    payload = yaml.safe_load(Path("config/orchestration.example.yaml").read_text(encoding="utf-8"))
    payload["runtime"]["bind_host"] = "0.0.0.0"
    payload["git"]["forbid_remote_commands"] = ["push"]

    with pytest.raises(ConfigurationError):
        validate_config(payload)


def test_configuration_rejects_path_escape() -> None:
    payload = yaml.safe_load(Path("config/orchestration.example.yaml").read_text(encoding="utf-8"))
    payload["paths"]["database"] = "../outside.db"

    with pytest.raises(ConfigurationError, match="inside the project"):
        validate_config(payload)


def test_uncommitted_configuration_replace_is_restored_from_crash_marker(
    tmp_path: Path,
) -> None:
    database = tmp_path / ".agentboard" / "state.db"
    service = BoardService(database)
    config = default_config("Recovery test")
    draft = service.config_draft_create(
        yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False),
        expected_version=0,
        actor=Actor("orchestrator", frozenset({"orchestrator"})),
        idempotency_key="config-recovery-draft",
    )
    target = tmp_path / "agentboard.yaml"
    target.write_text("old: true\n", encoding="utf-8")
    backup = tmp_path / ".agentboard" / "backups" / "old.yaml.bak"
    backup.parent.mkdir(parents=True)
    backup.write_text("old: true\n", encoding="utf-8")
    target.write_text("new: uncommitted\n", encoding="utf-8")
    marker = write_config_apply_marker(
        tmp_path,
        database_path=database,
        target=target,
        backup=backup,
        target_existed=True,
        draft_id=draft.entity_id,
        content_hash=draft.data["content_hash"],
    )

    recover_config_apply(tmp_path)

    assert target.read_text(encoding="utf-8") == "old: true\n"
    assert not marker.exists()
