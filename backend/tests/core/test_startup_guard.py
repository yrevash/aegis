"""Fail-fast startup guards: the signing secret (H4), and spend caps that bind.

Dev keeps the non-secret fallback (so the offline/test path stays quiet); any other
environment refuses to boot on a default or too-short ``JWT_SECRET`` — and, since
OWASP LLM10, on a budget posture that would let a governance read failure through.
"""

from __future__ import annotations

import secrets

import pytest

from app.config import (
    DEFAULT_JWT_SECRET,
    InsecureConfigurationError,
    Settings,
)


def test_dev_allows_default_secret():
    # The dev fallback is explicitly permitted so tests/offline stay quiet.
    Settings(app_env="dev", jwt_secret=DEFAULT_JWT_SECRET).ensure_secure_secrets()


def test_non_dev_rejects_default_secret():
    with pytest.raises(InsecureConfigurationError):
        Settings(app_env="prod", jwt_secret=DEFAULT_JWT_SECRET).ensure_secure_secrets()


def test_non_dev_rejects_short_secret():
    with pytest.raises(InsecureConfigurationError):
        Settings(app_env="staging", jwt_secret="too-short").ensure_secure_secrets()


def test_non_dev_accepts_a_secret_that_is_actually_strong():
    # A real generated value. This assertion used to read ``"x" * 48`` — 48 characters
    # and one byte of choice — which the guard accepted, so the test was certifying the
    # hole below rather than the property it named.
    Settings(
        app_env="prod", jwt_secret=secrets.token_urlsafe(48)
    ).ensure_secure_secrets()


def test_non_dev_rejects_a_long_secret_with_almost_no_choice_in_it():
    """The length floor alone certified ``"x" * 48`` as production-ready.

    Every access token this platform mints is authenticated with this value, so "48
    characters" is only reassuring if the 48 were chosen. A held-down key, a repeated
    word and ``abababab…`` all clear a length check and none of them is a secret.
    """
    for weak in ("x" * 48, "abababababababababababababababababab", "aegis" * 10):
        with pytest.raises(InsecureConfigurationError, match="distinct"):
            Settings(app_env="prod", jwt_secret=weak).ensure_secure_secrets()


def test_non_dev_rejects_a_placeholder_padded_to_the_length_floor():
    """The failure this catches is a deployment that read the instruction and padded it.

    ``change-me-in-production-1234567890`` is 34 characters, is not the built-in
    default, and draws on plenty of distinct characters — it cleared every check the
    guard had. It is also the first thing anyone would try.
    """
    for weak in (
        "change-me-in-production-1234567890",
        "supersecret-jwt-signing-key-for-aegis",
        "your-secret-key-goes-right-here-ok12",
    ):
        with pytest.raises(InsecureConfigurationError, match="placeholder"):
            Settings(app_env="prod", jwt_secret=weak).ensure_secure_secrets()


def test_the_refusal_never_echoes_the_secret_it_refused():
    """A diagnostic that prints a credential is one somebody pastes into an issue."""
    secret = "change-me-in-production-1234567890"
    with pytest.raises(InsecureConfigurationError) as exc:
        Settings(app_env="prod", jwt_secret=secret).ensure_secure_secrets()
    assert secret not in str(exc.value)
    # It still has to be actionable: name the fix, not just the fault.
    assert "token_urlsafe" in str(exc.value)


def test_dev_still_boots_on_everything_above():
    """The dev loop is not the thing being protected, and a guard that blocks it dies.

    Same asymmetry ``verify_rls_enforcement`` uses: fatal outside dev, silent inside.
    """
    for weak in (DEFAULT_JWT_SECRET, "x" * 48, "changeme", "short"):
        Settings(app_env="dev", jwt_secret=weak).ensure_secure_secrets()


def test_create_app_fails_fast_on_insecure_secret(monkeypatch):
    import app.main as main

    bad = Settings(app_env="prod", jwt_secret=DEFAULT_JWT_SECRET)
    monkeypatch.setattr(main, "get_settings", lambda: bad)
    with pytest.raises(InsecureConfigurationError):
        main.create_app()


# ─────────────────────────────────────────────────────────────────────────────
# OWASP LLM10 — a spend cap that fails open is not a spend cap
# ─────────────────────────────────────────────────────────────────────────────


def _strong(**kwargs) -> Settings:
    """A Settings that clears the secret guard, so only the budget guard can fire."""
    return Settings(jwt_secret=secrets.token_urlsafe(48), **kwargs)


def test_the_refusal_names_the_variable_that_actually_binds():
    """``GATEWAY_BUDGET_FAIL_OPEN`` is the standalone gateway's knob, and it is inert here.

    ``app.core.llm`` injects a ``GatewayConfig`` that reads ``Settings``, so the
    gateway never consults its own environment default and the variable an operator
    would reach for first does nothing. A refusal that named the wrong one would send
    them to unset a variable that was never in force.
    """
    from aegis.gateway import llm as gateway_llm

    assert Settings(budget_fail_open=False).budget_fail_open is False
    assert gateway_llm._get_config is not None  # the injected config is the authority
    with pytest.raises(InsecureConfigurationError) as exc:
        _strong(app_env="prod", budget_fail_open=True).ensure_spend_caps_bind()
    assert "BUDGET_FAIL_OPEN" in str(exc.value)
    assert "inert" in str(exc.value)


def test_non_dev_refuses_to_boot_with_budgets_failing_open():
    """The control this closes: ``BUDGET_FAIL_OPEN=true`` in production.

    Every token, USD, RPM and TPM ceiling becomes a suggestion the moment the
    enforcement read errors. This is the test that fails if the guard is removed —
    switch off ``ensure_spend_caps_bind`` and a fail-open production deployment boots
    again, which is exactly the state the LLM10 row used to have to admit to.
    """
    with pytest.raises(InsecureConfigurationError, match="BUDGET_FAIL_OPEN"):
        _strong(app_env="prod", budget_fail_open=True).ensure_spend_caps_bind()
    with pytest.raises(InsecureConfigurationError, match="not a cap"):
        _strong(app_env="staging", budget_fail_open=True).ensure_spend_caps_bind()


def test_dev_may_still_run_fail_open():
    """Same asymmetry as the secret guard: dev is not the thing being protected."""
    _strong(app_env="dev", budget_fail_open=True).ensure_spend_caps_bind()


def test_a_fail_closed_production_deployment_boots():
    """The default posture is the passing one — the guard must not block the good case."""
    _strong(app_env="prod", budget_fail_open=False).ensure_spend_caps_bind()


def test_non_dev_refuses_to_boot_with_no_governance_hook_at_the_gateway(monkeypatch):
    """The other half of the gap: caps configured, but nothing enforcing them.

    A cap binds at exactly one seam — the gateway's governance hook. Losing that
    wiring uncaps the whole fleet while every budget row still reads as configured,
    which is the silent version of the same failure.
    """
    from aegis.gateway import llm as gateway_llm

    monkeypatch.setattr(gateway_llm, "_governance", gateway_llm._NoOpGovernance())
    with pytest.raises(InsecureConfigurationError, match="No budget-governance hook"):
        _strong(app_env="prod").ensure_spend_caps_bind()


def test_create_app_fails_fast_on_fail_open_budgets(monkeypatch):
    """End to end at the composition root, not only on the method."""
    import app.main as main

    bad = _strong(app_env="prod", budget_fail_open=True)
    monkeypatch.setattr(main, "get_settings", lambda: bad)
    with pytest.raises(InsecureConfigurationError, match="BUDGET_FAIL_OPEN"):
        main.create_app()
