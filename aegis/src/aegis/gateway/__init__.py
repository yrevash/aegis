"""Aegis gateway — the LiteLLM chokepoint, standalone and LLM-fleet-agnostic.

`complete`/`embed`/`transcribe` route by :class:`~aegis.core.models.ModelRole`
through a custom OpenAI-compatible provider, with role fallbacks, timeouts, cost
accounting in the call's own billable units (tokens, audio-minutes, images) + a
measured small-model-routing savings tally, and a single corrective
structured-output re-ask. Budget/rate governance and observability
are **injected hooks** (see :func:`configure`) — this package has no policy or
OTel dependency of its own; both default to a documented no-op.

``litellm`` is imported lazily (inside :mod:`aegis.gateway.llm`), so
``import aegis.gateway`` never requires it — install ``aegis[gateway]`` and
call :func:`configure` to actually run calls.

Standalone usage::

    from aegis.gateway import complete, configure
    from aegis.core.models import ModelRole

    configure(config=my_config)  # or rely on GATEWAY_* env vars
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
"""

from __future__ import annotations

from aegis.gateway.llm import (
    complete,
    configure,
    embed,
    last_trace_id,
    optimization_config,
    optimization_summary,
    record_call,
    transcribe,
    usage_tally,
)
from aegis.gateway.types import (
    BudgetExceededError,
    CostSource,
    LLMResult,
    ToolCallResult,
    TranscriptionResult,
    TranscriptionSegment,
    Usage,
)

__all__ = [
    "BudgetExceededError",
    "CostSource",
    "LLMResult",
    "ToolCallResult",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Usage",
    "complete",
    "configure",
    "embed",
    "last_trace_id",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "transcribe",
    "usage_tally",
]
