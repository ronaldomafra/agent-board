from __future__ import annotations

import pytest

from agentboard.auth import AuthenticationError, LocalAuth


def test_dashboard_bootstrap_is_single_use_and_csrf_protected() -> None:
    auth = LocalAuth("a" * 64)
    bootstrap = auth.mint_bootstrap()

    session, csrf = auth.consume_bootstrap(bootstrap)
    principal = auth.browser_principal(session, csrf_token=csrf, require_csrf=True)

    assert principal.actor_id == "dashboard:human"
    with pytest.raises(AuthenticationError):
        auth.consume_bootstrap(bootstrap)
    with pytest.raises(AuthenticationError, match="CSRF"):
        auth.browser_principal(session, csrf_token="wrong", require_csrf=True)


def test_capability_derivation_uses_a_secret_independent_of_runtime_token() -> None:
    capability_secret = "stable-project-secret-" * 2
    first_runtime = LocalAuth("a" * 64, capability_secret=capability_secret)
    restarted_runtime = LocalAuth("b" * 64, capability_secret=capability_secret)

    arguments = {
        "idempotency_key": "claim-retry-key",
        "operation": "task_claim",
        "actor_id": "worker-1",
        "task_id": "AB-1",
    }

    assert first_runtime.derive_capability(**arguments) == restarted_runtime.derive_capability(
        **arguments
    )


def test_runtime_token_is_compared_and_never_returned() -> None:
    auth = LocalAuth("b" * 64)

    principal = auth.runtime_principal("b" * 64)

    assert "admin" in principal.roles
    with pytest.raises(AuthenticationError):
        auth.runtime_principal("c" * 64)
