"""Injection-classifier cache with an explicit, honest backend choice.

In-memory is returned ONLY when the mode is ``lite``/``auto``. In ``full`` mode a
real Redis client must be supplied; its absence raises rather than silently
degrading. There is no ``except -> in-memory`` path.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from aegis.core.config import AegisMode

logger = logging.getLogger(__name__)


class InjectionCache(Protocol):
    """A minimal key→value cache for classifier verdicts."""

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None``."""
        ...

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        ...


class InMemoryInjectionCache:
    """A process-local dict cache (lite/tests only — non-durable)."""

    def __init__(self) -> None:
        """Initialise an empty cache."""
        self._data: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None``."""
        return self._data.get(key)

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self._data[key] = value


def make_injection_cache(
    mode: AegisMode, *, redis_client: Any | None = None  # noqa: ANN401
) -> InjectionCache:
    """Select the injection cache backend by explicit mode.

    Raises:
        RuntimeError: if ``mode`` is ``full`` and no ``redis_client`` is supplied.
    """
    if mode is AegisMode.full:
        if redis_client is None:
            raise RuntimeError(
                "AEGIS_MODE=full requires a Redis client for the injection cache. "
                "Provide one, or set AEGIS_MODE=lite for an in-memory cache."
            )
        logger.info("InjectionCache: Redis-backed (durable).")
        return _RedisInjectionCache(redis_client)
    logger.warning(
        "InjectionCache: in-memory selected (AEGIS_MODE=%s, non-durable).", mode.value
    )
    return InMemoryInjectionCache()


class _RedisInjectionCache:
    """Redis-backed cache (full mode)."""

    def __init__(self, client: Any) -> None:  # noqa: ANN401
        """Wrap a redis client exposing sync ``get``/``set``."""
        self._client = client

    def get(self, key: str) -> str | None:
        """Return the cached value for ``key`` or ``None``."""
        value = self._client.get(key)
        return value.decode() if isinstance(value, bytes) else value

    def set(self, key: str, value: str) -> None:
        """Store ``value`` under ``key``."""
        self._client.set(key, value)
