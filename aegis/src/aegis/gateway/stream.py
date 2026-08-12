"""AG-UI streaming for the gateway — emits one ``model_call`` event per call.

Wraps :func:`~aegis.gateway.llm.complete` in a ``STEP_STARTED``/``STEP_FINISHED``
bracket (span kind ``LLM``), emitting a ``MODEL_CALL`` custom event carrying the
model, role, the role's intended primary model + whether a fallback fired, token
usage, actual cost, and the measured saving from small-model routing for *this*
call. Callers opt in — the gateway itself never
streams (``complete``/``embed`` are also called by non-agentic code paths, e.g.
an eval harness), so this is a thin à la carte helper, not a wrapper every
caller must use.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.core.models import ModelRole
from aegis.gateway.llm import complete, usage_tally
from aegis.gateway.routing import is_small_model, model_for
from aegis.gateway.types import LLMResult

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

_STEP_NAME = "llm"


async def stream_complete(
    role: ModelRole,
    messages: list[dict],
    emitter: AegisEmitter,
    **kwargs: object,
) -> LLMResult:
    """Run `complete` for `role`, streaming a `model_call` event over `emitter`.

    Args:
        role: The job to route (forwarded to `complete`).
        messages: OpenAI-style chat messages.
        emitter: The AG-UI emitter for streaming events.
        **kwargs: Forwarded to `complete` (`tools`, `temperature`,
            `response_format`, `max_tokens`).

    Returns:
        The full `LLMResult` from `complete`.
    """
    async with emitter.step(_STEP_NAME, SpanKind.LLM):
        # Snapshot the cumulative tally so the payload carries THIS call's
        # measured saving, not the process-wide running total.
        saved_before = usage_tally()["cost_saved_usd"]
        result = await complete(role, messages, **kwargs)  # type: ignore[arg-type]
        saved_after = usage_tally()["cost_saved_usd"]

        # The role's intended primary vs. the deployment that actually responded:
        # if they differ, a per-role fallback fired inside the gateway. Measured
        # from the real ``response.model``, not guessed.
        primary_model = model_for(role)
        await emitter.custom(
            stream_names.MODEL_CALL,
            {
                "model": result.model,
                "role": role.value,
                "primary_model": primary_model,
                "fallback_fired": bool(result.model and result.model != primary_model),
                "prompt_tokens": result.usage.prompt_tokens,
                "completion_tokens": result.usage.completion_tokens,
                "cost_usd": result.usage.cost_usd,
                "cost_saved_usd": max(0.0, saved_after - saved_before),
                "small_model": is_small_model(result.model),
            },
        )
    return result
