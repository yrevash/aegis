"""Typed, fail-fast configuration and the explicit infra mode.

``AEGIS_MODE`` selects backends deliberately: ``full`` (default) requires real
Redis + Postgres + a usable on-disk vector store and refuses to boot without them;
``lite`` opts into in-memory/embedded implementations loudly; ``auto`` **actually
probes** the configured backends (:meth:`CoreSettings.resolve_mode`) and drops to
lite only on a real, logged failure. There is no silent fallback — degradation is
always a named, surfaced choice.

``auto`` is resolved asynchronously because probing is I/O. A host must therefore
``await settings.resolve_mode()`` at startup and use the returned mode; the raw
``settings.mode`` is the *declared* mode, not the resolved one.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

#: The environment variable prefix every setting below is read from. The names in
#: error/log messages are built from it so they name the variable an operator must
#: actually set (``AEGIS_REDIS_URL``), not the bare field name.
ENV_PREFIX = "AEGIS_"


class AegisMode(StrEnum):
    """How Aegis chooses its backing infrastructure."""

    full = "full"
    lite = "lite"
    auto = "auto"


class CoreSettings(BaseSettings):
    """Core configuration, read from ``AEGIS_``-prefixed environment variables."""

    model_config = SettingsConfigDict(env_prefix=ENV_PREFIX, extra="ignore")

    mode: AegisMode = AegisMode.full
    redis_url: str | None = None
    database_url: str | None = None
    #: Filesystem directory for the embedded vector store (Chroma's ``PersistentClient``
    #: — the ANN engine behind retrieval + memory recall). It is a *path*, not a URL,
    #: because the vector tier runs in-process: there is no server binary to install,
    #: which is what makes Aegis deployable on a locked-down enterprise machine.
    #: In full mode it is a hard dependency and must be set explicitly — leaving it unset
    #: would mean an ephemeral in-RAM index, i.e. exactly the silent, non-durable
    #: degradation this module exists to prevent. An in-memory store is available only as
    #: an explicit dev/test choice (``AEGIS_MODE=lite``).
    vector_store_path: str | None = None

    def _missing_backends(self) -> list[str]:
        """Return the unset backend variables, named as the operator must set them."""
        return [
            f"{ENV_PREFIX}{name}"
            for name, value in (
                ("REDIS_URL", self.redis_url),
                ("DATABASE_URL", self.database_url),
                ("VECTOR_STORE_PATH", self.vector_store_path),
            )
            if not value
        ]

    def require_full_infra(self) -> None:
        """Raise if ``mode`` is ``full`` but a required backend URL is unset.

        In ``auto`` this cannot raise (dropping to lite is the point) but it still
        **says so**: the missing variables are logged at WARNING, because "auto quietly
        became lite" is exactly the silent degradation this module exists to prevent.

        Raises:
            RuntimeError: naming the missing variables and the lite escape hatch.
        """
        if self.mode is AegisMode.lite:
            return
        missing = self._missing_backends()
        if not missing:
            return
        if self.mode is AegisMode.auto:
            logger.warning(
                "AEGIS_MODE=auto: %s unset, so those backends cannot be probed; "
                "resolve_mode() will select lite (in-memory, non-durable).",
                ", ".join(missing),
            )
            return
        raise RuntimeError(
            f"AEGIS_MODE=full requires {missing}. Set them, or set "
            f"AEGIS_MODE=lite to run in-memory (non-durable)."
        )

    async def resolve_mode(self) -> AegisMode:
        """Resolve the *declared* mode into the mode this process can actually run in.

        ``full`` and ``lite`` are taken at their word (``full`` still enforces
        :meth:`require_full_infra`). ``auto`` is the only one that probes — and it does
        genuinely probe, via :mod:`aegis.core.health`, rather than assuming: every
        configured backend must answer for ``auto`` to resolve to ``full``. A single
        unset URL or unreachable backend resolves to ``lite`` **and logs which one and
        why**, so a degraded process is never a quiet one.

        Returns:
            The effective :class:`AegisMode`. Hosts must use *this*, not ``self.mode``.
        """
        if self.mode is not AegisMode.auto:
            self.require_full_infra()
            return self.mode

        missing = self._missing_backends()
        if missing:
            logger.warning(
                "AEGIS_MODE=auto resolved to LITE (in-memory, non-durable): %s unset.",
                ", ".join(missing),
            )
            return AegisMode.lite

        from aegis.core.health import probe_postgres, probe_redis, probe_vector_store

        results = [
            await probe_redis(str(self.redis_url)),
            await probe_postgres(str(self.database_url)),
            await probe_vector_store(str(self.vector_store_path)),
        ]
        down = [r for r in results if r.status == "down"]
        if down:
            logger.warning(
                "AEGIS_MODE=auto resolved to LITE (in-memory, non-durable): %s.",
                "; ".join(f"{r.name} unreachable ({r.detail})" for r in down),
            )
            return AegisMode.lite
        logger.info(
            "AEGIS_MODE=auto resolved to FULL: redis, postgres and the vector store "
            "all answered."
        )
        return AegisMode.full
