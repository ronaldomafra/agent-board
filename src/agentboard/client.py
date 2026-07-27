from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agentboard.runtime import RuntimeMetadata, ensure_runtime


@dataclass(frozen=True, slots=True)
class AgentBoardClientError(RuntimeError):
    code: str
    message: str
    status: int = 500
    details: dict[str, Any] | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


class RuntimeClient:
    def __init__(self, metadata: RuntimeMetadata) -> None:
        self.metadata = metadata

    @classmethod
    def for_project(cls, project: Path) -> RuntimeClient:
        return cls(ensure_runtime(project))

    def get(self, path: str, query: dict[str, Any] | None = None) -> Any:
        if query:
            values = {key: value for key, value in query.items() if value is not None}
            path = f"{path}?{urllib.parse.urlencode(values)}"
        return self._request("GET", path)

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        capability_token: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST", path, payload, capability_token=capability_token
        )

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        capability_token: str | None = None,
    ) -> Any:
        if not path.startswith("/api/v1/"):
            raise ValueError("Runtime client only permits versioned AgentBoard API paths")
        data = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.metadata.api_token}",
        }
        if payload is not None:
            data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if capability_token is not None:
            headers["X-AgentBoard-Capability"] = capability_token
        request = urllib.request.Request(
            f"{self.metadata.base_url}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read()
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except urllib.error.HTTPError as exc:
            try:
                error = json.loads(exc.read().decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                error = {}
            payload_error = error.get("error", error)
            raise AgentBoardClientError(
                code=str(payload_error.get("code", "HTTP_ERROR")),
                message=str(payload_error.get("message", exc.reason)),
                status=exc.code,
                details=payload_error.get("details"),
            ) from exc
        except (OSError, ValueError, urllib.error.URLError) as exc:
            raise AgentBoardClientError(
                code="RUNTIME_UNAVAILABLE",
                message="The local AgentBoard runtime is unavailable",
                status=503,
            ) from exc
        return parsed
