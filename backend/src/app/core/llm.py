"""Backend shim: the LiteLLM gateway now lives in the standalone ``aegis.gateway``.

This module used to own ``complete``/``embed`` — the single async entry point to
the TCS GenAI Lab custom OpenAI-compatible provider — plus role fallbacks,
timeouts, cost accounting, the small-model-routing savings tally, and the
structured-output re-ask. That implementation has moved to the standalone,
host-agnostic ``aegis.gateway`` package (see ``/aegis``) so it can be imported
by anything without pulling in this platform's settings, governance, or
observability stack. This module is the **strangler shim**: at import time it
wires ``aegis.gateway.configure(...)`` with three adapters —

* a :class:`GatewayConfig` reading live values off ``app.config.get_settings()``
  (a property per field, not a snapshot, so a test that mutates the settings
  singleton — e.g. ``monkeypatch.setattr(get_settings(), "budget_fail_open",
  True)`` — is honoured on the next call);
* a governance hook wrapping ``app.core.governance.get_governance_context`` +
  the lazy ``app.data.governance.enforce_governance``/``record_usage`` calls,
  preserving the exact fail-closed-unless-``budget_fail_open`` semantics of the
  original ``_enforce_governance``, and the "ungoverned (no tenant) request is a
  full no-op" semantics of the original ``_governed`` gate;
* the standalone ``aegis.observability.OtelObservabilitySink`` — the concrete
  implementation of the gateway's ``ObservabilitySink`` Protocol, wired
  directly with no bespoke adapter of this app's own.

then re-exports the public surface (``complete``, ``embed``, ``record_call``,
``usage_tally``, ``last_trace_id``, ``BudgetExceededError``, ``LLMResult``,
``ToolCallResult``, ``Usage``) so every existing call site (agent/deps,
retrieval/gateway, guardrails, memory, ops/eval, platform ``usage_tally``) keeps
working unchanged. ``app.core.models.ModelRole`` is the separate, still-thin
shim over ``aegis.core.models``/``aegis.gateway.routing``.
"""

from __future__ import annotations

import logging

import aegis.gateway as gateway
from aegis.gateway import (
    BudgetExceededError,
    LLMResult,
    ToolCallResult,
    Usage,
    complete,
    embed,
    last_trace_id,
    optimization_config,
    optimization_summary,
    record_call,
    usage_tally,
)
from aegis.observability import OtelObservabilitySink

from app.config import get_settings
from app.core.governance import GovernanceContext, get_governance_context

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetExceededError",
    "LLMResult",
    "ToolCallResult",
    "Usage",
    "complete",
    "embed",
    "last_trace_id",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "usage_tally",
]


class _SettingsGatewayConfig:
    """Adapts the live ``app.config.Settings`` singleton to ``GatewayConfig``.

    Every field is a property reading ``get_settings()`` fresh on each access
    (rather than a value captured once at ``configure()`` time), so mutating the
    settings singleton in a test or at runtime is picked up on the next call —
    matching how the pre-shim ``app.core.llm`` read ``get_settings()`` directly.
    """

    @property
    def base_url(self) -> str:
        """The GenAI Lab gateway's base URL."""
        return get_settings().genailab_base_url

    @property
    def api_key(self) -> str:
        """The GenAI Lab gateway's API key."""
        return get_settings().genailab_api_key

    @property
    def ssl_verify(self) -> bool:
        """Whether to verify the gateway's (self-signed) TLS certificate."""
        return get_settings().genailab_ssl_verify

    @property
    def max_output_tokens(self) -> int:
        """The default per-call output-token ceiling."""
        return get_settings().llm_max_output_tokens

    @property
    def timeout_seconds(self) -> float:
        """The per-attempt wall-clock timeout."""
        return get_settings().llm_timeout_seconds

    @property
    def budget_fail_open(self) -> bool:
        """Whether a budget-enforcement read failure fails open (vs. closed)."""
        return get_settings().budget_fail_open


def _governed(ctx: GovernanceContext | None) -> GovernanceContext | None:
    """Return the context iff the request is tenant-governed, else ``None``.

    Enforcement and ledgering are fully gated behind a bound tenant: an unscoped
    request (no governance context, the default for every existing flow and the
    offline demo) skips the database entirely and behaves exactly as before.
    """
    if ctx is None or ctx.tenant_id is None:
        return None
    return ctx


class _GovernanceHook:
    """Wires the platform's budget/rate governance into the gateway's hook seam."""

    def get_context(self) -> GovernanceContext | None:
        """Return the bound :class:`GovernanceContext`, or ``None`` if ungoverned."""
        return _governed(get_governance_context())

    async def enforce(self, ctx: GovernanceContext) -> None:
        """Raise :class:`BudgetExceededError` if the principal is over any cap.

        Reads the authoritative ``Budget`` rows and ``UsageLedger`` sums for the
        tenant→user path. A real budget breach always propagates. An
        enforcement **error** (e.g. a database blip) fails **CLOSED** by
        default — the call is denied rather than silently uncapped, so a
        transient failure can never disable every spend cap. Set
        ``budget_fail_open`` to opt into soft/fail-open ceilings.
        """
        try:
            from app.data.governance import enforce_governance
        except ImportError:
            # Only a genuinely-absent data layer (offline lite) is a clean
            # no-op. A non-import failure (a real bug in the governance module)
            # must NOT silently disable budget enforcement — let it surface /
            # fall through to fail-closed below.
            return
        try:
            await enforce_governance(tenant_id=ctx.tenant_id, user_id=ctx.user_id)
        except BudgetExceededError:
            raise
        except Exception as exc:  # noqa: BLE001 - a DB blip must not silently uncap
            if get_settings().budget_fail_open:
                logger.warning(
                    "Budget enforcement read failed; failing open (configured).",
                    exc_info=True,
                )
                return
            logger.error("Budget enforcement read failed; failing closed (denying).")
            raise BudgetExceededError(
                scope="tenant",
                scope_id=ctx.tenant_id,
                limit_type="enforcement_error",
                limit=None,
                used=None,
                message="Budget enforcement unavailable; denying the call (fail-closed).",
            ) from exc

    async def record(
        self,
        ctx: GovernanceContext,
        *,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        trace_id: str | None,
    ) -> None:
        """Write one durable usage-ledger row for a governed call.

        The gateway calls this best-effort (a failure is swallowed and logged
        by the caller, never raised) — see ``aegis.gateway.llm._record_usage``.
        """
        from app.data.governance import record_usage

        await record_usage(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            trace_id=trace_id,
        )


# Wire the injected hooks once, at import time — every existing call site
# (`from app.core.llm import complete`, etc.) then goes through the real gateway
# with this platform's config/governance bound in, and the standalone
# `aegis.observability.OtelObservabilitySink` wired with no bespoke adapter.
gateway.configure(
    config=_SettingsGatewayConfig(),
    governance=_GovernanceHook(),
    observability=OtelObservabilitySink(),
)
