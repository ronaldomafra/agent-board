from __future__ import annotations

import json
import os
import stat
import subprocess
import time
from pathlib import Path

import pytest

import agentboard.runtime as runtime_module
from agentboard.runtime import (
    ProjectIdentity,
    RuntimeAlreadyRunningError,
    RuntimeLock,
    RuntimeMetadata,
    read_metadata,
    remove_metadata,
    write_metadata,
)


def identity_for(root: Path) -> ProjectIdentity:
    return ProjectIdentity(root=root, git_common_dir=None, key="project-key")


def test_runtime_metadata_round_trip_and_nonce_protected_cleanup(tmp_path: Path) -> None:
    identity = identity_for(tmp_path)
    metadata = RuntimeMetadata(
        schema_version=1,
        project_key=identity.key,
        project_root=str(tmp_path),
        pid=123,
        nonce="current",
        port=43127,
        api_token="secret",
        started_at=1.0,
    )

    write_metadata(identity, metadata)
    assert read_metadata(identity) == metadata

    remove_metadata(identity, "older")
    assert read_metadata(identity) == metadata
    remove_metadata(identity, "current")
    assert not (tmp_path / ".agentboard" / "runtime.json").exists()


def test_runtime_lock_rejects_live_owner(tmp_path: Path) -> None:
    identity = identity_for(tmp_path)
    first = RuntimeLock(identity, "one")
    first.acquire()
    try:
        with pytest.raises(RuntimeAlreadyRunningError):
            RuntimeLock(identity, "two").acquire()
    finally:
        first.release()


def test_runtime_lock_recovers_a_confirmed_stale_owner(tmp_path: Path) -> None:
    identity = identity_for(tmp_path)
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    (runtime_dir / "runtime.lock").write_text(
        json.dumps({"pid": 999_999_999, "nonce": "old", "created_at": 0}),
        encoding="utf-8",
    )

    lock = RuntimeLock(identity, "new")
    lock.acquire()
    try:
        payload = json.loads((runtime_dir / "runtime.lock").read_text(encoding="utf-8"))
        assert payload["nonce"] == "new"
    finally:
        lock.release()


def test_runtime_lock_recovers_a_reused_live_pid_without_matching_runtime(
    tmp_path: Path,
) -> None:
    identity = identity_for(tmp_path)
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    (runtime_dir / "runtime.lock").write_text(
        json.dumps(
                {
                    "pid": os.getpid(),
                    "nonce": "unrelated-process",
                    # Predate every possible start time for the current PID so this
                    # remains a deterministic PID-reuse simulation in long suites.
                    "created_at": 1.0,
                }
            ),
        encoding="utf-8",
    )
    write_metadata(
        identity,
        RuntimeMetadata(
            schema_version=1,
            project_key=identity.key,
            project_root=str(tmp_path),
            pid=os.getpid(),
            nonce="unrelated-process",
            port=9,
            api_token="secret",
            started_at=1.0,
        ),
    )

    lock = RuntimeLock(identity, "new-owner")
    lock.acquire()
    try:
        payload = json.loads((runtime_dir / "runtime.lock").read_text(encoding="utf-8"))
        assert payload["nonce"] == "new-owner"
    finally:
        lock.release()


def test_runtime_lock_keeps_matching_live_owner_when_health_is_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("agentboard.runtime._RUNTIME_STARTUP_GRACE_SECONDS", 0)
    identity = identity_for(tmp_path)
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    nonce = "matching-owner"
    (runtime_dir / "runtime.lock").write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "nonce": nonce,
                "created_at": time.time() - 0.1,
            }
        ),
        encoding="utf-8",
    )
    write_metadata(
        identity,
        RuntimeMetadata(
            schema_version=1,
            project_key=identity.key,
            project_root=str(tmp_path),
            pid=os.getpid(),
            nonce=nonce,
            port=9,
            api_token="secret",
            started_at=1.0,
        ),
    )

    with pytest.raises(RuntimeAlreadyRunningError):
        RuntimeLock(identity, "contender").acquire()


