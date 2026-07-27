from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any


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
    age = datetime.now(UTC) - activated_at.astimezone(UTC)
    script_hash = payload.get("script_hash")
    plugin_root = payload.get("plugin_root")
    valid_hash = (
        isinstance(script_hash, str)
        and len(script_hash) == 64
        and all(character in "0123456789abcdef" for character in script_hash)
    )
    if (
        payload.get("schema_version") != 1
        or not isinstance(plugin_root, str)
        or not plugin_root
        or not valid_hash
    ):
        return {"active": False, "reason": "activation_marker_invalid"}
    try:
        hook_script = Path(plugin_root).resolve() / "scripts" / "codex_hook.py"
        installed_hash = hashlib.sha256(hook_script.read_bytes()).hexdigest()
    except OSError:
        return {"active": False, "reason": "hook_script_unavailable"}
    if installed_hash != script_hash:
        return {"active": False, "reason": "hook_script_changed"}
    if age < timedelta(minutes=-5) or age > max_age:
        return {"active": False, "reason": "activation_marker_stale"}
    return {
        "active": True,
        "reason": None,
        "activated_at": activated_at.astimezone(UTC).isoformat(),
        "script_hash": script_hash,
    }
