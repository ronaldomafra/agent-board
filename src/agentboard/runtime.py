from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Self

from agentboard.config import find_project_root
from agentboard.git_local import GitPolicyError, LocalGitAdapter


class RuntimeUnavailableError(RuntimeError):
    """Raised when the project runtime cannot be discovered or started."""


class RuntimeAlreadyRunningError(RuntimeError):
    """Raised when another live process owns the project runtime lock."""


_RUNTIME_STARTUP_GRACE_SECONDS = 3.0


@dataclass(frozen=True, slots=True)
class ProjectIdentity:
    root: Path
    git_common_dir: Path | None
    key: str


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    schema_version: int
    project_key: str
    project_root: str
    pid: int
    nonce: str
    port: int
    api_token: str
    started_at: float

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RuntimeMetadata:
        metadata = cls(
            schema_version=int(payload["schema_version"]),
            project_key=str(payload["project_key"]),
            project_root=str(payload["project_root"]),
            pid=int(payload["pid"]),
            nonce=str(payload["nonce"]),
            port=int(payload["port"]),
            api_token=str(payload["api_token"]),
            started_at=float(payload["started_at"]),
        )
        if metadata.schema_version != 1 or not 1 <= metadata.port <= 65535:
            raise ValueError("Unsupported runtime metadata")
        return metadata


def project_identity(start: Path) -> ProjectIdentity:
    root = find_project_root(start).resolve()
    common: Path | None = None
    try:
        common = LocalGitAdapter(root).common_directory()
    except (GitPolicyError, OSError):
        common = None
    material = f"{os.path.normcase(str(root))}\0{os.path.normcase(str(common or ''))}".encode()
    return ProjectIdentity(
        root=root,
        git_common_dir=common,
        key=hashlib.sha256(material).hexdigest()[:24],
    )


