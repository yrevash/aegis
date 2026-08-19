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

then re-exports the public surface (``complete``, ``embed``, ``transcribe``, ``record_call``,
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
    TranscriptionResult,
    Usage,
    complete,
    embed,
    last_trace_id,
    optimization_config,
    optimization_summary,
    record_call,
    transcribe,
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
    "TranscriptionResult",
    "Usage",
    "complete",
    "embed",
    "last_trace_id",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "transcribe",
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
    """Return the bound governance context, or ``None`` when nothing is bound.

    **What changed here, and why (task 9.2).** This gate used to return ``None`` for a
    context whose ``tenant_id`` was ``None``, which collapsed two different things into
    one: "nobody is governing this call" and "this call is platform work owned by no
    tenant". The consequence was that a platform-scoped red-team run — dozens of live
    model calls — wrote **zero** ``usage_ledger`` rows, and the money it spent existed
    nowhere in the system.

    The two halves are now separated, because they answer to different constraints:

    * **The cap needs a principal.** A USD ceiling is a promise made to somebody who
      agreed to it, and there is no ``budgets`` row for "nobody". Charging platform work
      to an arbitrary tenant would corrupt the figure on that tenant's own budget screen,
      which is the one number :mod:`aegis.jobs.admission` exists to keep single and true.
      So :func:`aegis.governance.enforcement.enforce_governance` still returns
      immediately for a tenantless context, and it does so without opening a session.
    * **The ledger needs only a call.** ``usage_ledger.tenant_id`` is nullable, so
      platform spend is recordable, and recording it is what makes it *visible* —
      countable on the usage rollup, reconcilable against a provider invoice, and
      impossible to mistake for zero.

    A call with **no context at all** stays a complete no-op: that is the offline/lite
    path where there is no database to write to, and widening it would make every unit
    test with a fake completer reach for a session.

    **What does bound platform work, then**, since no USD cap does: the same two controls
    that bound a tenant's, neither of which needs a principal to name. Admission's
    concurrency gate counts platform rows explicitly (``jobs.max_inflight.{job_type}``
    matches ``tenant_id IS NULL`` rather than skipping it — see
    :func:`aegis.jobs.admission._tenant_clause`), and the fleet-wide model-call limiter
    (:mod:`aegis.gateway.limiter`) bounds how fast any of it can spend. The ledger rows
    are what make the total *reviewable* afterwards, which is the part that was missing.

    Args:
        ctx: The context bound by the edge, or ``None``.

    Returns:
        ``ctx`` when anything is bound — tenant or platform — and ``None`` otherwise.
    """
    return ctx


class _GovernanceHook:
    """Wires the platform's budget/rate governance into the gateway's hook seam."""

    def get_context(self) -> GovernanceContext | None:
        """Return the bound :class:`GovernanceContext`, or ``None`` if ungoverned."""
        return _governed(get_governance_context())

    async def enforce(self, ctx: GovernanceContext) -> None:
        """Raise :class:`BudgetExceededError` if the principal is over any cap.

        Reads the authoritative ``Budget`` rows and ``UsageLedger`` sums for the
        tenant→user path. A **platform-scoped** context (``tenant_id is None``) is
        returned uncapped and without a database round trip — see :func:`_governed` for
        why a cap needs a principal and the ledger does not; the spend is still
        recorded by :meth:`record`. A real budget breach always propagates. An
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
        audio_seconds: float = 0.0,
        images: int = 0,
    ) -> None:
        """Write one durable usage-ledger row for a governed call.

        ``audio_seconds``/``images`` carry the non-token billable units of a
        transcription or vision call (Whisper bills per audio-minute), so those
        calls land in the ledger — and therefore under a USD cap — instead of
        being invisible behind ``prompt_tokens=0``.

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
            audio_seconds=audio_seconds,
            images=images,
        )


def _build_limiter() -> gateway.SlotLimiter:
    """Choose the model-call limiter this deployment can honestly claim to hold.

    Three outcomes, and the difference between them is reported rather than assumed:

    * **No limit configured** (``GATEWAY_MAX_CONCURRENT_CALLS=0``) →
      :class:`~aegis.gateway.limiter.NoSlotLimiter`, whose scope reads ``unlimited``.
    * **Real stores** → :class:`~aegis.gateway.limiter.RedisSlotLimiter`, whose leases
      live in the Redis this platform already runs, so the API process and every
      ``python -m app.jobs.worker`` share **one** count. This is the only configuration
      in which the number means what a reader assumes it means.
    * **Stores off** (the offline/lite profile, which has no Redis) →
      :class:`~aegis.gateway.limiter.LocalSlotLimiter`, which bounds this interpreter and
      says ``process`` on the platform surface. Correct for a one-process demo and
      visibly *not* a fleet claim for anyone else — a per-process semaphore presented as
      a global limit is worse than no limiter at all, because it promises a bound it
      cannot hold.

    The lease length is derived from ``llm_timeout_seconds`` rather than configured
    separately: a slot held materially longer than the call it guards is held by a
    process that is no longer running, and a second number here could be set shorter than
    the call it must outlast.

    It is derived through :func:`~aegis.gateway.llm.max_call_hold_seconds`, **not from
    the timeout directly**. The slot wraps the gateway's outer backstop, which gives the
    primary deployment and each fallback in the chain its own timeout budget — three call
    timeouts on the shipped chains. A lease of ``2 × timeout`` was therefore shorter than
    the call it guarded, and the reaper handed the slot to a second caller mid-flight: a
    limit of N admitting N+1 on precisely the slow calls it exists for.

    Returns:
        The limiter to install on the gateway.
    """
    settings = get_settings()
    limit = settings.gateway_max_concurrent_calls
    if limit < 1:
        logger.warning(
            "GATEWAY_MAX_CONCURRENT_CALLS=%d: model calls are UNBOUNDED. Five users "
            "asking four-agent questions is twenty simultaneous provider calls.",
            limit,
        )
        return gateway.NoSlotLimiter()
    lease = gateway.lease_seconds_for(
        gateway.max_call_hold_seconds(settings.llm_timeout_seconds)
    )
    if settings.stores_enabled and settings.redis_url:
        return gateway.RedisSlotLimiter.from_url(
            settings.redis_url,
            limit=limit,
            lease_seconds=lease,
            wait_seconds=settings.gateway_slot_wait_seconds,
        )
    logger.warning(
        "No shared store is configured, so the model-call limit of %d bounds THIS "
        "PROCESS only (scope='process'). A second process would double it.",
        limit,
    )
    return gateway.LocalSlotLimiter(limit)


# Wire the injected hooks once, at import time — every existing call site
# (`from app.core.llm import complete`, etc.) then goes through the real gateway
# with this platform's config/governance bound in, and the standalone
# `aegis.observability.OtelObservabilitySink` wired with no bespoke adapter.
gateway.configure(
    config=_SettingsGatewayConfig(),
    governance=_GovernanceHook(),
    observability=OtelObservabilitySink(),
    limiter=_build_limiter(),
)
