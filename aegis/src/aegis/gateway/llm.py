"""LiteLLM gateway — the single async entry point to a heterogeneous model fleet.

Every model call goes through :func:`complete` (chat, including multimodal
vision), :func:`embed` (text) or :func:`transcribe` (audio), which route by
:class:`~aegis.core.models.ModelRole` (never a hard-coded id) and talk to a
**custom OpenAI-compatible provider**:

- model string form ``openai/<deployment_id>`` (bare deployment names, no
  ``azure/`` prefix), with ``api_base`` + ``api_key`` supplied per call;
- a self-signed gateway certificate is handled via the injected
  :class:`GatewayConfig` (``ssl_verify=False`` disables TLS verification for it);
- usage and cost are captured and handed to the injected observability sink —
  in the call's own billable units, which are **not always tokens**: Whisper
  bills per audio-minute (see :class:`~aegis.gateway.routing.BillingUnit`), so
  those units are carried end to end rather than ledgered as ``$0.00``;
- tool calls are parsed into typed :class:`~aegis.gateway.types.ToolCallResult`
  objects;
- the per-role fallback chain a chat call may traverse is bounded before the call
  leaves: by the tenant's tier (:func:`model_allowlist` — a chain never promotes
  to a model the tenant is not entitled to) and by an in-process circuit breaker
  (a deployment that fails repeatedly is skipped for a cooldown, so a fan-out of
  N agents does not each pay a dead deployment's timeout). Every resulting
  downgrade is logged at ERROR and emitted as a :class:`FallbackEvent`.

Budget/rate governance and observability are **injected hooks** (see
:func:`configure`), each defaulting to a documented no-op so this module has no
hard dependency on any particular host application. ``litellm`` is imported
lazily (inside :func:`_litellm`) so importing this module never requires the
package — the unit tests inject a fake ``litellm`` and never touch the network.

Verified against: LiteLLM 1.52+ (``acompletion`` / ``aembedding`` /
``atranscription`` custom OpenAI-compatible provider, ``litellm.ssl_verify``,
``completion_cost``), August 2026.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import deque
from collections.abc import AsyncIterator, Iterable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from aegis.core.models import ModelRole
from aegis.gateway.routing import (
    baseline_role,
    is_routable_role,
    is_small_model,
    model_for,
    routing_table,
    unit_cost,
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

logger = logging.getLogger(__name__)

__all__ = [
    "GATEWAY_FALLBACK",
    "BudgetExceededError",
    "CostSource",
    "FallbackEvent",
    "FallbackReason",
    "FallbackSink",
    "GatewayConfig",
    "GenAIOperation",
    "GovernanceHook",
    "LLMResult",
    "ModelUnavailableError",
    "ObservabilitySink",
    "ToolCallResult",
    "TranscriptionResult",
    "TranscriptionSegment",
    "Usage",
    "breaker_status",
    "call_saving_usd",
    "complete",
    "configure",
    "count_images",
    "embed",
    "fallback_events",
    "last_trace_id",
    "model_allowlist",
    "optimization_config",
    "optimization_summary",
    "record_call",
    "transcribe",
    "usage_tally",
]


class GenAIOperation(StrEnum):
    """The GenAI operation an observability span is opened for."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"
    TRANSCRIPTION = "transcription"


# ─────────────────────────────────────────────────────────────────────────────
# Injected hooks (config / governance / observability) — the three couplings
# severed from the original host application, each a documented no-op default.
# ─────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class GatewayConfig(Protocol):
    """Connection + call-safety configuration for the gateway."""

    base_url: str
    api_key: str
    ssl_verify: bool
    max_output_tokens: int
    timeout_seconds: float
    budget_fail_open: bool


@dataclass
class _EnvGatewayConfig:
    """Default :class:`GatewayConfig`, read once from the environment.

    Used only when the host never calls :func:`configure` with its own config —
    keeps ``aegis.gateway`` usable standalone with just an api key/base url.
    """

    base_url: str = field(default_factory=lambda: os.environ.get("GATEWAY_BASE_URL", ""))
    api_key: str = field(default_factory=lambda: os.environ.get("GATEWAY_API_KEY", ""))
    ssl_verify: bool = field(
        default_factory=lambda: os.environ.get("GATEWAY_SSL_VERIFY", "true").lower()
        not in {"false", "0", "no"}
    )
    max_output_tokens: int = field(
        default_factory=lambda: int(os.environ.get("GATEWAY_MAX_OUTPUT_TOKENS", "1024"))
    )
    timeout_seconds: float = field(
        default_factory=lambda: float(os.environ.get("GATEWAY_TIMEOUT_SECONDS", "60.0"))
    )
    budget_fail_open: bool = field(
        default_factory=lambda: os.environ.get("GATEWAY_BUDGET_FAIL_OPEN", "false").lower()
        in {"true", "1", "yes"}
    )


