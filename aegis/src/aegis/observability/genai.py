"""Helpers for emitting OpenTelemetry GenAI (``gen_ai.*``) spans.

Provides an async context manager, :func:`genai_span`, that opens a span named per
the GenAI convention (``"{operation} {model}"``), stamps the standard request
attributes, and records errors — plus :func:`set_usage` to attach token counts
and cost when the call returns.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager

from opentelemetry.trace import Span, Status, StatusCode

from . import semconv
from .otel import get_tracer
from .semconv import GenAIOperation

__all__ = ["GenAIOperation", "genai_span", "genai_span_sync", "set_usage"]


#: Map a GenAI operation onto its OpenInference span kind so Phoenix renders the
#: model call as an LLM (or EMBEDDING) node in the same tree as the other spans.
_OPERATION_SPAN_KIND: dict[semconv.GenAIOperation, semconv.SpanKind] = {
    semconv.GenAIOperation.CHAT: semconv.SpanKind.LLM,
    semconv.GenAIOperation.TEXT_COMPLETION: semconv.SpanKind.LLM,
    semconv.GenAIOperation.EMBEDDINGS: semconv.SpanKind.EMBEDDING,
    # Phoenix has no audio kind; a transcription is a model call, so it renders as
    # an LLM node alongside the chat spans in the same trace tree.
    semconv.GenAIOperation.TRANSCRIPTION: semconv.SpanKind.LLM,
}


def _stamp_request(
    span: Span,
    *,
    operation: semconv.GenAIOperation,
    model: str,
    temperature: float | None,
    provider: str,
    max_tokens: int | None = None,
) -> None:
    """Set the standard ``gen_ai.*`` request attributes on ``span``."""
    span.set_attribute(
        semconv.OPENINFERENCE_SPAN_KIND,
        _OPERATION_SPAN_KIND.get(operation, semconv.SpanKind.LLM).value,
    )
    span.set_attribute(semconv.GEN_AI_OPERATION_NAME, operation.value)
    span.set_attribute(semconv.GEN_AI_PROVIDER_NAME, provider)
    span.set_attribute(semconv.GEN_AI_SYSTEM, provider)  # deprecated alias
    span.set_attribute(semconv.GEN_AI_REQUEST_MODEL, model)
    if temperature is not None:
        span.set_attribute(semconv.GEN_AI_REQUEST_TEMPERATURE, temperature)
    if max_tokens is not None:
        span.set_attribute(semconv.GEN_AI_REQUEST_MAX_TOKENS, max_tokens)


def set_usage(
    span: Span,
    *,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost_usd: float | None = None,
    response_model: str | None = None,
) -> None:
    """Record token usage, cost and the responding model on ``span``.

    Args:
        span: The active GenAI span.
        input_tokens: Prompt/input token count.
        output_tokens: Completion/output token count.
        cost_usd: Computed USD cost for the call.
        response_model: The model that actually responded.
    """
    if input_tokens is not None:
        span.set_attribute(semconv.GEN_AI_USAGE_INPUT_TOKENS, input_tokens)
    if output_tokens is not None:
        span.set_attribute(semconv.GEN_AI_USAGE_OUTPUT_TOKENS, output_tokens)
    if cost_usd is not None:
        span.set_attribute(semconv.GEN_AI_USAGE_COST, cost_usd)
    if response_model is not None:
        span.set_attribute(semconv.GEN_AI_RESPONSE_MODEL, response_model)


@contextmanager
def genai_span_sync(
    operation: semconv.GenAIOperation,
    model: str,
    *,
    temperature: float | None = None,
    provider: str = semconv.DEFAULT_PROVIDER,
    max_tokens: int | None = None,
) -> Iterator[Span]:
    """Open a GenAI span synchronously (see :func:`genai_span` for details)."""
    tracer = get_tracer()
    span_name = f"{operation.value} {model}"
    with tracer.start_as_current_span(span_name) as span:
        _stamp_request(
            span,
            operation=operation,
            model=model,
            temperature=temperature,
            provider=provider,
            max_tokens=max_tokens,
        )
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, str(exc)))
            raise


@asynccontextmanager
async def genai_span(
    operation: semconv.GenAIOperation,
    model: str,
    *,
    temperature: float | None = None,
    provider: str = semconv.DEFAULT_PROVIDER,
    max_tokens: int | None = None,
) -> AsyncIterator[Span]:
    """Open a ``gen_ai.*`` span around an LLM/embedding call (async).

    The span is named ``"{operation} {model}"`` per the GenAI convention and is
    pre-stamped with the request attributes. Exceptions are recorded on the span
    and re-raised. Attach usage with :func:`set_usage` before the block exits.

    Args:
        operation: The GenAI operation (chat, embeddings, …).
        model: The requested model/deployment id.
        temperature: Sampling temperature, if applicable.
        provider: ``gen_ai.provider.name`` value; defaults to the TCS GenAI Lab gateway.
        max_tokens: Requested output-token ceiling, stamped as
            ``gen_ai.request.max_tokens`` when set.

    Yields:
        The active :class:`~opentelemetry.trace.Span`.
    """
    with genai_span_sync(
        operation,
        model,
        temperature=temperature,
        provider=provider,
        max_tokens=max_tokens,
    ) as span:
        yield span
