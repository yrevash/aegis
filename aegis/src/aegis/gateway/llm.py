"""LiteLLM gateway — the single async entry point to a heterogeneous model fleet.

Every model call goes through :func:`complete` or :func:`embed`, which route by
:class:`~aegis.core.models.ModelRole` (never a hard-coded id) and talk to a
**custom OpenAI-compatible provider**:

- model string form ``openai/<deployment_id>`` (bare deployment names, no
  ``azure/`` prefix), with ``api_base`` + ``api_key`` supplied per call;
- a self-signed gateway certificate is handled via the injected
  :class:`GatewayConfig` (``ssl_verify=False`` disables TLS verification for it);
- token usage and cost are captured and handed to the injected observability
  sink;
- tool calls are parsed into typed :class:`~aegis.gateway.types.ToolCallResult`
  objects.

Budget/rate governance and observability are **injected hooks** (see
:func:`configure`), each defaulting to a documented no-op so this module has no
hard dependency on any particular host application. ``litellm`` is imported
lazily (inside :func:`_litellm`) so importing this module never requires the
package — the unit tests inject a fake ``litellm`` and never touch the network.

Verified against: LiteLLM 1.52+ (``acompletion`` / ``aembedding`` custom
OpenAI-compatible provider, ``litellm.ssl_verify``, ``completion_cost``),
August 2026.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

from aegis.core.models import ModelRole
from aegis.gateway.routing import (
    _rate_for,
    baseline_role,
    is_small_model,
    model_for,
    routing_table,
)
from aegis.gateway.types import BudgetExceededError, LLMResult, ToolCallResult, Usage

logger = logging.getLogger(__name__)

__all__ = [
    "BudgetExceededError",
    "GatewayConfig",
    "GenAIOperation",
    "GovernanceHook",
    "LLMResult",
    "ObservabilitySink",
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


class GenAIOperation(StrEnum):
    """The GenAI operation an observability span is opened for."""

    CHAT = "chat"
    EMBEDDINGS = "embeddings"


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
    ) -> None:
        """Write one durable usage-ledger row for a governed call.

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
    """
    global _config, _governance, _observability
    global _fallbacks_override, _baseline_role_override
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


def _estimate_cost(role: ModelRole, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate call cost from tokens when the provider has no price for the model."""
    rate_in, rate_out = _rate_for(role)
    return (prompt_tokens / 1000) * rate_in + (completion_tokens / 1000) * rate_out


@dataclass
class _RoleAgg:
    """Per-role accumulation of real chat completions (calls, tokens, cost)."""

    calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


@dataclass
class _UsageTally:
    """Process-wide count of chat completions, for the measured efficiency metric.

    ``by_role`` is a supplementary per-role breakdown; the top-line counters stay
    the single source of truth (``usage_tally`` reads only them), and the per-role
    costs always sum to ``total_cost_usd`` because every recorded call is
    attributed to exactly one role.
    """

    total_calls: int = 0
    small_calls: int = 0
    total_cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0
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


def record_call(
    model_id: str,
    cost_usd: float,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    role: ModelRole | None = None,
) -> None:
    """Record one chat completion for the measured efficiency metrics.

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
        role: The routing role, used only for the supplementary per-role
            breakdown; when omitted it is inferred from ``model_id``.
    """
    _tally.total_calls += 1
    _tally.small_calls += int(is_small_model(model_id))
    _tally.total_cost_usd += cost_usd
    _tally.baseline_cost_usd += _estimate_cost(
        _effective_baseline_role(), prompt_tokens, completion_tokens
    )
    agg = _tally.by_role.setdefault(_attribution_role(model_id, role), _RoleAgg())
    agg.calls += 1
    agg.prompt_tokens += prompt_tokens
    agg.completion_tokens += completion_tokens
    agg.cost_usd += cost_usd


def usage_tally() -> dict[str, Any]:
    """Return live call counts. ``small_model_share`` is ``None`` before any call."""
    total = _tally.total_calls
    baseline = _tally.baseline_cost_usd
    actual = _tally.total_cost_usd
    return {
        "total_calls": total,
        "small_calls": _tally.small_calls,
        "total_cost_usd": actual,
        "baseline_cost_usd": baseline,
        "cost_saved_usd": max(0.0, baseline - actual),
        "small_model_share": (_tally.small_calls / total) if total else None,
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
    cap, and the frontier-baseline role/model the savings calc prices against.
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
) -> None:
    """Best-effort ledger write for a governed call: swallow, log, never raise.

    A ledger write is a durable record, not a control path — a failure here must
    never fail the model call that already succeeded (or was already refused).
    """
    if ctx is None:
        return
    try:
        await _governance.record(
            ctx,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            trace_id=_observability.trace_id(),
        )
    except Exception:  # noqa: BLE001 - ledger is best-effort at the edge
        logger.warning("Usage-ledger write failed.", exc_info=True)


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
        token/cost usage.

    Raises:
        BudgetExceededError: When the injected governance hook refuses the call.
    """
    gov_ctx = _governance.get_context()
    if gov_ctx is not None:
        # Budget/rate check BEFORE spend — refuse the call if over any cap.
        await _governance.enforce(gov_ctx)

    config = _get_config()
    litellm = _litellm()
    model = _provider_model(role)

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

    fallbacks = [_provider_model(r) for r in _effective_fallbacks().get(role, [])]
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
        model_for(role),
        temperature=temperature,
        max_tokens=max_tokens_effective,
    ) as span:

        async def _account(response: Any) -> tuple[Any, int, int, float, str]:  # noqa: ANN401
            """Record usage/cost/ledger for one real model call (per attempt)."""
            message = response.choices[0].message
            usage_obj = getattr(response, "usage", None)
            prompt_tokens = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
            completion_tokens = int(getattr(usage_obj, "completion_tokens", 0) or 0)
            cost = _safe_cost(litellm, response)
            if cost <= 0.0:
                # Custom gateway deployments aren't in the provider's cost map, so
                # completion_cost returns 0 — fall back to a token-based estimate
                # so the cost dashboard reflects real spend instead of $0.
                cost = _estimate_cost(role, prompt_tokens, completion_tokens)
            record_call(
                model_for(role),
                cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                role=role,
            )
            response_model = getattr(response, "model", None) or model_for(role)
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
            )
            return message, prompt_tokens, completion_tokens, cost, response_model

        response = await _bounded_acompletion(litellm, kwargs, timeout=outer_timeout)
        message, prompt_tokens, completion_tokens, cost, response_model = await _account(
            response
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
            response = await _bounded_acompletion(litellm, reask_kwargs, timeout=outer_timeout)
            message, prompt_tokens, completion_tokens, cost, response_model = await _account(
                response
            )
            content = message.content or ""

        return LLMResult(
            content=content,
            tool_calls=_parse_tool_calls(message),
            usage=Usage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cost_usd=cost,
            ),
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
        cost = _safe_cost(litellm, response)
        _observability.set_usage(
            span,
            input_tokens=prompt_tokens,
            cost_usd=cost,
            response_model=model_for(ModelRole.EMBEDDING),
        )
        await _record_usage(
            gov_ctx,
            model=model_for(ModelRole.EMBEDDING),
            prompt_tokens=prompt_tokens,
            completion_tokens=0,
            cost_usd=cost,
        )
        return [item["embedding"] for item in response.data]


def last_trace_id() -> str | None:
    """Return the current trace id (hex) from the injected observability sink."""
    return _observability.trace_id()