class GovernanceHook(Protocol):
    """Budget/rate governance, injected so the gateway has no policy of its own.

    The **default** (see :func:`configure`) is an explicit no-op: standalone
    ``aegis.gateway`` does no budget enforcement at all unless a host injects a
    real hook — it never silently pretends to enforce.
    """

    def get_context(self) -> Any:  # noqa: ANN401 - opaque, host-defined context
        """Return the governed context for the in-flight call, or ``None``.

        Returning ``None`` means "ungoverned": :func:`complete`/:func:`embed`
        skip enforcement and ledgering entirely for this call.
        """
        ...

    async def enforce(self, ctx: Any) -> None:  # noqa: ANN401
        """Raise :class:`BudgetExceededError` if ``ctx`` is over any cap.

        Called BEFORE spend. Whether an enforcement-read failure (e.g. a
        database blip) fails open or closed is entirely the hook's policy.
        """
        ...

    async def record(
        self,
        ctx: Any,  # noqa: ANN401
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

        ``audio_seconds`` / ``images`` carry the non-token billable units of a
        transcription or vision call, so a per-minute or per-image charge lands
        in the ledger (and therefore under a USD cap) instead of being invisible
        behind ``prompt_tokens=0``. Both default to zero, so a hook written for
        token-only calls keeps working unchanged.

        The gateway calls this best-effort (a ledger-write failure is swallowed
        and logged, never raised) — see :func:`complete`/:func:`embed`.
        """
        ...


@runtime_checkable
class ObservabilitySink(Protocol):
    """Span + usage instrumentation, injected so the gateway has no OTel dep."""

    def span(
        self,
        operation: GenAIOperation,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> Any:  # noqa: ANN401 - an async context manager yielding an opaque span
        """Open an async context manager bracketing one model call's span."""
        ...

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
        ...

    def trace_id(self) -> str | None:
        """Return the active trace id (hex), for audit correlation."""
        ...


class _NoOpGovernance:
    """The default governance hook: no enforcement, no ledger — a clean no-op."""

    def get_context(self) -> None:
        """Always ungoverned — no host has injected a real hook."""
        return None

    async def enforce(self, ctx: Any) -> None:  # noqa: ANN401 - never called (get_context is None)
        """Never called: `get_context` always returns `None`."""
        return None

    async def record(self, ctx: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        """Never called: `get_context` always returns `None`."""
        return None


class _NoOpObservability:
    """The default observability sink: no span, no OTel dependency."""

    @asynccontextmanager
    async def span(
        self,
        operation: GenAIOperation,
        model: str,
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[None]:
        """Yield `None` — nothing is recorded."""
        yield None

    def set_usage(self, span: Any, **_kwargs: Any) -> None:  # noqa: ANN401
        """No-op."""
        return None

    def trace_id(self) -> None:
        """No tracer wired — always `None`."""
        return None


_config: GatewayConfig | None = None
_governance: GovernanceHook = _NoOpGovernance()
_observability: ObservabilitySink = _NoOpObservability()


def configure(
    *,
    config: GatewayConfig | None = None,
    governance: GovernanceHook | None = None,
    observability: ObservabilitySink | None = None,
    fallbacks: dict[ModelRole, list[ModelRole]] | None = None,
    baseline_role: ModelRole | None = None,
    fallback_sink: FallbackSink | None = None,
    allowed_roles: Iterable[ModelRole] | None = None,
    breaker_threshold: int | None = None,
    breaker_cooldown_seconds: float | None = None,
) -> None:
    """Wire the gateway's injected hooks and optimization knobs.

    Call once at host application startup (or import time of a strangler shim).
    Any argument left as `None` keeps the current binding — the module starts
    with config read from the environment on first use, a no-op governance hook
    (fail-open by construction: no enforcement at all), a no-op observability
    sink, the default per-role fallback chains, and the env/default frontier
    baseline, so `aegis.gateway` is usable standalone with just an api key/base
    url and every existing behaviour is unchanged unless a knob is passed.

    Args:
        config: Connection + call-safety settings.
        governance: Budget/rate governance hook.
        observability: Span + usage instrumentation sink.
        fallbacks: Override for the role → fallback-chain map used when a
            primary deployment errors.
        baseline_role: Override for the frontier-baseline role the savings calc
            prices actual spend against (takes precedence over
            ``GATEWAY_BASELINE_ROLE``).
        fallback_sink: Hook notified of every degradation (see
            :class:`FallbackSink`). The ERROR log and :func:`fallback_events`
            happen with or without it — this is the channel that puts a
            degradation on the host's event stream.
        allowed_roles: The process-wide default model allowlist — the roles a
            fallback chain may reach. ``None`` (the default) is unrestricted, so
            existing behaviour is unchanged; per-request tier bounds belong in
            :func:`model_allowlist`, which wins over this.
        breaker_threshold: Consecutive failures on one deployment before its
            breaker opens (default 3, env ``GATEWAY_BREAKER_THRESHOLD``).
        breaker_cooldown_seconds: How long an open breaker skips a deployment
            before letting one probe through (default 30s, env
            ``GATEWAY_BREAKER_COOLDOWN_SECONDS``).
    """
    global _config, _governance, _observability
    global _fallbacks_override, _baseline_role_override
    global _fallback_sink, _allowed_roles_override
    global _breaker_threshold_override, _breaker_cooldown_override
    if config is not None:
        _config = config
    if governance is not None:
        _governance = governance
    if observability is not None:
        _observability = observability
    if fallbacks is not None:
        _fallbacks_override = fallbacks
    if baseline_role is not None:
        _baseline_role_override = baseline_role
    if fallback_sink is not None:
        _fallback_sink = fallback_sink
    if allowed_roles is not None:
        _allowed_roles_override = frozenset(allowed_roles)
    if breaker_threshold is not None:
        _breaker_threshold_override = breaker_threshold
    if breaker_cooldown_seconds is not None:
        _breaker_cooldown_override = breaker_cooldown_seconds


def _get_config() -> GatewayConfig:
    """Return the active `GatewayConfig`, defaulting to env-read values."""
    global _config
    if _config is None:
        _config = _EnvGatewayConfig()
    return _config


# ─────────────────────────────────────────────────────────────────────────────
# The LiteLLM call shape (preserved byte-identical in behaviour)
# ─────────────────────────────────────────────────────────────────────────────

# Per-role fallback chain: if the primary deployment for a role fails, LiteLLM
# retries these deployment ids (routed through the same custom provider). This is
# the DEFAULT; a host may override it via ``configure(fallbacks=...)`` — see
# :func:`_effective_fallbacks`.
_DEFAULT_ROLE_FALLBACKS: dict[ModelRole, list[ModelRole]] = {
    ModelRole.GENERATION: [ModelRole.REASONING, ModelRole.CHEAP],
    ModelRole.REASONING: [ModelRole.GENERATION, ModelRole.CHEAP],
    ModelRole.CHEAP: [ModelRole.GENERATION],
}

# Optimization knobs a host can override (existing behaviour unchanged when unset):
#   * ``_fallbacks_override`` — the role → fallback-chain map;
#   * ``_baseline_role_override`` — the frontier-baseline role for the savings calc
#     (takes precedence over the ``GATEWAY_BASELINE_ROLE`` env default).
_fallbacks_override: dict[ModelRole, list[ModelRole]] | None = None
_baseline_role_override: ModelRole | None = None


def _effective_fallbacks() -> dict[ModelRole, list[ModelRole]]:
    """Return the active role → fallback-chain map (host override wins)."""
    return _fallbacks_override if _fallbacks_override is not None else _DEFAULT_ROLE_FALLBACKS


def _effective_baseline_role() -> ModelRole:
    """Return the active frontier-baseline role (host override wins over env)."""
    return _baseline_role_override if _baseline_role_override is not None else baseline_role()


# ─────────────────────────────────────────────────────────────────────────────
# Fallback that survives a fan-out: a circuit breaker, a visible event, and a
# chain bounded by the tenant's tier (phase 5 §5.8).
#
# There is exactly ONE fallback chain in this module — ``_effective_fallbacks()``
# above — and this section does not add a second. It decides, per call, WHICH
# members of that one chain are eligible before the call is handed to LiteLLM,
# and it makes the outcome observable:
#
#   * the **breaker** removes a deployment that has failed repeatedly, so a fan-out
#     of N concurrent agents does not each independently pay a dead deployment's
#     full timeout;
#   * the **tier bound** removes a role the tenant is not entitled to, so a cheap
#     tenant is never silently promoted to an expensive model (that is a spend
#     decision taken on their behalf, and it lands in their usage ledger);
#   * every downgrade emits a :class:`FallbackEvent` and logs at ERROR, because a
#     silent downgrade is a system that keeps answering while nobody learns the
#     answer got worse.
# ─────────────────────────────────────────────────────────────────────────────

#: CustomEvent name for a gateway fallback/degradation. Kept here (rather than in
#: ``aegis.core.stream_names``) because the gateway has no emitter of its own: the
#: host observes fallbacks through :func:`configure(fallback_sink=...)` and streams
#: them under whatever name it registers.
GATEWAY_FALLBACK = "gateway_fallback"


class FallbackReason(StrEnum):
    """Why a call did not run on the deployment the role normally routes to."""

    #: The primary's breaker is open — it failed repeatedly and is in cooldown.
    CIRCUIT_OPEN = "circuit_open"
    #: LiteLLM answered from a fallback deployment: the primary errored upstream.
    UPSTREAM_ERROR = "upstream_error"
    #: Every deployment in the chain is degraded — the call was refused, loudly.
    ALL_DEGRADED = "all_degraded"
    #: The role itself is outside the tenant's tier — refused before any spend.
    OUTSIDE_TIER = "outside_tier"


@dataclass(frozen=True)
class FallbackEvent:
    """One visible degradation: what was asked for, what happened, and why.

    ``taken_deployment`` is ``None`` when nothing was taken — i.e. the chain was
    exhausted and the call failed loudly rather than succeeding expensively.
    """

    role: ModelRole
    failed_deployment: str
    taken_deployment: str | None
    reason: FallbackReason
    considered: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        """Return the event as a plain JSON-safe payload (wire/stream shape)."""
        return {
            "event": GATEWAY_FALLBACK,
            "role": self.role.value,
            "failed_deployment": self.failed_deployment,
            "taken_deployment": self.taken_deployment,
            "reason": self.reason.value,
            "considered": list(self.considered),
        }


class ModelUnavailableError(RuntimeError):
    """Every deployment this role may reach is unavailable — fail loud, not wide.

    Raised instead of quietly promoting the call to a model outside the tenant's
    tier (a spend decision taken on their behalf) or of paying a known-dead
    deployment's timeout again.
    """

    def __init__(
        self,
        role: ModelRole,
        *,
        reason: FallbackReason,
        considered: tuple[str, ...],
    ) -> None:
        """Record which role was refused, why, and which deployments were weighed."""
        self.role = role
        self.reason = reason
        self.considered = considered
        super().__init__(
            f"No model available for role {role.value} ({reason.value}); "
            f"considered={list(considered)}"
        )


class FallbackSink(Protocol):
    """Host hook notified of every gateway degradation (default: a no-op).

    The ERROR log and the in-process ring buffer (:func:`fallback_events`) happen
    whether or not a host injects one — this sink is the *additional* channel that
    puts the degradation on the run's event stream.
    """

    def fallback(self, event: FallbackEvent) -> None:
        """Handle one degradation event. Must not raise (failures are logged)."""
        ...


#: Consecutive failures on one deployment before its breaker opens.
_DEFAULT_BREAKER_THRESHOLD = 3
#: How long an open breaker stays open before one probe is allowed through.
_DEFAULT_BREAKER_COOLDOWN_SECONDS = 30.0
#: How many degradation events are retained in-process for the console/harness.
_FALLBACK_EVENT_RING = 64

_fallback_sink: FallbackSink | None = None
_breaker_threshold_override: int | None = None
_breaker_cooldown_override: float | None = None
_allowed_roles_override: frozenset[ModelRole] | None = None
_fallback_events: deque[FallbackEvent] = deque(maxlen=_FALLBACK_EVENT_RING)

#: The in-flight tenant's model allowlist. A ``ContextVar`` rather than a global
#: because a fan-out runs many calls concurrently in one process: each sub-agent
#: task inherits the context of the request that spawned it, so the bound travels
#: with the tenant instead of being whatever the last request happened to set.
_allowed_roles_var: ContextVar[frozenset[ModelRole] | None] = ContextVar(
    "aegis_gateway_allowed_roles", default=None
)


def _env_float(name: str, default: float) -> float:
    """Read a float env var, falling back to ``default`` on anything unparseable."""
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _effective_breaker_threshold() -> int:
    """Return the consecutive-failure count that opens a breaker (override wins)."""
    if _breaker_threshold_override is not None:
        return max(1, _breaker_threshold_override)
    try:
        return max(1, int(os.environ.get("GATEWAY_BREAKER_THRESHOLD", _DEFAULT_BREAKER_THRESHOLD)))
    except (TypeError, ValueError):
        return _DEFAULT_BREAKER_THRESHOLD


def _effective_breaker_cooldown() -> float:
    """Return the seconds an open breaker waits before allowing a probe."""
    if _breaker_cooldown_override is not None:
        return max(0.0, _breaker_cooldown_override)
    return max(
        0.0,
        _env_float("GATEWAY_BREAKER_COOLDOWN_SECONDS", _DEFAULT_BREAKER_COOLDOWN_SECONDS),
    )


def _effective_allowed_roles() -> frozenset[ModelRole] | None:
    """Return the roles the in-flight caller may reach, or ``None`` for all.

    The context-scoped bound (see :func:`model_allowlist`) wins over the process
    default set by :func:`configure`; ``None`` at both levels means unrestricted,
    which is the standalone default and preserves every pre-existing behaviour.
    """
    scoped = _allowed_roles_var.get()
    if scoped is not None:
        return scoped
    return _allowed_roles_override


@contextmanager
def model_allowlist(roles: Iterable[ModelRole] | None) -> Iterator[None]:
    """Bound every gateway call in this context to ``roles`` (the tenant's tier).

    Bounds :func:`complete` — the call path that owns the fallback chain. Embedding
    and transcription have exactly one deployment each and no chain to escape
    through, so they are deliberately not gated here rather than being gated
    somewhere a caller would have to guess about.

    Args:
        roles: The roles the tenant's tier entitles them to, or ``None`` to lift
            the bound for this context.

    Yields:
        ``None`` — the bound applies for the duration of the ``with`` block, in
        this task and in every task spawned from it.
    """
    token = _allowed_roles_var.set(frozenset(roles) if roles is not None else None)
    try:
        yield
    finally:
        _allowed_roles_var.reset(token)


def _now() -> float:
    """Return the monotonic clock the breaker measures cooldowns on (test seam)."""
    return time.monotonic()


@dataclass
class _BreakerState:
    """One deployment's health: consecutive failures and when it was opened."""

    consecutive_failures: int = 0
    opened_at: float | None = None


_breakers: dict[str, _BreakerState] = {}


def _is_available(deployment: str) -> bool:
    """Return whether ``deployment`` may be called now.

    An open breaker becomes available again once the cooldown has elapsed — that
    call is the probe. It is a probe and not a reopening: a single failure while
    open restarts the cooldown, and a single success closes the breaker.
    """
    state = _breakers.get(deployment)
    if state is None or state.opened_at is None:
        return True
    return (_now() - state.opened_at) >= _effective_breaker_cooldown()


#: Exception *types* that are direct evidence the deployment could not be reached or
#: did not answer, whatever was asked of it. ``TimeoutError`` is what
#: :func:`_bounded_acompletion`'s outer ``wait_for`` raises, and is also what
#: ``asyncio.TimeoutError`` aliases on 3.11+.
_DEPLOYMENT_FAILURE_TYPES: tuple[type[BaseException], ...] = (ConnectionError, TimeoutError)

#: Exception *class names* LiteLLM/OpenAI raise for transport failures that do not
#: always carry a status code. Matched by name on purpose: importing litellm's
#: exception module here would defeat the lazy import this gateway is built on.
_DEPLOYMENT_FAILURE_NAMES = frozenset(
    {
        "APIConnectionError",
        "APIConnectionTimeoutError",
        "APITimeoutError",
        "InternalServerError",
        "ServiceUnavailableError",
        "Timeout",
    }
)


def _status_code(exc: BaseException) -> int | None:
    """Return the HTTP status an exception carries, or ``None`` if it carries none."""
    for attribute in ("status_code", "http_status", "code"):
        value = getattr(exc, attribute, None)
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    status = getattr(getattr(exc, "response", None), "status_code", None)
    return status if isinstance(status, int) and not isinstance(status, bool) else None


def _is_deployment_evidence(exc: BaseException) -> bool:
    """Return whether ``exc`` is evidence about the *deployment* rather than the request.

    The breaker is process-global and keyed by deployment, which is right: a
    deployment is genuinely shared, so "it is unreachable" is a shared fact. But
    a provider rejecting one tenant's request is *not* a fact about the deployment.
    An over-long prompt, a content-policy refusal, or that tenant's own rate limit
    would fail identically against a perfectly healthy upstream, and counting them
    opens the breaker for every other tenant in the process — who are then answered
    by a more expensive fallback and billed for it, or refused outright. That is a
    spend decision taken on their behalf, which is the thing §5.8 exists to prevent.

    So the classification is conservative in the safe direction: only positive
    evidence of upstream unhealth arms the breaker. Connect errors, timeouts, 408
    and 5xx qualify. 4xx does not. Neither does an exception type this gateway has
    never seen — an unrecognised failure is evidence of nothing, and the cost of
    missing one is paying a timeout, while the cost of a false positive is another
    tenant's ledger.

    Args:
        exc: The exception raised by the provider call.

    Returns:
        ``True`` when the failure should count against the deployment.
    """
    if isinstance(exc, _DEPLOYMENT_FAILURE_TYPES):
        return True
    status = _status_code(exc)
    if status is not None:
        return status >= 500 or status == 408
    return type(exc).__name__ in _DEPLOYMENT_FAILURE_NAMES


def _record_deployment_failure(deployment: str, *, role: ModelRole, reason: str) -> None:
    """Count one failure against ``deployment``, opening its breaker at threshold."""
    state = _breakers.setdefault(deployment, _BreakerState())
    state.consecutive_failures += 1
    threshold = _effective_breaker_threshold()
    if state.consecutive_failures >= threshold:
        state.opened_at = _now()
        logger.error(
            "Circuit breaker OPEN for deployment %s (role=%s): %s consecutive "
            "failures (threshold=%s, reason=%s). Skipping it for %.0fs; the next "
            "call after that is a probe.",
            deployment,
            role.value,
            state.consecutive_failures,
            threshold,
            reason,
            _effective_breaker_cooldown(),
        )


def _record_deployment_success(deployment: str) -> None:
    """Close ``deployment``'s breaker: a success resets the consecutive count."""
    state = _breakers.get(deployment)
    if state is None:
        return
    if state.opened_at is not None:
        logger.info(
            "Circuit breaker CLOSED for deployment %s: probe succeeded.", deployment
        )
    _breakers.pop(deployment, None)


def breaker_status() -> dict[str, dict[str, Any]]:
    """Return the live per-deployment breaker state (for a dashboard / the harness).

    A degraded deployment nobody can see is the same defect as a silent fallback,
    so the state is readable rather than only inferable from logs.
    """
    now = _now()
    cooldown = _effective_breaker_cooldown()
    return {
        deployment: {
            "consecutive_failures": state.consecutive_failures,
            "degraded": state.opened_at is not None
            and (now - state.opened_at) < cooldown,
            "cooldown_remaining_seconds": (
                max(0.0, cooldown - (now - state.opened_at))
                if state.opened_at is not None
                else 0.0
            ),
        }
        for deployment, state in _breakers.items()
    }


def fallback_events() -> list[dict[str, Any]]:
    """Return the recent degradation events, oldest first (bounded ring buffer)."""
    return [event.as_dict() for event in _fallback_events]


def _emit_fallback(event: FallbackEvent) -> None:
    """Log at ERROR, retain, and hand the degradation to the injected sink.

    The log and the ring happen unconditionally: this control is never silent
    just because no host wired a sink.
    """
    logger.error(
        "Gateway fallback (%s): role=%s failed_deployment=%s taken=%s considered=%s",
        event.reason.value,
        event.role.value,
        event.failed_deployment,
        event.taken_deployment or "<none — call refused>",
        list(event.considered),
    )
    _fallback_events.append(event)
    if _fallback_sink is None:
        return
    try:
        _fallback_sink.fallback(event)
    except Exception:  # noqa: BLE001 - a sink is observation, never a control path
        logger.warning("Fallback sink raised; degradation still logged.", exc_info=True)


def _role_chain(role: ModelRole) -> list[ModelRole]:
    """Return ``role`` followed by its configured fallbacks, de-duplicated."""
    ordered: list[ModelRole] = []
    for candidate in (role, *_effective_fallbacks().get(role, [])):
        if candidate not in ordered:
            ordered.append(candidate)
    return ordered


def _resolve_chain(role: ModelRole) -> tuple[ModelRole, list[ModelRole]]:
    """Return the (primary, fallbacks) roles this call may actually use.

    Applies, in order: the tenant's tier bound (a chain may never promote out of
    it), then the circuit breaker (skip what is known dead). Emits a visible event
    when the result is not the role's normal primary.

    Raises:
        ModelUnavailableError: When no deployment in tier is available. Failing
            loudly is the point — succeeding on a model the tenant is not entitled
            to would put an unauthorised charge in their ledger.
    """
    allowed = _effective_allowed_roles()
    chain = _role_chain(role)
    considered = tuple(model_for(candidate) for candidate in chain)
    primary_deployment = model_for(role)

    in_tier = [candidate for candidate in chain if allowed is None or candidate in allowed]
    if not in_tier:
        _emit_fallback(
            FallbackEvent(
                role=role,
                failed_deployment=primary_deployment,
                taken_deployment=None,
                reason=FallbackReason.OUTSIDE_TIER,
                considered=considered,
            )
        )
        raise ModelUnavailableError(
            role, reason=FallbackReason.OUTSIDE_TIER, considered=considered
        )

    healthy = [candidate for candidate in in_tier if _is_available(model_for(candidate))]
    if not healthy:
        _emit_fallback(
            FallbackEvent(
                role=role,
                failed_deployment=primary_deployment,
                taken_deployment=None,
                reason=FallbackReason.ALL_DEGRADED,
                considered=tuple(model_for(candidate) for candidate in in_tier),
            )
        )
        raise ModelUnavailableError(
            role,
            reason=FallbackReason.ALL_DEGRADED,
            considered=tuple(model_for(candidate) for candidate in in_tier),
        )

    primary, *rest = healthy
    if primary is not role:
        # Why the role's own deployment is not being used decides the reason:
        # the tier refused it (a downgrade the tenant is entitled to) or the
        # breaker knows it is dead. Reporting one as the other would be a lie in
        # the only record anybody reads.
        reason = (
            FallbackReason.OUTSIDE_TIER
            if role not in in_tier
            else FallbackReason.CIRCUIT_OPEN
        )
        _emit_fallback(
            FallbackEvent(
                role=role,
                failed_deployment=primary_deployment,
                taken_deployment=model_for(primary),
                reason=reason,
                considered=tuple(model_for(candidate) for candidate in in_tier),
            )
        )
    return primary, rest


def _attribute_response(
    *,
    role: ModelRole,
    primary_role: ModelRole,
    fallback_roles: list[ModelRole],
    response_model: str,
) -> None:
    """Credit or fault the deployment that actually answered.

    LiteLLM traverses the ``fallbacks`` chain internally, so the only evidence of
    an in-LiteLLM fallback is the responding model id. The inference is deliberately
    strict: it counts as a fallback ONLY when the responding id exactly matches one
    of the fallback deployments this call passed. Anything else (a provider echoing
    a different string, an unknown id) is credited to the primary rather than
    fabricated into a failure nobody observed.
    """
    primary_deployment = model_for(primary_role)
    answered = (response_model or "").removeprefix("openai/")
    if answered == primary_deployment or not answered:
        _record_deployment_success(primary_deployment)
        return
    taken = next(
        (model_for(candidate) for candidate in fallback_roles if model_for(candidate) == answered),
        None,
    )
    if taken is None:
        # Not a deployment we offered — no evidence the primary failed.
        _record_deployment_success(primary_deployment)
        return
    _record_deployment_failure(
        primary_deployment, role=role, reason=FallbackReason.UPSTREAM_ERROR.value
    )
    _record_deployment_success(taken)
    _emit_fallback(
        FallbackEvent(
            role=role,
            failed_deployment=primary_deployment,
            taken_deployment=taken,
            reason=FallbackReason.UPSTREAM_ERROR,
            considered=(primary_deployment, *(model_for(c) for c in fallback_roles)),
        )
    )

_ssl_configured = False

# One corrective nudge appended when a structured-output (JSON) call returns
# content that does not parse — a single re-ask, never a loop.
_JSON_REASK_NUDGE = (
    "Your previous reply was not valid JSON. Return ONLY a single valid JSON "
    "object, with no prose, no markdown fences, and no explanation."
)


def _wants_json(response_format: dict | None) -> bool:
    """Return whether ``response_format`` asks for a JSON object/schema reply."""
    if not isinstance(response_format, dict):
        return False
    return "json" in str(response_format.get("type", "")).lower()


def _is_valid_json(content: str) -> bool:
    """Return whether ``content`` parses as a single JSON value (non-empty)."""
    text = (content or "").strip()
    if not text:
        return False
    try:
        json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return False
    return True


async def _bounded_acompletion(
    litellm: Any,  # noqa: ANN401 - third-party module handle
    kwargs: dict[str, Any],
    *,
    timeout: float | None,
) -> Any:  # noqa: ANN401
    """Await ``litellm.acompletion`` under a hard outer wall-clock backstop.

    LiteLLM already receives ``timeout`` per attempt (bounding each call in the
    fallback chain), but a genuinely hung coroutine could still ignore it; the
    outer :func:`asyncio.wait_for` guarantees the await returns. On expiry it
    raises :class:`TimeoutError`, which propagates exactly like any other
    transport failure — the run fails closed rather than blocking indefinitely.
    """
    if timeout is not None and timeout > 0:
        return await asyncio.wait_for(litellm.acompletion(**kwargs), timeout=timeout)
    return await litellm.acompletion(**kwargs)


async def _attempt(
    litellm: Any,  # noqa: ANN401 - third-party module handle
    kwargs: dict[str, Any],
    *,
    timeout: float | None,
    role: ModelRole,
    primary_role: ModelRole,
) -> Any:  # noqa: ANN401
    """Run one bounded completion, faulting the chosen deployment only if it earned it.

    An exception here means the whole chain LiteLLM was given is exhausted. When the
    failure is evidence about the *deployment* (see :func:`_is_deployment_evidence`)
    it is counted against the deployment **this gateway selected** — the one it is
    responsible for choosing again next time — and not against the fallbacks, because
    an opaque transport error carries no evidence of how far LiteLLM got, and a
    breaker opened on a guess is a control that lies.

    When the failure is evidence about the *request* — a 4xx, a content-policy
    refusal, an over-long prompt, this caller's own rate limit — nothing is counted.
    The breaker is shared by every tenant in the process, and one tenant's bad
    request must not push another tenant onto a costlier deployment or refuse them
    outright. The failure still propagates to *this* caller unchanged, and it is
    still logged, because not arming a control is not a reason to go quiet about it.
    """
    try:
        return await _bounded_acompletion(litellm, kwargs, timeout=timeout)
    except Exception as exc:
        deployment = model_for(primary_role)
        if _is_deployment_evidence(exc):
            _record_deployment_failure(deployment, role=role, reason=type(exc).__name__)
        else:
            logger.warning(
                "Gateway call to %s failed with a REQUEST-side error (%s: %s); the "
                "circuit breaker is NOT armed. This failure is evidence about the "
                "request, not about the deployment, and faulting a shared deployment "
                "for it would degrade every other tenant in this process.",
                deployment,
                type(exc).__name__,
                exc,
            )
        raise


def _estimate_cost(
    role: ModelRole,
    prompt_tokens: int,
    completion_tokens: int,
    *,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> float:
    """Estimate call cost from measured units when the provider has no price.

    Delegates to :func:`~aegis.gateway.routing.unit_cost`, which charges the
    role's input rate per *its own billing unit* — per 1k prompt tokens for a text
    model, per audio-minute for ``VOICE``, per image for an image-billed role. The
    two positional token arguments keep the original signature, so every existing
    caller is unchanged.
    """
    return unit_cost(
        role,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        audio_seconds=audio_seconds,
        images=images,
    )


def _has_non_token_units(audio_seconds: float, images: int) -> bool:
    """Return whether a call consumed billable units that are not tokens."""
    return audio_seconds > 0.0 or images > 0


@dataclass
class _RoleAgg:
    """Per-role accumulation of real model calls (calls, units, cost)."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0
    audio_seconds: float = 0.0
    images: int = 0


@dataclass
class _UsageTally:
    """Process-wide count of model calls, for the measured efficiency metric.

    ``by_role`` is a supplementary per-role breakdown; the top-line counters stay
    the single source of truth (``usage_tally`` reads only them), and the per-role
    costs always sum to ``total_cost_usd`` because every recorded call is
    attributed to exactly one role.

    ``routable_calls`` is the denominator of ``small_model_share``: only calls to a
    role small-model routing actually chooses between (see
    :func:`~aegis.gateway.routing.is_routable_role`) count, so ledgering an
    embedding or a transcription cannot dilute a *routing* metric with calls that
    were never routable.
    """

    total_calls: int = 0
    routable_calls: int = 0
    small_calls: int = 0
    total_cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
    total_audio_seconds: float = 0.0
    total_images: int = 0
    by_role: dict[ModelRole, _RoleAgg] = field(default_factory=dict)


_tally = _UsageTally()


def _attribution_role(model_id: str, role: ModelRole | None) -> ModelRole:
    """Return the role a call is attributed to in the per-role breakdown.

    Prefer the explicit routing ``role``; for a legacy caller that passes none,
    infer a bucket from the deployment id (small id → ``CHEAP``, else the
    baseline role) so the per-role costs still sum to the total.
    """
    if role is not None:
        return role
    return ModelRole.CHEAP if is_small_model(model_id) else _effective_baseline_role()


def _baseline_cost(
    cost_usd: float,
    *,
    prompt_tokens: int,
    completion_tokens: int,
    audio_seconds: float,
    images: int,
) -> float:
    """Return what this call's work would have cost at the frontier baseline.

    For token work that is the baseline role's token price. For non-token work
    (audio minutes, images) the frontier *chat* model cannot do the job at all, so
    there is no cheaper-or-dearer alternative to price against: the baseline is
    the call's own measured cost, which books a **zero** saving rather than a
    fabricated negative one that would silently eat real savings elsewhere.
    """
    token_baseline = _estimate_cost(
        _effective_baseline_role(), prompt_tokens, completion_tokens
    )
    if not _has_non_token_units(audio_seconds, images):
        return token_baseline
    return max(token_baseline, cost_usd)


def call_saving_usd(usage: Usage) -> float:
    """Return the measured small-model saving for ONE call, from its own usage.

    ``baseline − actual``, clamped at zero, priced exactly as the cumulative tally
    prices it (see :func:`_baseline_cost`) but derived solely from this call's own
    :class:`~aegis.gateway.types.Usage`. Reading no shared mutable state makes it
    exact under concurrency — a before/after delta over the process-global tally
    taken across an ``await`` attributes concurrent calls' spend to each other.
    """
    baseline = _baseline_cost(
        usage.cost_usd,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        audio_seconds=usage.audio_seconds,
        images=usage.images,
    )
    return max(0.0, baseline - usage.cost_usd)


def record_call(
    model_id: str,
    cost_usd: float,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
    role: ModelRole | None = None,
) -> None:
    """Record one model call for the measured efficiency metrics.

    Alongside the actual ``cost_usd``, this accumulates a **baseline** cost: what
    the same tokens would have cost had the call been routed to the
    frontier-baseline model (see :func:`_effective_baseline_role`, default
    ``GENERATION``). The gap between baseline and actual is the measured saving
    from small-model routing (surfaced as ``cost_saved_usd``).

    Args:
        model_id: The deployment id that served the call (drives small-model
            classification, the single source of ``small_model_share``).
        cost_usd: The **measured** cost of the call (real litellm cost, or the
            honest token-estimate fallback — never a silent ``0``).
        prompt_tokens: Input tokens the call consumed.
        completion_tokens: Output tokens the call produced.
        audio_seconds: Seconds of audio billed (a transcription's billing unit).
        images: Images billed or sent as input (a vision call's unit).
        role: The routing role, used only for the supplementary per-role
            breakdown and the ``small_model_share`` denominator; when omitted it
            is inferred from ``model_id``.
    """
    _tally.total_calls += 1
    if is_routable_role(role):
        _tally.routable_calls += 1
        _tally.small_calls += int(is_small_model(model_id))
    _tally.total_cost_usd += cost_usd
    _tally.total_audio_seconds += audio_seconds
    _tally.total_images += images
    _tally.baseline_cost_usd += _baseline_cost(
        cost_usd,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        audio_seconds=audio_seconds,
        images=images,
    )
    agg = _tally.by_role.setdefault(_attribution_role(model_id, role), _RoleAgg())
    agg.calls += 1
    agg.prompt_tokens += prompt_tokens
    agg.completion_tokens += completion_tokens
    agg.cost_usd += cost_usd
    agg.audio_seconds += audio_seconds
    agg.images += images


def usage_tally() -> dict[str, Any]:
    """Return live call counts. ``small_model_share`` is ``None`` before any call.

    ``total_calls`` counts every ledgered model call (chat, embedding,
    transcription); ``small_model_share`` is measured only over the *routable*
    chat calls, the ones small-model routing genuinely chose between.
    """
    routable = _tally.routable_calls
    baseline = _tally.baseline_cost_usd
    actual = _tally.total_cost_usd
    return {
        "total_calls": _tally.total_calls,
        "small_calls": _tally.small_calls,
        "total_cost_usd": actual,
        "baseline_cost_usd": baseline,
        "cost_saved_usd": max(0.0, baseline - actual),
        "small_model_share": (_tally.small_calls / routable) if routable else None,
        "total_audio_seconds": _tally.total_audio_seconds,
        "total_images": _tally.total_images,
    }


def optimization_summary() -> dict[str, Any]:
    """Return the token-optimization summary — the Business-Impact savings data.

    The top-line figures are taken verbatim from :func:`usage_tally` (one source
    of truth: this accessor never recomputes them), extended with a **measured**
    per-role breakdown (call counts, tokens, cost, small-model flag) and the
    frontier-baseline model the ``cost_saved_usd`` figure is priced against. Every
    number is measured from real per-call data — nothing here is fabricated.

    Cache-hit savings are intentionally **not** in this figure: a semantic- or
    answer-cache hit skips the model entirely and never reaches the gateway
    ledger, so its win is metered elsewhere as cache-hit rate (avoiding double
    counting / false precision).
    """
    tally = usage_tally()
    by_role = {
        role.value: {
            "calls": agg.calls,
            "prompt_tokens": agg.prompt_tokens,
            "completion_tokens": agg.completion_tokens,
            "cost_usd": agg.cost_usd,
            "audio_seconds": agg.audio_seconds,
            "images": agg.images,
            "small_model": is_small_model(model_for(role)),
        }
        for role, agg in _tally.by_role.items()
    }
    return {
        **tally,
        "by_role": by_role,
        "baseline_role": _effective_baseline_role().value,
        "baseline_model": model_for(_effective_baseline_role()),
    }


def optimization_config() -> dict[str, Any]:
    """Return the effective routing/optimization knobs (for a dashboard / UI).

    Reflects the live, host-overridable configuration: the role → model map, the
    per-role fallback chains, the wall-clock timeout, the default output-token
    cap, the frontier-baseline role/model the savings calc prices against, the
    tier bound on what a chain may reach (``None`` = unrestricted), and the
    circuit-breaker settings plus its live per-deployment state — so a degraded
    deployment is a visible fact and not something only the logs know.
    """
    config = _get_config()
    return {
        "routing": routing_table(),
        "fallbacks": {
            role.value: [fb.value for fb in chain]
            for role, chain in _effective_fallbacks().items()
        },
        "timeout_seconds": config.timeout_seconds,
        "max_output_tokens": config.max_output_tokens,
        "baseline_role": _effective_baseline_role().value,
        "baseline_model": model_for(_effective_baseline_role()),
        "allowed_roles": (
            sorted(r.value for r in allowed) if (allowed := _effective_allowed_roles()) else None
        ),
        "breaker": {
            "threshold": _effective_breaker_threshold(),
            "cooldown_seconds": _effective_breaker_cooldown(),
            "state": breaker_status(),
        },
    }


def _litellm() -> Any:  # noqa: ANN401 - third-party module handle
    """Import and configure ``litellm`` lazily (TLS verify set once).

    Returns:
        The ``litellm`` module, with ``ssl_verify`` set from the injected
        :class:`GatewayConfig` on first use.
    """
    global _ssl_configured
    import litellm

    if not _ssl_configured:
        # Current LiteLLM way to disable TLS verification for a self-signed
        # gateway: the module-level global. Scoped, documented exception.
        litellm.ssl_verify = _get_config().ssl_verify
        _ssl_configured = True
    return litellm


def _provider_model(role: ModelRole) -> str:
    """Return the LiteLLM custom-provider model string for ``role``."""
    return f"openai/{model_for(role)}"


def _base_kwargs() -> dict[str, Any]:
    """Return the shared ``api_base`` / ``api_key`` kwargs for every call."""
    config = _get_config()
    return {"api_base": config.base_url, "api_key": config.api_key}


def _safe_cost(litellm: Any, response: Any) -> float:  # noqa: ANN401
    """Return the USD cost of ``response``, or ``0.0`` for unmapped models."""
    try:
        return float(litellm.completion_cost(completion_response=response) or 0.0)
    except Exception:  # pragma: no cover - depends on litellm cost map
        logger.debug("completion_cost failed (model likely unmapped)", exc_info=True)
        return 0.0


def _resolve_cost(
    litellm: Any,  # noqa: ANN401 - third-party module handle
    response: Any,  # noqa: ANN401
    role: ModelRole,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    audio_seconds: float = 0.0,
    images: int = 0,
    billable_work: bool = False,
) -> tuple[float, CostSource]:
    """Price one call and say where the price came from — never a silent ``$0``.

    Order: the provider's own cost map first; otherwise the measured units × the
    configured per-role rate (custom gateway deployment ids are not in LiteLLM's
    cost map, so this is the normal path here). If neither yields a positive cost
    while the call did consume billable units, the result is tagged
    :attr:`CostSource.UNPRICED` and logged at WARNING — a $0 that means "we could
    not price this", loudly, rather than "this was free", silently.

    ``billable_work`` says "the provider really did work here" for a call whose
    billable UNIT is itself unknown: a transcription whose duration nobody
    reported has no tokens, no seconds and no images to point at, and is exactly
    the case that must not be mistaken for a free call.
    """
    cost = _safe_cost(litellm, response) if response is not None else 0.0
    if cost > 0.0:
        return cost, CostSource.PROVIDER

    cost = _estimate_cost(
        role,
        prompt_tokens,
        completion_tokens,
        audio_seconds=audio_seconds,
        images=images,
    )
    if cost > 0.0:
        return cost, CostSource.ESTIMATED

    billable = (
        billable_work
        or prompt_tokens > 0
        or completion_tokens > 0
        or _has_non_token_units(audio_seconds, images)
    )
    if billable:
        logger.warning(
            "Unpriced %s call on %s: billable units consumed "
            "(prompt_tokens=%s, completion_tokens=%s, audio_seconds=%s, images=%s) "
            "but neither the provider cost map nor the configured COST_%s_* rates "
            "yielded a cost. Ledgering $0.00 as UNPRICED.",
            role.value,
            model_for(role),
            prompt_tokens,
            completion_tokens,
            audio_seconds,
            images,
            role.name,
        )
        return 0.0, CostSource.UNPRICED
    # Nothing billable happened at all — a genuine, unambiguous zero.
    return 0.0, CostSource.ESTIMATED


def _parse_tool_calls(message: Any) -> list[ToolCallResult]:  # noqa: ANN401
    """Parse a message's ``tool_calls`` into typed :class:`ToolCallResult` list."""
    results: list[ToolCallResult] = []
    for call in getattr(message, "tool_calls", None) or []:
        raw_args = call.function.arguments
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
        except (json.JSONDecodeError, TypeError, ValueError):
            args = {"_raw": raw_args}
        results.append(ToolCallResult(id=call.id, name=call.function.name, args=args))
    return results


async def _record_usage(
    ctx: Any,  # noqa: ANN401 - opaque governance context
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    audio_seconds: float = 0.0,
    images: int = 0,
) -> None:
    """Best-effort ledger write for a governed call: swallow, log, never raise.

    A ledger write is a durable record, not a control path — a failure here must
    never fail the model call that already succeeded (or was already refused).

    The non-token unit kwargs are forwarded **only** when the call actually
    consumed them, so a token-only call's payload is byte-identical to before and
    no pre-existing :class:`GovernanceHook` implementation can break. A hook that
    has not yet been widened will raise ``TypeError`` on a transcription — logged
    here at WARNING with the traceback, i.e. visible, never a silent skip.
    """
    if ctx is None:
        return
    extra: dict[str, Any] = {}
    if _has_non_token_units(audio_seconds, images):
        extra = {"audio_seconds": audio_seconds, "images": images}
    try:
        await _governance.record(
            ctx,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            trace_id=_observability.trace_id(),
            **extra,
        )
    except Exception:  # noqa: BLE001 - ledger is best-effort at the edge
        logger.warning("Usage-ledger write failed.", exc_info=True)


#: OpenAI-style multimodal content-part types that carry an image.
_IMAGE_PART_TYPES: frozenset[str] = frozenset({"image_url", "image", "input_image"})


def count_images(messages: list[dict]) -> int:
    """Return how many image parts ``messages`` carries.

    A vision call is an ordinary chat completion whose content is a list of
    parts; counting the image parts is what lets the image COUNT flow end to end
    (result → tally → ledger) as a first-class billable unit, instead of being
    inferable only from an opaque input-token number. Text-only messages always
    return ``0``, so nothing about an existing chat call changes.
    """
    count = 0
    for message in messages:
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, list):
            continue
        for part in content:
            if isinstance(part, dict) and str(part.get("type", "")) in _IMAGE_PART_TYPES:
                count += 1
    return count


async def complete(
    role: ModelRole,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.0,
    response_format: dict | None = None,
    max_tokens: int | None = None,
) -> LLMResult:
    """Run a chat completion for ``role`` and return a normalised result.

    Three production call-safety measures wrap every call: a bounded output
    (``max_tokens``), a per-call ``timeout``, and — for structured-output
    requests — exactly one corrective re-ask when the reply is not valid JSON.

    Args:
        role: The job to route (``GENERATION``, ``CHEAP``, ``REASONING``, …); the
            concrete deployment id comes from :func:`aegis.gateway.routing.model_for`.
        messages: OpenAI-style chat messages.
        tools: Optional OpenAI tool/function schemas to offer the model.
        temperature: Sampling temperature (default deterministic ``0.0``).
        response_format: Optional structured-output spec, e.g.
            ``{"type": "json_object"}``.
        max_tokens: Optional per-call output-token ceiling. When omitted, the
            configured default cap (``config.max_output_tokens``) applies so
            every generation is bounded on cost and latency.

    Returns:
        An :class:`LLMResult` with the assistant text, any parsed tool calls, and
        token/cost usage. For a multimodal (``VISION``) call the number of image
        parts in ``messages`` is measured and carried through ``usage.images`` to
        the tally and the ledger.

    Raises:
        BudgetExceededError: When the injected governance hook refuses the call.
        ModelUnavailableError: When every deployment this role may reach — after
            the tenant's tier bound and the circuit breaker — is unavailable. The
            call fails loudly rather than succeeding on a model outside the tier.
    """
    gov_ctx = _governance.get_context()
    if gov_ctx is not None:
        # Budget/rate check BEFORE spend — refuse the call if over any cap.
        await _governance.enforce(gov_ctx)

    config = _get_config()
    # Which of the role's ONE chain (`_effective_fallbacks`) this call may use:
    # bounded by the tenant's tier, minus whatever the breaker knows is dead.
    # Raises rather than promoting out of tier or re-paying a known-dead timeout.
    primary_role, fallback_roles = _resolve_chain(role)
    litellm = _litellm()
    model = _provider_model(primary_role)
    # Measured, not guessed: how many images this call actually sends (0 for every
    # text-only chat, so nothing about an existing call site changes).
    images = count_images(messages)

    # Per-call output cap: explicit argument wins, else the configured default so
    # no generation is unbounded.
    max_tokens_effective = (
        max_tokens if max_tokens is not None else config.max_output_tokens
    )
    per_call_timeout = config.timeout_seconds

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens_effective,
        # Forwarded to LiteLLM so each attempt (primary + fallbacks) is bounded.
        "timeout": per_call_timeout,
        **_base_kwargs(),
    }
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    if response_format:
        kwargs["response_format"] = response_format

    fallbacks = [_provider_model(r) for r in fallback_roles]
    if fallbacks:
        kwargs["fallbacks"] = fallbacks

    # Hard outer backstop for a hung upstream: give the primary and each fallback
    # its own per-call timeout budget before the ``wait_for`` ceiling trips, so
    # the existing fallback semantics are preserved but the await always returns.
    outer_timeout = (
        per_call_timeout * (len(fallbacks) + 1)
        if per_call_timeout and per_call_timeout > 0
        else None
    )

    async with _observability.span(
        GenAIOperation.CHAT,
        model_for(primary_role),
        temperature=temperature,
        max_tokens=max_tokens_effective,
    ) as span:

        async def _account(response: Any) -> tuple[Any, Usage, str]:  # noqa: ANN401
            """Record usage/cost/ledger for one real model call (per attempt)."""
            message = response.choices[0].message
            usage_obj = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            # Custom gateway deployments aren't in the provider's cost map, so
            # completion_cost returns 0 — fall back to a measured-unit estimate so
            # the cost dashboard reflects real spend instead of a silent $0.
            cost, cost_source = _resolve_cost(
                litellm,
                response,
                primary_role,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                images=images,
            )
            record_call(
                model_for(primary_role),
                cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                images=images,
                role=primary_role,
            )
            response_model = getattr(response, "model", None) or model_for(primary_role)
            _observability.set_usage(
                span,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cost_usd=cost,
                response_model=response_model,
            )
            # Durable per-call spend record for the governed principal (best-effort).
            await _record_usage(
                gov_ctx,
                model=response_model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                images=images,
            )
            usage = Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
                images=images,
                cost_source=cost_source,
            )
            return message, usage, response_model

        response = await _attempt(
            litellm,
            kwargs,
            timeout=outer_timeout,
            role=role,
            primary_role=primary_role,
        )
        message, usage, response_model = await _account(response)
        _attribute_response(
            role=role,
            primary_role=primary_role,
            fallback_roles=fallback_roles,
            response_model=response_model,
        )
        content = message.content or ""

        # One corrective re-ask when structured JSON was requested but the reply
        # doesn't parse — exactly once, no loop. A tool-call reply (empty content
        # by design) is left untouched.
        if (
            _wants_json(response_format)
            and not getattr(message, "tool_calls", None)
            and not _is_valid_json(content)
        ):
            logger.info(
                "Structured-output re-ask: first JSON reply was invalid; "
                "retrying once with a corrective nudge."
            )
            reask_kwargs = {
                **kwargs,
                "messages": [
                    *messages,
                    {"role": "user", "content": _JSON_REASK_NUDGE},
                ],
            }
            response = await _attempt(
                litellm,
                reask_kwargs,
                timeout=outer_timeout,
                role=role,
                primary_role=primary_role,
            )
            message, usage, response_model = await _account(response)
            _attribute_response(
                role=role,
                primary_role=primary_role,
                fallback_roles=fallback_roles,
                response_model=response_model,
            )
            content = message.content or ""

        return LLMResult(
            content=content,
            tool_calls=_parse_tool_calls(message),
            usage=usage,
            model=response_model,
        )


async def embed(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` with the fixed embedding model.

    Args:
        texts: The strings to embed.

    Returns:
        One embedding vector (``list[float]``) per input, in order.

    Raises:
        BudgetExceededError: When the injected governance hook refuses the call.
    """
    gov_ctx = _governance.get_context()
    if gov_ctx is not None:
        await _governance.enforce(gov_ctx)

    litellm = _litellm()
    model = _provider_model(ModelRole.EMBEDDING)
    timeout = _get_config().timeout_seconds

    embedding_model = model_for(ModelRole.EMBEDDING)
    async with _observability.span(GenAIOperation.EMBEDDINGS, embedding_model) as span:
        # Bound the embeddings call the same way ``complete`` bounds generation:
        # the ``timeout`` kwarg caps the upstream, and the outer ``wait_for`` is a
        # hard backstop so a hung embeddings endpoint (on the hot retrieval path)
        # can never block a run indefinitely.
        response = await asyncio.wait_for(
            litellm.aembedding(model=model, input=texts, timeout=timeout, **_base_kwargs()),
            timeout=timeout + 5.0,
        )
        usage_obj = getattr(response, "usage", None)
        prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        # The configured embedding deployment is a custom gateway id that is NOT in
        # LiteLLM's cost map, so ``completion_cost`` returns 0 for every embedding.
        # Without the same estimate fallback ``complete`` uses, every embedding row
        # ledgered $0.00 — embeddings never counted against a USD cap.
        cost, _cost_source = _resolve_cost(
            litellm, response, ModelRole.EMBEDDING, prompt_tokens=prompt_tokens
        )
        # ...and they were invisible to ``usage_tally`` because ``record_call`` was
        # never invoked for them. It is now (as a non-routable role, so the
        # small-model-share denominator is untouched).
        record_call(
            embedding_model,
            cost,
            prompt_tokens=prompt_tokens,
            role=ModelRole.EMBEDDING,
        )
        _observability.set_usage(
            span,
            input_tokens=prompt_tokens,
            cost_usd=cost,
            response_model=embedding_model,
        )
        await _record_usage(
            gov_ctx,
            model=embedding_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            cost_usd=cost,
        )
        return [item["embedding"] for item in response.data]


# ─────────────────────────────────────────────────────────────────────────────
# Audio — transcription (``ModelRole.VOICE``), billed per audio-minute
# ─────────────────────────────────────────────────────────────────────────────

@contextmanager
def _audio_handle(audio: Any) -> Iterator[Any]:  # noqa: ANN401
    """Yield a readable binary handle for ``audio``.

    A caller may pass an already-open handle (which is left alone — whoever
    opened it owns closing it) or a filesystem path, which is opened here and
    closed on the way out even if the call raises.
    """
    if isinstance(audio, (str, Path, os.PathLike)):
        with Path(os.fspath(audio)).open("rb") as handle:
            yield handle
    else:
        yield audio


def _transcription_field(response: Any, name: str) -> Any:  # noqa: ANN401
    """Read ``name`` off a transcription response (attribute or mapping key)."""
    value = getattr(response, name, None)
    if value is None and isinstance(response, dict):
        value = response.get(name)
    return value


def _parse_segments(raw: Any) -> list[TranscriptionSegment]:  # noqa: ANN401
    """Parse a provider's ``segments`` into typed segments, tolerating shapes.

    Providers return segments as dicts or objects and omit them entirely for the
    plain-text response formats; anything unparseable yields an empty list rather
    than a fabricated segment.
    """
    segments: list[TranscriptionSegment] = []
    for item in raw or []:
        get = item.get if isinstance(item, dict) else lambda k, _i=item: getattr(_i, k, None)
        try:
            segments.append(
                TranscriptionSegment(
                    id=get("id"),
                    start=get("start"),
                    end=get("end"),
                    text=str(get("text") or ""),
                )
            )
        except (TypeError, ValueError):  # pragma: no cover - defensive
            logger.debug("Skipping unparseable transcription segment.", exc_info=True)
    return segments


async def transcribe(
    audio: Any,  # noqa: ANN401 - a binary file handle or a filesystem path
    *,
    language: str | None = None,
    prompt: str | None = None,
    response_format: str = "verbose_json",
    duration_seconds: float | None = None,
) -> TranscriptionResult:
    """Transcribe ``audio`` on the fleet's hosted voice model (``ModelRole.VOICE``).

    Policy is fleet-only, so this is a hosted call to the routed ``VOICE``
    deployment through the same custom OpenAI-compatible provider as every other
    call — not a local Whisper. Unlike ``complete``/``embed``, LiteLLM's
    transcription API takes a **file handle**, not ``messages``.

    Billing is per minute of audio, not per token, so a transcription would
    otherwise ledger ``prompt_tokens=0`` → ``$0.00`` and slip past a USD cap
    entirely. The audio duration is therefore the billable unit: taken from the
    provider's own ``duration`` when it reports one (``verbose_json``), else from
    the caller's ``duration_seconds``. If neither is available the cost is tagged
    ``CostSource.UNPRICED`` and logged — visible, never a silent zero.

    Args:
        audio: An open binary file handle, or a path to an audio file (opened and
            closed here).
        language: Optional ISO-639-1 hint for the source language.
        prompt: Optional decoding hint (proper nouns, formatting).
        response_format: Provider response format; the default ``verbose_json``
            is what carries ``duration``/``segments``, i.e. the billing unit.
        duration_seconds: The clip's known duration, used when the provider does
            not report one (e.g. a plain-``text`` response format).

    Returns:
        A :class:`TranscriptionResult` with the transcript, the provider's
        language/duration/segments where given, and the billed usage.

    Raises:
        BudgetExceededError: When the injected governance hook refuses the call.
    """
    gov_ctx = _governance.get_context()
    if gov_ctx is not None:
        # Budget/rate check BEFORE spend — identical, modality-agnostic gate to
        # the one ``complete`` applies; a voice call is spend like any other.
        await _governance.enforce(gov_ctx)

    litellm = _litellm()
    voice_model = model_for(ModelRole.VOICE)
    timeout = _get_config().timeout_seconds

    async with _observability.span(GenAIOperation.TRANSCRIPTION, voice_model) as span:
        with _audio_handle(audio) as handle:
            kwargs: dict[str, Any] = {
                "model": _provider_model(ModelRole.VOICE),
                # LiteLLM's transcription API takes a FILE HANDLE, not ``messages``.
                "file": handle,
                "response_format": response_format,
                "timeout": timeout,
                **_base_kwargs(),
            }
            if language:
                kwargs["language"] = language
            if prompt:
                kwargs["prompt"] = prompt
            # Bounded exactly like ``embed``: the ``timeout`` kwarg caps the
            # upstream and the outer ``wait_for`` is a hard backstop.
            response = await asyncio.wait_for(
                litellm.atranscription(**kwargs), timeout=timeout + 5.0
            )

        text = str(_transcription_field(response, "text") or "")
        detected_language = _transcription_field(response, "language")
        raw_duration = _transcription_field(response, "duration")
        reported = float(raw_duration) if raw_duration is not None else None
        billable_seconds = reported if reported is not None else duration_seconds
        segments = _parse_segments(_transcription_field(response, "segments"))

        usage_obj = _transcription_field(response, "usage")
        completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)

        if billable_seconds is None:
            logger.warning(
                "Transcription on %s reported no duration and none was supplied; "
                "its per-minute charge cannot be determined.",
                voice_model,
            )
        audio_seconds = float(billable_seconds or 0.0)

        cost, cost_source = _resolve_cost(
            litellm,
            response,
            ModelRole.VOICE,
            completion_tokens=completion_tokens,
            audio_seconds=audio_seconds,
            # The provider transcribed something; if we still cannot price it,
            # that is an UNPRICED call, never a free one.
            billable_work=True,
        )
        record_call(
            voice_model,
            cost,
            completion_tokens=completion_tokens,
            audio_seconds=audio_seconds,
            role=ModelRole.VOICE,
        )
        response_model = _transcription_field(response, "model") or voice_model
        _observability.set_usage(
            span,
            output_tokens=completion_tokens or None,
            cost_usd=cost,
            response_model=str(response_model),
        )
        await _record_usage(
            gov_ctx,
            model=str(response_model),
            prompt_tokens=0,
            completion_tokens=completion_tokens,
            cost_usd=cost,
            audio_seconds=audio_seconds,
        )
        return TranscriptionResult(
            text=text,
            language=str(detected_language) if detected_language else None,
            duration_seconds=billable_seconds,
            segments=segments,
            usage=Usage(
                completion_tokens=completion_tokens,
                cost_usd=cost,
                audio_seconds=audio_seconds,
                cost_source=cost_source,
            ),
            model=str(response_model),
        )


def last_trace_id() -> str | None:
    """Return the current trace id (hex) from the injected observability sink."""
    return _observability.trace_id()
