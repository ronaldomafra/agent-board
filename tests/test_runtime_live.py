from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import yaml

from agentboard.client import RuntimeClient
from agentboard.config import default_config
from agentboard.runtime import discover_runtime, ensure_runtime


def free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_runtime_stops_after_last_mcp_client_detaches(tmp_path: Path) -> None:
    payload = default_config("Runtime test").model_dump(mode="json")
    payload["runtime"]["idle_shutdown_seconds"] = 1
    (tmp_path / "agentboard.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    port = free_loopback_port()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentboard.cli",
            "runtime",
            "--project",
            str(tmp_path),
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        deadline = time.monotonic() + 30
        metadata = None
        while time.monotonic() < deadline:
            metadata = discover_runtime(tmp_path)
            if metadata is not None:
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Runtime exited before health check.\nstdout={stdout}\nstderr={stderr}"
                )
            time.sleep(0.1)
        assert metadata is not None
        assert metadata.port == port

        client = RuntimeClient(metadata)
        client_id = "mcp-runtime-live-test-client"
        assert client.post(
            "/api/v1/runtime/attach", {"client_id": client_id}
        )["client_count"] == 1
        assert client.post(
            "/api/v1/runtime/detach", {"client_id": client_id}
        )["client_count"] == 0

        process.wait(timeout=10)
        assert process.returncode == 0
        assert discover_runtime(tmp_path) is None
        assert not (tmp_path / ".agentboard" / "runtime.json").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_runtime_without_any_client_stops_after_idle_grace(tmp_path: Path) -> None:
    payload = default_config("Runtime zero-client test").model_dump(mode="json")
    payload["runtime"]["idle_shutdown_seconds"] = 2
    (tmp_path / "agentboard.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    port = free_loopback_port()
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentboard.cli",
            "runtime",
            "--project",
            str(tmp_path),
            "--port",
            str(port),
        ],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=creationflags,
    )
    try:
        deadline = time.monotonic() + 30
        metadata = None
        while time.monotonic() < deadline:
            metadata = discover_runtime(tmp_path)
            if metadata is not None:
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                raise AssertionError(
                    f"Runtime exited before health check.\nstdout={stdout}\nstderr={stderr}"
                )
            time.sleep(0.1)
        assert metadata is not None
        assert metadata.port == port

        process.wait(timeout=10)
        assert process.returncode == 0
        assert discover_runtime(tmp_path) is None
        assert not (tmp_path / ".agentboard" / "runtime.json").exists()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)


def test_runtime_starter_retries_a_fresh_abandoned_lock(tmp_path: Path) -> None:
    payload = default_config("Runtime recovery test").model_dump(mode="json")
    payload["runtime"]["idle_shutdown_seconds"] = 3
    (tmp_path / "agentboard.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
        newline="\n",
    )
    runtime_dir = tmp_path / ".agentboard"
    runtime_dir.mkdir()
    (runtime_dir / "runtime.lock").write_text(
        json.dumps(
            {
                "pid": 999_999_999,
                "nonce": "abandoned",
                "created_at": time.time(),
            }
        ),
        encoding="utf-8",
    )

    metadata = ensure_runtime(tmp_path, timeout=20)
    client = RuntimeClient(metadata)
    client_id = "mcp-runtime-recovery-test"
    client.post("/api/v1/runtime/attach", {"client_id": client_id})
    client.post("/api/v1/runtime/detach", {"client_id": client_id})

    runtime_metadata = tmp_path / ".agentboard" / "runtime.json"
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and runtime_metadata.exists():
        time.sleep(0.1)
    assert not runtime_metadata.exists()
    assert discover_runtime(tmp_path) is None
