from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


def _handler_path() -> Path:
    from agentboard import codex_hook

    return Path(codex_hook.__file__).resolve()


def _valid_hash(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def hook_protection_status(
    project_root: Path,
    *,
    max_age: timedelta = timedelta(hours=12),
) -> dict[str, Any]:
    marker = project_root.resolve() / ".agentboard" / "hook-protection.json"
    if not marker.is_file():
        return {"active": False, "reason": "activation_marker_missing"}
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
        activated_at = datetime.fromisoformat(str(payload["activated_at"]))
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return {"active": False, "reason": "activation_marker_invalid"}
    if activated_at.tzinfo is None:
        return {"active": False, "reason": "activation_timestamp_invalid"}
    if payload.get("schema_version") == 1:
        return {"active": False, "reason": "activation_marker_outdated"}
    age = datetime.now(UTC) - activated_at.astimezone(UTC)
    script_hash = payload.get("script_hash")
    hook_config_hash = payload.get("hook_config_hash")
    plugin_root = payload.get("plugin_root")
    handler_path = payload.get("handler_path")
    if (
        payload.get("schema_version") != 2
        or not isinstance(plugin_root, str)
        or not plugin_root
        or not isinstance(handler_path, str)
        or not handler_path
        or not _valid_hash(script_hash)
        or not _valid_hash(hook_config_hash)
    ):
        return {"active": False, "reason": "activation_marker_invalid"}
    try:
        installed_handler = _handler_path()
        recorded_handler = Path(handler_path).resolve()
        if recorded_handler != installed_handler:
            return {"active": False, "reason": "hook_handler_changed"}
        installed_hash = hashlib.sha256(installed_handler.read_bytes()).hexdigest()
    except OSError:
        return {"active": False, "reason": "hook_script_unavailable"}
    if installed_hash != script_hash:
        return {"active": False, "reason": "hook_script_changed"}
    try:
        hook_config = Path(plugin_root).resolve() / "hooks" / "hooks.json"
        installed_config_hash = hashlib.sha256(hook_config.read_bytes()).hexdigest()
    except OSError:
        return {"active": False, "reason": "hook_config_unavailable"}
    if installed_config_hash != hook_config_hash:
        return {"active": False, "reason": "hook_config_changed"}
    if age < timedelta(minutes=-5) or age > max_age:
        return {"active": False, "reason": "activation_marker_stale"}
    return {
        "active": True,
        "reason": None,
        "activated_at": activated_at.astimezone(UTC).isoformat(),
        "script_hash": script_hash,
    }