@pytest.mark.parametrize("metadata_state", ["missing", "corrupt", "mismatched"])
def test_runtime_lock_never_steals_a_live_non_reused_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    metadata_state: str,
) -> None:
    monkeypatch.setattr("agentboard.runtime._RUNTIME_STARTUP_GRACE_SECONDS", 0)
    monkeypatch.setattr("agentboard.runtime._pid_is_alive", lambda _pid: True)
    monkeypatch.setattr(
        "agentboard.runtime._pid_started_after_lock",
        lambda _pid, _created_at: False,
    )
    identity = identity_for(tmp_path)
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    lock_path = runtime_dir / "runtime.lock"
    lock_path.write_text(
        json.dumps(
            {
                "pid": os.getpid(),
                "nonce": "live-owner",
                "created_at": time.time() - 60,
            }
        ),
        encoding="utf-8",
    )
    if metadata_state == "corrupt":
        (runtime_dir / "runtime.json").write_text("{", encoding="utf-8")
    elif metadata_state == "mismatched":
        write_metadata(
            identity,
            RuntimeMetadata(
                schema_version=1,
                project_key=identity.key,
                project_root=str(tmp_path),
                pid=os.getpid() + 1,
                nonce="different-owner",
                port=9,
                api_token="secret",
                started_at=1.0,
            ),
        )

    with pytest.raises(RuntimeAlreadyRunningError):
        RuntimeLock(identity, "contender").acquire()

    assert json.loads(lock_path.read_text(encoding="utf-8"))["nonce"] == "live-owner"


def test_runtime_state_is_owner_only(tmp_path: Path) -> None:
    identity = identity_for(tmp_path)
    metadata = RuntimeMetadata(
        schema_version=1,
        project_key=identity.key,
        project_root=str(tmp_path),
        pid=123,
        nonce="private",
        port=43127,
        api_token="secret",
        started_at=1.0,
    )
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    if os.name == "nt":
        subprocess.run(
            [
                "icacls",
                str(runtime_dir),
                "/grant",
                "*S-1-1-0:(OI)(CI)(RX)",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    write_metadata(identity, metadata)
    runtime_file = runtime_dir / "runtime.json"

    if os.name == "nt":
        subprocess.run(
            ["icacls", str(runtime_file), "/grant", "*S-1-1-0:(R)"],
            capture_output=True,
            text=True,
            check=True,
        )
        runtime_module._secure_runtime_path(runtime_file, directory=False)
        environment = os.environ.copy()
        for path in (runtime_dir, runtime_file):
            environment["AGENTBOARD_TEST_ACL_TARGET"] = str(path)
            result = subprocess.run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    r"""
$acl = Get-Acl -LiteralPath $env:AGENTBOARD_TEST_ACL_TARGET
$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$owner = $acl.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
)
$expectedInheritance = if (
    Test-Path -LiteralPath $env:AGENTBOARD_TEST_ACL_TARGET -PathType Container
) {
    (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
} else {
    [System.Security.AccessControl.InheritanceFlags]::None
}
$rules = @(
    $acl.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    )
)
if (
    -not $acl.AreAccessRulesProtected -or
    $owner.Value -ne $sid.Value -or
    $rules.Count -ne 1 -or
    $rules[0].IdentityReference.Value -ne $sid.Value -or
    $rules[0].IsInherited -or
    $rules[0].InheritanceFlags -ne $expectedInheritance -or
    (($rules[0].FileSystemRights -band (
        [System.Security.AccessControl.FileSystemRights]::FullControl
    )) -ne [System.Security.AccessControl.FileSystemRights]::FullControl)
) {
    exit 1
}
""",
                ],
                capture_output=True,
                text=True,
                check=False,
                env=environment,
            )
            assert result.returncode == 0, result.stderr
    else:
        assert stat.S_IMODE(runtime_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(runtime_file.stat().st_mode) == 0o600