class RuntimeLock:
    """Exclusive project runtime ownership with conservative stale-lock recovery."""

    def __init__(self, identity: ProjectIdentity, nonce: str) -> None:
        self.identity = identity
        self.nonce = nonce
        self.runtime_dir = identity.root / ".agentboard"
        self.path = self.runtime_dir / "runtime.lock"
        self.acquired = False

    def acquire(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        _secure_runtime_path(self.runtime_dir, directory=True)
        payload = json.dumps(
            {"pid": os.getpid(), "nonce": self.nonce, "created_at": time.time()},
            separators=(",", ":"),
        )
        try:
            self._create(payload)
        except FileExistsError:
            existing = self._read()
            pid = int(existing.get("pid", 0))
            created_at = float(existing.get("created_at", 0))
            age = time.time() - created_at
            pid_alive = _pid_is_alive(pid)
            pid_reused = pid_alive and _pid_started_after_lock(pid, created_at)
            if age < _RUNTIME_STARTUP_GRACE_SECONDS or (
                pid_alive and not pid_reused
            ):
                raise RuntimeAlreadyRunningError(
                    f"AgentBoard runtime already owns project {self.identity.key}"
                ) from None
            stale = self.runtime_dir / f"runtime.lock.stale.{secrets.token_hex(6)}"
            try:
                os.replace(self.path, stale)
                self._create(payload)
            except (FileNotFoundError, FileExistsError, PermissionError) as exc:
                raise RuntimeAlreadyRunningError("Runtime lock changed during recovery") from exc
        self.acquired = True

    def release(self) -> None:
        if not self.acquired:
            return
        try:
            existing = self._read()
            if existing.get("nonce") == self.nonce:
                self.path.unlink(missing_ok=True)
        finally:
            self.acquired = False

    def _create(self, payload: str) -> None:
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
        descriptor = -1
        try:
            _secure_runtime_path(self.path, directory=False)
            descriptor = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
            os.write(descriptor, payload.encode("utf-8"))
            os.fsync(descriptor)
        except Exception:
            self.path.unlink(missing_ok=True)
            raise
        finally:
            if descriptor >= 0:
                os.close(descriptor)

    def _read(self) -> dict[str, Any]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeAlreadyRunningError("Runtime lock is unreadable") from exc
        if not isinstance(value, dict):
            raise RuntimeAlreadyRunningError("Runtime lock is invalid")
        return value

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(self, *_: object) -> None:
        self.release()


def metadata_path(identity: ProjectIdentity) -> Path:
    return identity.root / ".agentboard" / "runtime.json"


def write_metadata(identity: ProjectIdentity, metadata: RuntimeMetadata) -> None:
    runtime_dir = identity.root / ".agentboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _secure_runtime_path(runtime_dir, directory=True)
    target = metadata_path(identity)
    temporary = runtime_dir / f"runtime.json.{metadata.nonce}.tmp"
    data = json.dumps(asdict(metadata), separators=(",", ":"), sort_keys=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    descriptor = -1
    try:
        _secure_runtime_path(temporary, directory=False)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_TRUNC)
        os.write(descriptor, data.encode("utf-8"))
        os.fsync(descriptor)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    os.replace(temporary, target)
    _secure_runtime_path(target, directory=False)


def remove_metadata(identity: ProjectIdentity, nonce: str) -> None:
    path = metadata_path(identity)
    try:
        current = read_metadata(identity)
    except RuntimeUnavailableError:
        return
    if current.nonce == nonce:
        path.unlink(missing_ok=True)


def read_metadata(identity: ProjectIdentity) -> RuntimeMetadata:
    try:
        raw = json.loads(metadata_path(identity).read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise TypeError("metadata must be an object")
        metadata = RuntimeMetadata.from_dict(raw)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise RuntimeUnavailableError("No valid AgentBoard runtime metadata") from exc
    if metadata.project_key != identity.key:
        raise RuntimeUnavailableError("Runtime metadata belongs to another project identity")
    return metadata


def runtime_is_healthy(metadata: RuntimeMetadata, timeout: float = 0.5) -> bool:
    request = urllib.request.Request(
        f"{metadata.base_url}/api/v1/health",
        headers={"Authorization": f"Bearer {metadata.api_token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return response.status == 200 and payload.get("project_key") == metadata.project_key
    except (OSError, ValueError, urllib.error.URLError):
        return False


def discover_runtime(start: Path) -> RuntimeMetadata | None:
    identity = project_identity(start)
    try:
        metadata = read_metadata(identity)
    except RuntimeUnavailableError:
        return None
    return metadata if runtime_is_healthy(metadata) else None


def ensure_runtime(start: Path, timeout: float = 20.0) -> RuntimeMetadata:
    identity = project_identity(start)
    existing = discover_runtime(identity.root)
    if existing:
        return existing

    runtime_dir = identity.root / ".agentboard"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _secure_runtime_path(runtime_dir, directory=True)
    log_path = runtime_dir / "runtime.log"
    _touch_private_file(log_path)
    _spawn_runtime_candidate(identity, log_path)

    deadline = time.monotonic() + timeout
    retry_at = time.monotonic() + _RUNTIME_STARTUP_GRACE_SECONDS + 0.5
    retried = False
    while time.monotonic() < deadline:
        runtime = discover_runtime(identity.root)
        if runtime:
            return runtime
        if not retried and time.monotonic() >= retry_at:
            _spawn_runtime_candidate(identity, log_path)
            retried = True
        time.sleep(0.1)
    raise RuntimeUnavailableError(f"AgentBoard runtime did not start; inspect {log_path}")


def _spawn_runtime_candidate(identity: ProjectIdentity, log_path: Path) -> None:
    command = [
        sys.executable,
        "-m",
        "agentboard.cli",
        "runtime",
        "--project",
        str(identity.root),
        "--port",
        str(_free_loopback_port()),
    ]
    creationflags = 0
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        creationflags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    with log_path.open("a", encoding="utf-8") as log:
        subprocess.Popen(
            command,
            cwd=identity.root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            creationflags=creationflags,
            close_fds=True,
            **kwargs,
        )


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _pid_started_after_lock(pid: int, lock_created_at: float) -> bool:
    started_at = _pid_started_at(pid)
    return started_at is not None and started_at > lock_created_at + 1.0


def _pid_started_at(pid: int) -> float | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _windows_pid_started_at(pid)
    try:
        result = subprocess.run(
            ["ps", "-o", "etime=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    try:
        day_text, separator, clock = value.partition("-")
        days = int(day_text) if separator else 0
        parts = [int(part) for part in (clock if separator else day_text).split(":")]
        if len(parts) == 3:
            hours, minutes, seconds = parts
        elif len(parts) == 2:
            hours = 0
            minutes, seconds = parts
        else:
            return None
    except ValueError:
        return None
    elapsed = days * 86400 + hours * 3600 + minutes * 60 + seconds
    return time.time() - elapsed


def _windows_pid_started_at(pid: int) -> float | None:
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        process = kernel32.OpenProcess(0x1000, False, pid)
        if not process:
            return None
        created = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        try:
            if not kernel32.GetProcessTimes(
                process,
                ctypes.byref(created),
                ctypes.byref(exited),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                return None
        finally:
            kernel32.CloseHandle(process)
        ticks = (created.dwHighDateTime << 32) | created.dwLowDateTime
        return ticks / 10_000_000 - 11_644_473_600
    except (AttributeError, OSError, ValueError):
        return None


def _touch_private_file(path: Path) -> None:
    if not path.exists():
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(descriptor)
    _secure_runtime_path(path, directory=False)


def _secure_runtime_path(path: Path, *, directory: bool) -> None:
    """Restrict runtime state to the current OS account."""

    if os.name != "nt":
        os.chmod(path, 0o700 if directory else 0o600)
        return

    environment = os.environ.copy()
    environment["AGENTBOARD_ACL_TARGET"] = str(path)
    environment["AGENTBOARD_ACL_DIRECTORY"] = "1" if directory else "0"
    try:
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                _WINDOWS_OWNER_ONLY_ACL_SCRIPT,
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeUnavailableError(
            f"Could not restrict runtime permissions for {path.name}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeUnavailableError(
            f"Could not restrict runtime permissions for {path.name}"
        )


_WINDOWS_OWNER_ONLY_ACL_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$target = $env:AGENTBOARD_ACL_TARGET
$isDirectory = $env:AGENTBOARD_ACL_DIRECTORY -eq '1'
if ([string]::IsNullOrWhiteSpace($target)) {
    throw 'Missing ACL target'
}

$sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$rights = [System.Security.AccessControl.FileSystemRights]::FullControl
$allow = [System.Security.AccessControl.AccessControlType]::Allow
if ($isDirectory) {
    $expectedInheritance = (
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor
        [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    )
} else {
    $expectedInheritance = [System.Security.AccessControl.InheritanceFlags]::None
}
$security = Get-Acl -LiteralPath $target
$currentOwner = $security.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
)
$currentRules = @(
    $security.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    )
)
if (
    $security.AreAccessRulesProtected -and
    $currentOwner.Value -eq $sid.Value -and
    $currentRules.Count -eq 1 -and
    -not $currentRules[0].IsInherited -and
    $currentRules[0].IdentityReference.Value -eq $sid.Value -and
    $currentRules[0].AccessControlType -eq $allow -and
    (($currentRules[0].FileSystemRights -band $rights) -eq $rights) -and
    $currentRules[0].InheritanceFlags -eq $expectedInheritance -and
    $currentRules[0].PropagationFlags -eq (
        [System.Security.AccessControl.PropagationFlags]::None
    )
) {
    exit 0
}

if ($currentOwner.Value -ne $sid.Value) {
    & icacls.exe $target '/setowner' ('*' + $sid.Value) | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not set runtime owner'
    }
}

& icacls.exe $target '/inheritance:r' | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not remove runtime ACL inheritance'
}

$explicitIdentities = @(
    (Get-Acl -LiteralPath $target).GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    ) |
        ForEach-Object { $_.IdentityReference.Value } |
        Sort-Object -Unique
)
foreach ($identity in $explicitIdentities) {
    & icacls.exe $target '/remove' ('*' + $identity) | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not remove an explicit runtime ACL entry'
    }
}

if ($isDirectory) {
    $permission = '*{0}:(OI)(CI)(F)' -f $sid.Value
} else {
    $permission = '*{0}:(F)' -f $sid.Value
}
& icacls.exe $target '/grant:r' $permission | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw 'Could not grant the runtime owner access'
}

$actual = Get-Acl -LiteralPath $target
$actualOwner = $actual.GetOwner(
    [System.Security.Principal.SecurityIdentifier]
)
$rules = @(
    $actual.GetAccessRules(
        $true,
        $true,
        [System.Security.Principal.SecurityIdentifier]
    )
)
if (-not $actual.AreAccessRulesProtected) {
    throw 'Runtime ACL still inherits permissions'
}
if ($actualOwner.Value -ne $sid.Value -or $rules.Count -ne 1) {
    throw 'Runtime ACL is not owner-only'
}
$actualRule = $rules[0]
if (
    $actualRule.IsInherited -or
    $actualRule.IdentityReference.Value -ne $sid.Value -or
    $actualRule.AccessControlType -ne $allow -or
    (($actualRule.FileSystemRights -band $rights) -ne $rights) -or
    $actualRule.InheritanceFlags -ne $expectedInheritance -or
    $actualRule.PropagationFlags -ne (
        [System.Security.AccessControl.PropagationFlags]::None
    )
) {
    throw 'Runtime ACL verification failed'
}
"""
