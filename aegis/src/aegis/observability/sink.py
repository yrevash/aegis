"""The concrete ``aegis.gateway`` ``ObservabilitySink`` — OTel/Phoenix-backed.

``aegis.gateway.llm`` defines an ``ObservabilitySink`` Protocol (``span`` /
``set_usage`` / ``trace_id``) so the gateway itself has no OTel dependency; a
host wires a concrete implementation via ``gateway.configure(observability=...)``.
:class:`OtelObservabilitySink` is that implementation, backed by this module's
:func:`~aegis.observability.genai.genai_span` / :func:`~aegis.observability.genai.set_usage`
/ :func:`~aegis.observability.otel.current_trace_id` — so a host does::

    from aegis.gateway import configure
    from aegis.observability import OtelObservabilitySink

    configure(observability=OtelObservabilitySink())

with no bespoke adapter of its own.
"""

from __future__ import annotations

from typing import Any

from .genai import GenAIOperation, genai_span, set_usage
from .otel import current_trace_id

__all__ = ["OtelObservabilitySink"]


class OtelObservabilitySink:
    """Wires OTel ``gen_ai.*`` spans into the gateway's ``ObservabilitySink`` seam.

    Structurally satisfies ``aegis.gateway.llm.ObservabilitySink`` (a
    ``@runtime_checkable`` Protocol) — no inheritance or import of the gateway
    package is required here.
    """

    def span(
        self,
        operation: Any,  # noqa: ANN401 - aegis.gateway.llm.GenAIOperation, kept opaque here
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:  # noqa: ANN401 - the async context manager returned by genai_span
        """Open a ``gen_ai.*`` span for ``operation``/``model`` (see ``genai_span``).

        ``operation.value`` is ``"chat"``/``"embeddings"`` — identical to this
        module's own :class:`~aegis.observability.semconv.GenAIOperation` values,
        so the gateway's operation enum maps here with a trivial value lookup and
        no import of the gateway's own enum type.
        """
        return genai_span(
            GenAIOperation(operation.value),
            model,
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def set_usage(
        self,
        span: Any,  # noqa: ANN401 - the opaque span yielded by `span`
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_usd: float | None = None,
        response_model: str | None = None,
    ) -> None:
        """Record token usage, cost and the responding model on ``span``."""
        set_usage(
            span,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            response_model=response_model,
        )

    def trace_id(self) -> str | None:
        """Return the active OTel trace id (hex), for audit correlation."""
        return current_trace_id()
