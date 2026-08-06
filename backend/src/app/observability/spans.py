"""A tiny, dependency-free span helper for the non-LLM units of an agent run.

:func:`span` opens one OpenTelemetry span stamped with the OpenInference
``openinference.span.kind`` attribute (AGENT/CHAIN/TOOL/RETRIEVER/RERANKER/
GUARDRAIL/…) so Phoenix renders the whole run as a nested tree — retrieval,
reranking, guardrails, per-graph-node work and each tool call, not just the LLM
and embedding calls. It is the counterpart to :func:`app.observability.genai_span`
(which owns the ``gen_ai.*`` LLM/embedding spans).

Everything degrades to a **no-op** when no tracer/Phoenix is configured: before
:func:`app.observability.init_observability` runs (or in offline "lite" mode and
in tests), :func:`app.observability.get_tracer` resolves against OTel's global
no-op provider, so :func:`span` returns a non-recording span and never touches the
network. ``set_span_attribute(s)`` on a non-recording span is a safe no-op too.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import Span, Status, StatusCode

from . import semconv
from .otel import get_tracer

__all__ = ["set_span_attribute", "set_span_attributes", "span"]

_AttrValue = str | bool | int | float


@contextmanager
def span(
    kind: semconv.SpanKind,
    name: str,
    *,
    attributes: Mapping[str, _AttrValue] | None = None,
) -> Iterator[Span]:
    """Open a nested span of OpenInference ``kind`` named ``name``.

    The span becomes the current span for its ``with`` block, so any span opened
    inside it (a deeper unit of work, or an LLM ``gen_ai.*`` span) nests beneath it
    and the trace reads as a tree. Exceptions are recorded on the span and
    re-raised; the span status is left OK otherwise.

    Args:
        kind: The OpenInference span kind (drives how Phoenix renders the node).
        name: The span name (e.g. ``"retrieve"``, ``"guardrail.input"``).
        attributes: Optional attributes stamped on the span at open time.

    Yields:
        The active :class:`~opentelemetry.trace.Span` (may be non-recording when no
        tracer is configured — all writes are then safe no-ops).
    """
    tracer = get_tracer()
    with tracer.start_as_current_span(name) as active:
        active.set_attribute(semconv.OPENINFERENCE_SPAN_KIND, kind.value)
        if attributes:
            set_span_attributes(attributes, span=active)
        try:
            yield active
        except Exception as exc:
            active.record_exception(exc)
            active.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


def set_span_attribute(
    key: str, value: _AttrValue | None, *, span: Span | None = None
) -> None:
    """Set one attribute on ``span`` (or the current span); ``None`` is skipped.

    Safe no-op when the target span is non-recording (no tracer configured).
    """
    if value is None:
        return
    target = span if span is not None else trace.get_current_span()
    target.set_attribute(key, value)


def set_span_attributes(
    attributes: Mapping[str, Any], *, span: Span | None = None
) -> None:
    """Set many attributes on ``span`` (or the current span); ``None`` values skip."""
    target = span if span is not None else trace.get_current_span()
    for key, value in attributes.items():
        if value is not None:
            target.set_attribute(key, value)
