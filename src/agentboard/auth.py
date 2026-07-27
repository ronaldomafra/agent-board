from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass


class AuthenticationError(PermissionError):
    """Raised when localhost credentials are absent, expired or out of scope."""


@dataclass(frozen=True, slots=True)
class Principal:
    actor_id: str
    roles: frozenset[str]
    authentication: str


@dataclass(frozen=True, slots=True)
class BrowserSession:
    token_hash: str
    csrf_token: str
    expires_at: float


@dataclass(frozen=True, slots=True)
class HumanAuthorization:
    operation: str
    resource_id: str | None
    expires_at: float


class LocalAuth:
    """In-memory browser bootstrap/session state anchored by the runtime token."""

    def __init__(self, api_token: str, session_ttl_seconds: int = 8 * 60 * 60) -> None:
        if len(api_token) < 32:
            raise ValueError("Runtime API token is too short")
        self._api_token = api_token
        self._session_ttl = session_ttl_seconds
        self._bootstrap: dict[str, float] = {}
        self._sessions: dict[str, BrowserSession] = {}
        self._human_authorizations: dict[str, HumanAuthorization] = {}
        self._lock = threading.RLock()

    def runtime_principal(self, presented_token: str) -> Principal:
        if not secrets.compare_digest(presented_token, self._api_token):
            raise AuthenticationError("Invalid local runtime credential")
        return Principal(
            actor_id="codex:orchestrator",
            roles=frozenset({"orchestrator", "admin"}),
            authentication="runtime",
        )

    def mint_bootstrap(self, ttl_seconds: int = 60) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._prune()
            self._bootstrap[_hash_token(token)] = time.time() + ttl_seconds
        return token

    def derive_capability(
        self,
        *,
        idempotency_key: str,
        operation: str,
        actor_id: str,
        task_id: str,
    ) -> str:
        """Derive a replay-stable token without persisting the plaintext capability."""
        material = f"{idempotency_key}\0{operation}\0{actor_id}\0{task_id}".encode()
        digest = hmac.new(self._api_token.encode(), material, hashlib.sha256).hexdigest()
        return f"abcap_{digest}"

    def mint_human_authorization(
        self,
        *,
        operation: str,
        resource_id: str | None,
        ttl_seconds: int = 120,
    ) -> str:
        token = f"abhuman_{secrets.token_urlsafe(32)}"
        with self._lock:
            self._prune()
            self._human_authorizations[_hash_token(token)] = HumanAuthorization(
                operation=operation,
                resource_id=resource_id,
                expires_at=time.time() + ttl_seconds,
            )
        return token

    def human_authorization_principal(
        self,
        token: str,
        *,
        operation: str,
        resource_id: str | None,
    ) -> Principal:
        with self._lock:
            self._prune()
            authorization = self._human_authorizations.get(_hash_token(token))
            if (
                authorization is None
                or authorization.operation != operation
                or authorization.resource_id != resource_id
            ):
                raise AuthenticationError(
                    "Human authorization is expired or outside the requested scope"
                )
        return Principal(
            actor_id="dashboard:human",
            roles=frozenset({"human"}),
            authentication="human_authorization",
        )

    def consume_bootstrap(self, token: str) -> tuple[str, str]:
        token_hash = _hash_token(token)
        with self._lock:
            self._prune()
            expiry = self._bootstrap.pop(token_hash, None)
            if expiry is None or expiry < time.time():
                raise AuthenticationError("Dashboard bootstrap is invalid or already used")
            session_token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(24)
            self._sessions[_hash_token(session_token)] = BrowserSession(
                token_hash=_hash_token(session_token),
                csrf_token=csrf_token,
                expires_at=time.time() + self._session_ttl,
            )
        return session_token, csrf_token

    def browser_principal(
        self,
        session_token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
    ) -> Principal:
        with self._lock:
            self._prune()
            session = self._sessions.get(_hash_token(session_token))
            if session is None:
                raise AuthenticationError("Dashboard session is missing or expired")
            if require_csrf and (
                csrf_token is None
                or not secrets.compare_digest(csrf_token, session.csrf_token)
            ):
                raise AuthenticationError("CSRF token is missing or invalid")
        return Principal(
            actor_id="dashboard:human",
            roles=frozenset({"human", "reader"}),
            authentication="browser",
        )

    def _prune(self) -> None:
        now = time.time()
        self._bootstrap = {
            token_hash: expiry
            for token_hash, expiry in self._bootstrap.items()
            if expiry >= now
        }
        self._sessions = {
            token_hash: session
            for token_hash, session in self._sessions.items()
            if session.expires_at >= now
        }
        self._human_authorizations = {
            token_hash: authorization
            for token_hash, authorization in self._human_authorizations.items()
            if authorization.expires_at >= now
        }


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
