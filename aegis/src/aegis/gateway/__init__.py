"""Aegis gateway — the LiteLLM chokepoint, standalone and LLM-fleet-agnostic.

`complete`/`embed` route by :class:`~aegis.core.models.ModelRole` through a
custom OpenAI-compatible provider, with role fallbacks, timeouts, cost
accounting + a measured small-model-routing savings tally, and a single
corrective structured-output re-ask. Budget/rate governance and observability
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
    usage_tally,
)
from aegis.gateway.types import BudgetExceededError, LLMResult, ToolCallResult, Usage

__all__ = [
    "BudgetExceededError",
    "LLMResult",
    "ToolCallResult",
    "Usage",
    "complete",
    "configure",
    "embed",
    "last_trace_id",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "usage_tally",
]
