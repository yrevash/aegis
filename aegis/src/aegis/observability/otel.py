"""Tracer-provider setup and Phoenix export (local, in-process — no Docker).

``init_observability(...)`` wires an OpenTelemetry tracer provider that exports
``gen_ai.*`` spans to a **local, in-process** Arize Phoenix instance. Everything
degrades gracefully: if Phoenix (or ``phoenix.otel``) is not installed, we fall
back to a plain OTel SDK provider with a console exporter so the host still runs
and spans are still produced.

**Config is injected** — ``phoenix_enabled``/``service_name``/``project_name``
are call-time arguments, not read from any host settings module (the one
coupling this module had to a specific host application before extraction).

Verified against: ``arize-phoenix`` 9.x / ``arize-phoenix-otel`` 0.16.x
(``phoenix.otel.register``) and ``opentelemetry-sdk`` 1.27+, August 2026.
"""

from __future__ import annotations

import logging
from typing import Any

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.trace import Tracer

logger = logging.getLogger(__name__)

_TRACER_NAME = "app"

# Set once by ``init_observability`` so ``get_tracer`` returns a provider-bound
# tracer even if the global provider is left untouched.
_provider: TracerProvider | None = None


def _build_fallback_provider(service_name: str) -> TracerProvider:
    """Build a plain OTel SDK provider with a console exporter (no Phoenix).

    Uses a **synchronous** ``SimpleSpanProcessor`` rather than the batched one: the
    console exporter is only a dev/offline fallback, and a background batch-flush
    thread otherwise races the interpreter's stdout at teardown (a stray
    ``ValueError: I/O operation on closed file`` after a test run). Synchronous
    export removes that race with no functional loss.
    """
    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    return provider


def _launch_phoenix() -> None:
    """Start a local in-process Phoenix server if it is not already running."""
    import phoenix as px

    if px.active_session() is None:
        session = px.launch_app()
        logger.info("Phoenix UI available at %s", getattr(session, "url", "?"))


def init_observability(
    *,
    phoenix_enabled: bool = True,
    service_name: str = "taif",
    project_name: str = "taif",
    app: Any = None,  # noqa: ANN401 - host app object (e.g. FastAPI), optional
) -> None:
    """Initialise tracing: launch local Phoenix and register the tracer provider.

    Safe to call once at startup. If ``phoenix_enabled`` is false, or if Phoenix
    / its OTel bridge are unavailable (``aegis[phoenix]`` not installed), a
    console-exporting SDK provider is installed instead so tracing still works
    offline.

    Args:
        phoenix_enabled: Whether to launch/wire local Phoenix. Injected by the
            host (was a hard-coded read of a host settings singleton before
            this module was extracted); defaults to enabled.
        service_name: The OTel ``service.name`` resource attribute used by the
            fallback (console-exporter) provider.
        project_name: The Phoenix project name spans are grouped under.
        app: Accepted for a conventional ``init_observability(app)`` call shape
            (e.g. a FastAPI application); unused directly here.
    """
    global _provider

    if not phoenix_enabled:
        _provider = _build_fallback_provider(service_name)
        logger.info("Phoenix disabled; using console span exporter.")
        return

    try:
        from phoenix.otel import register

        _launch_phoenix()
        _provider = register(
            project_name=project_name,
            batch=True,
            set_global_tracer_provider=True,
        )
        logger.info("OpenTelemetry tracing wired to local Phoenix.")
    except Exception:  # pragma: no cover - depends on optional heavy deps
        logger.warning(
            "Phoenix/phoenix-otel unavailable; falling back to console exporter.",
            exc_info=True,
        )
        _provider = _build_fallback_provider(service_name)


def get_tracer() -> Tracer:
    """Return the application's OTel tracer (emitting ``gen_ai.*`` spans).

    Works whether or not :func:`init_observability` has run — before init it
    resolves against the OTel global provider (a no-op provider until set).
    """
    if _provider is not None:
        return _provider.get_tracer(_TRACER_NAME)
    return trace.get_tracer(_TRACER_NAME)


def current_trace_id() -> str | None:
    """Return the active span's trace id as 32-char hex, or ``None`` if unset."""
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None
    return format(ctx.trace_id, "032x")
