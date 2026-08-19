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

:class:`GatewayConfig`, :class:`GovernanceHook` and :class:`ObservabilitySink` are
re-exported here because they are :func:`configure`'s parameter types: a host writing a
budget hook has to name the Protocol it satisfies, and reaching into
``aegis.gateway.llm`` for it means binding to a submodule path that ``PUBLIC.md`` calls
internal. They lived only in the submodule, so ``aegis.runtime``'s own
``TYPE_CHECKING`` import of all three named nothing the package exported — a type-only
import that no test could fail, and that a type checker (and ``pdoc``) reported as
undefined on ``Aegis.from_env`` itself.

Standalone usage::

    from aegis.gateway import complete, configure
    from aegis.core.models import ModelRole

    configure(config=my_config)  # or rely on GATEWAY_* env vars
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
"""

from __future__ import annotations

from aegis.gateway.limiter import (
    LocalSlotLimiter,
    NoSlotLimiter,
    RedisSlotLimiter,
    SlotLimiter,
    SlotUnavailableError,
    lease_seconds_for,
)
from aegis.gateway.llm import (
    GatewayConfig,
    GovernanceHook,
    ObservabilitySink,
    complete,
    configure,
    embed,
    last_trace_id,
    limiter_status,
    max_call_hold_seconds,
    optimization_config,
    optimization_summary,
    record_call,
    reset_usage_tally,
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
    "GatewayConfig",
    "GovernanceHook",
    "LLMResult",
    "LocalSlotLimiter",
    "NoSlotLimiter",
    "ObservabilitySink",
    "RedisSlotLimiter",
    "SlotLimiter",
    "SlotUnavailableError",
    "ToolCallResult",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Usage",
    "complete",
    "configure",
    "embed",
    "last_trace_id",
    "lease_seconds_for",
    "limiter_status",
    "max_call_hold_seconds",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "transcribe",
    "reset_usage_tally",
    "usage_tally",
]
