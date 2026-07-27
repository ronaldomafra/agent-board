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


def test_runtime_token_is_compared_and_never_returned() -> None:
    auth = LocalAuth("b" * 64)

    principal = auth.runtime_principal("b" * 64)

    assert "admin" in principal.roles
    with pytest.raises(AuthenticationError):
        auth.runtime_principal("c" * 64)

