"""Strangler shim: tracer-provider setup now lives in ``aegis.observability.otel``.

Delegates :func:`init_observability` to it, injecting ``phoenix_enabled`` from
``app.config.get_settings()`` — the one host coupling severed in the
``aegis.observability`` extraction. Service/project names are pinned to this
app's original hardcoded values so behaviour is unchanged.

:func:`get_tracer` and :func:`current_trace_id` are re-exported directly: the
tracer-provider state (``_provider``) now lives solely in
``aegis.observability.otel``, so both this module and the standalone package
resolve against the exact same provider.
"""

from __future__ import annotations

from typing import Any

from aegis.observability.otel import current_trace_id, get_tracer

from app.config import get_settings

__all__ = ["current_trace_id", "get_tracer", "init_observability"]

_SERVICE_NAME = "taif-backend"
_PROJECT_NAME = "taif"


def init_observability(app: Any = None) -> None:  # noqa: ANN401 - FastAPI app, optional
    """Initialise tracing: launch local Phoenix and register the tracer provider.

    Safe to call once at startup. Delegates to
    ``aegis.observability.otel.init_observability`` with ``phoenix_enabled`` read
    from this app's settings; falls back to a console exporter if Phoenix is
    disabled or unavailable.

    Args:
        app: The FastAPI application (accepted for a conventional
            ``init_observability(app)`` signature; unused directly here).
    """
    from aegis.observability.otel import init_observability as _init_observability

    settings = get_settings()
    _init_observability(
        phoenix_enabled=settings.phoenix_enabled,
        service_name=_SERVICE_NAME,
        project_name=_PROJECT_NAME,
        app=app,
    )
