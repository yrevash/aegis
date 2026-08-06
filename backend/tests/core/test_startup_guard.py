"""Fail-fast startup guard against an insecure JWT signing secret (H4).

Dev keeps the non-secret fallback (so the offline/test path stays quiet); any other
environment refuses to boot on a default or too-short ``JWT_SECRET``.
"""

from __future__ import annotations

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


def test_non_dev_accepts_strong_secret():
    Settings(app_env="prod", jwt_secret="x" * 48).ensure_secure_secrets()


def test_create_app_fails_fast_on_insecure_secret(monkeypatch):
    import app.main as main

    bad = Settings(app_env="prod", jwt_secret=DEFAULT_JWT_SECRET)
    monkeypatch.setattr(main, "get_settings", lambda: bad)
    with pytest.raises(InsecureConfigurationError):
        main.create_app()
