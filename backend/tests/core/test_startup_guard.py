"""Fail-fast startup guard against an insecure JWT signing secret (H4).

Dev keeps the non-secret fallback (so the offline/test path stays quiet); any other
environment refuses to boot on a default or too-short ``JWT_SECRET``.
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
