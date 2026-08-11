"""Typed, fail-fast configuration and the explicit infra mode.

``AEGIS_MODE`` selects backends deliberately: ``full`` (default) requires real
Redis + Postgres and refuses to boot without them; ``lite`` opts into in-memory
implementations loudly; ``auto`` probes then drops to lite but stays loud. There
is no silent fallback — degradation is always a named, surfaced choice.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict


class AegisMode(StrEnum):
    """How Aegis chooses its backing infrastructure."""

    full = "full"
    lite = "lite"
    auto = "auto"


class CoreSettings(BaseSettings):
    """Core configuration, read from ``AEGIS_``-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix="AEGIS_", extra="ignore")

    mode: AegisMode = AegisMode.full
    redis_url: str | None = None
    database_url: str | None = None

    def require_full_infra(self) -> None:
        """Raise if ``mode`` is ``full`` but a required backend URL is unset.

        Raises:
            RuntimeError: naming the missing variables and the lite escape hatch.
        """
        if self.mode is not AegisMode.full:
            return
        missing = [
            name
            for name, value in (("REDIS_URL", self.redis_url), ("DATABASE_URL", self.database_url))
            if not value
        ]
        if missing:
            raise RuntimeError(
                f"AEGIS_MODE=full requires {missing}. Set them, or set "
                f"AEGIS_MODE=lite to run in-memory (non-durable)."
            )
