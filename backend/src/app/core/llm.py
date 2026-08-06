"""LiteLLM gateway — the single async entry point to the Azure model fleet.

Every model call in the app goes through :func:`complete` or :func:`embed`, which
route by :class:`~app.core.models.ModelRole` (never a hard-coded id) and talk to
the TCS GenAI Lab as a **custom OpenAI-compatible provider**:

- model string form ``openai/<deployment_id>`` (bare deployment names, no
  ``azure/`` prefix), with ``api_base`` + ``api_key`` supplied per call;
- the gateway's self-signed certificate is handled via ``settings``
  (``genailab_ssl_verify=False`` disables TLS verification for it);
- token usage and cost are captured and attached to a ``gen_ai.*`` OTel span;
- tool calls are parsed into typed :class:`ToolCallResult` objects.

``litellm`` is imported lazily (inside :func:`_litellm`) so importing this module
never requires the package — the unit tests inject a fake ``litellm`` and never
touch the network.

Verified against: LiteLLM 1.52+ (``acompletion`` / ``aembedding`` custom
OpenAI-compatible provider, ``litellm.ssl_verify``, ``completion_cost``),
August 2026.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.config import get_settings
from app.core.governance import GovernanceContext, get_governance_context
from app.core.models import ModelRole, is_small_model, model_for
from app.observability import GenAIOperation, genai_span, set_usage
from app.observability.otel import current_trace_id

logger = logging.getLogger(__name__)


class BudgetExceededError(Exception):
    """A per-tenant/user budget or rate cap was hit at the gateway chokepoint (§3.3).

    Raised by :func:`complete` / :func:`embed` **before** the model call when the
    governed principal is over one of its caps, so the run degrades to "budget
    exceeded" instead of runaway cost. The orchestrator catches it and surfaces the
    terminal ``budget_exceeded`` SSE event.

    Attributes:
        scope: Which level tripped — ``"tenant"`` or ``"user"``.
        scope_id: Id of the tripped scope.
        limit_type: Which cap tripped — ``"token_cap"`` | ``"usd_cap"`` | ``"rpm"`` |
            ``"tpm"``.
        limit: The configured cap value.
        used: Consumption at refusal time.
    """

    def __init__(
        self,
        *,
        scope: str,
        scope_id: int | None,
        limit_type: str,
        limit: float | None,
        used: float | None,
        message: str | None = None,
    ) -> None:
        """Capture the tripped cap so the wire event can be built from it."""
        self.scope = scope
        self.scope_id = scope_id
        self.limit_type = limit_type
        self.limit = limit
        self.used = used
        self.message = message or (
            f"{scope} {limit_type} exceeded: used {used} of {limit}."
        )
        super().__init__(self.message)

# Per-role fallback chain: if the primary deployment for a role fails, LiteLLM
# retries these deployment ids (routed through the same custom provider).
_ROLE_FALLBACKS: dict[ModelRole, list[ModelRole]] = {
    ModelRole.GENERATION: [ModelRole.REASONING, ModelRole.CHEAP],
    ModelRole.REASONING: [ModelRole.GENERATION, ModelRole.CHEAP],
    ModelRole.CHEAP: [ModelRole.GENERATION],
}

_ssl_configured = False

# One corrective nudge appended when a structured-output (JSON) call returns
# content that does not parse — a single re-ask, never a loop (§ call-safety).
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
    timeout: float,
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

# Per-role token pricing (USD per 1k tokens, input/output) used ONLY as a
# fallback when LiteLLM's cost map has no entry for a custom deployment id —
# which is the case for the ``openai/genailab-maas-*`` gateway. Approximate but
# honest and non-zero; override any role via ``COST_<ROLE>_IN`` / ``COST_<ROLE>_OUT``.
_COST_PER_1K: dict[ModelRole, tuple[float, float]] = {
    ModelRole.CHEAP: (0.00015, 0.0006),
    ModelRole.REASONING: (0.0011, 0.0044),
    ModelRole.GENERATION: (0.0025, 0.01),
    ModelRole.EMBEDDING: (0.00013, 0.0),
    ModelRole.VISION: (0.0025, 0.01),
    ModelRole.VOICE: (0.006, 0.0),
}

def _rate_for(role: ModelRole) -> tuple[float, float]:
    """Return the (input, output) $/1k rate for ``role`` (env-overridable)."""
    default_in, default_out = _COST_PER_1K.get(role, (0.0, 0.0))
    rate_in = float(os.environ.get(f"COST_{role.name}_IN", default_in))
    rate_out = float(os.environ.get(f"COST_{role.name}_OUT", default_out))
    return rate_in, rate_out


def _estimate_cost(role: ModelRole, prompt_tokens: int, completion_tokens: int) -> float:
    """Estimate call cost from tokens when LiteLLM has no price for the model."""
    rate_in, rate_out = _rate_for(role)
    return (prompt_tokens / 1000) * rate_in + (completion_tokens / 1000) * rate_out


@dataclass
class _UsageTally:
    """Process-wide count of chat completions, for the measured efficiency metric."""

    total_calls: int = 0
    small_calls: int = 0
    total_cost_usd: float = 0.0
    baseline_cost_usd: float = 0.0


_tally = _UsageTally()


def record_call(
    model_id: str,
    cost_usd: float,
    *,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> None:
    """Record one chat completion for the measured efficiency metrics.

    Alongside the actual ``cost_usd``, this accumulates a **baseline** cost: what
    the same tokens would have cost had the call been routed to the
    generation-role model. The gap between baseline and actual is the measured
    saving from small-model routing (surfaced as ``cost_saved_usd``).
    """
    _tally.total_calls += 1
    _tally.small_calls += int(is_small_model(model_id))
    _tally.total_cost_usd += cost_usd
    _tally.baseline_cost_usd += _estimate_cost(
        ModelRole.GENERATION, prompt_tokens, completion_tokens
    )


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


class ToolCallResult(BaseModel):
    """A single tool/function call the model asked to make."""

    id: str = Field(description="Provider-assigned tool-call id.")
    name: str = Field(description="Tool/function name.")
    args: dict[str, Any] = Field(
        default_factory=dict, description="Parsed JSON arguments."
    )


class Usage(BaseModel):
    """Token accounting and cost for one model call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: float = 0.0


class LLMResult(BaseModel):
    """The normalised result of a :func:`complete` call."""

    content: str = Field(default="", description="Assistant text (may be empty).")
    tool_calls: list[ToolCallResult] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = Field(default="", description="Deployment id that responded.")


def _litellm() -> Any:  # noqa: ANN401 - third-party module handle
    """Import and configure ``litellm`` lazily (TLS verify set once).

    Returns:
        The ``litellm`` module, with ``ssl_verify`` set from settings on first
        use.
    """
    global _ssl_configured
    import litellm

    if not _ssl_configured:
        # Current LiteLLM way to disable TLS verification for a self-signed
        # gateway: the module-level global. Scoped, documented exception.
        litellm.ssl_verify = get_settings().genailab_ssl_verify
        _ssl_configured = True
    return litellm


def _provider_model(role: ModelRole) -> str:
    """Return the LiteLLM custom-provider model string for ``role``."""
    return f"openai/{model_for(role)}"


def _base_kwargs() -> dict[str, Any]:
    """Return the shared ``api_base`` / ``api_key`` kwargs for every call."""
    settings = get_settings()
    return {
        "api_base": settings.genailab_base_url,
        "api_key": settings.genailab_api_key,
    }


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


def _governed(ctx: GovernanceContext | None) -> GovernanceContext | None:
    """Return the context iff the request is tenant-governed, else ``None``.

    Enforcement and ledgering are fully gated behind a bound tenant: an unscoped
    request (no governance context, the default for every existing flow and the
    offline demo) skips the database entirely and behaves exactly as before.
    """
    if ctx is None or ctx.tenant_id is None:
        return None
    return ctx


async def _enforce_governance(ctx: GovernanceContext) -> None:
    """Raise :class:`BudgetExceededError` if the principal is over any cap (§3.3).

    Reads the authoritative :class:`~app.data.models.Budget` rows and the
    :class:`~app.data.models.UsageLedger` sums for the tenant→user path. A real
    budget breach always propagates. An enforcement **error** (e.g. a database blip)
    fails **CLOSED** by default — the call is denied rather than silently uncapped,
    so a transient failure can never disable every spend cap (M3). Set
    ``budget_fail_open`` to opt into soft/fail-open ceilings.

    The ungoverned (``tenant_id`` ``None``) path never reaches here — the caller
    gates on :func:`_governed`, so an unscoped request stays a clean no-op.
    """
    try:
        from app.data.governance import enforce_governance
    except ImportError:
        # Only a genuinely-absent data layer (offline lite) is a clean no-op. A
        # non-import failure (a real bug in the governance module) must NOT silently
        # disable budget enforcement — let it surface / fall through to fail-closed.
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


async def _ledger_write(
    ctx: GovernanceContext,
    *,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
) -> None:
    """Write one durable usage-ledger row for a governed call (best-effort)."""
    try:
        from app.data.governance import record_usage

        await record_usage(
            tenant_id=ctx.tenant_id,
            user_id=ctx.user_id,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=cost_usd,
            trace_id=current_trace_id(),
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
            concrete deployment id comes from :func:`app.core.models.model_for`.
        messages: OpenAI-style chat messages.
        tools: Optional OpenAI tool/function schemas to offer the model.
        temperature: Sampling temperature (default deterministic ``0.0``).
        response_format: Optional structured-output spec, e.g.
            ``{"type": "json_object"}``.
        max_tokens: Optional per-call output-token ceiling. When omitted, the
            configured default cap (``settings.llm_max_output_tokens``) applies so
            every generation is bounded on cost and latency.

    Returns:
        An :class:`LLMResult` with the assistant text, any parsed tool calls, and
        token/cost usage.
    """
    gov = _governed(get_governance_context())
    if gov is not None:
        # Budget/rate check BEFORE spend — refuse the call if over any cap.
        await _enforce_governance(gov)

    settings = get_settings()
    litellm = _litellm()
    model = _provider_model(role)

    # Per-call output cap: explicit argument wins, else the configured default so
    # no generation is unbounded. (A per-role registry cap would slot in here; the
    # current registry carries none, so nothing to preserve/regress.)
    max_tokens_effective = (
        max_tokens if max_tokens is not None else settings.llm_max_output_tokens
    )
    per_call_timeout = settings.llm_timeout_seconds

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

    fallbacks = [_provider_model(r) for r in _ROLE_FALLBACKS.get(role, [])]
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

    async with genai_span(
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
                # Custom gateway deployments aren't in LiteLLM's cost map, so
                # completion_cost returns 0 — fall back to a token-based estimate
                # so the cost dashboard reflects real spend instead of $0.
                cost = _estimate_cost(role, prompt_tokens, completion_tokens)
            record_call(
                model_for(role),
                cost,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            response_model = getattr(response, "model", None) or model_for(role)
            set_usage(
                span,
                input_tokens=prompt_tokens,
                output_tokens=completion_tokens,
                cost_usd=cost,
                response_model=response_model,
            )
            if gov is not None:
                # Durable per-call spend record for the tenant/user (the ledger).
                await _ledger_write(
                    gov,
                    model=response_model,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    cost_usd=cost,
                )
            return message, prompt_tokens, completion_tokens, cost, response_model

        response = await _bounded_acompletion(
            litellm, kwargs, timeout=outer_timeout
        )
        message, prompt_tokens, completion_tokens, cost, response_model = (
            await _account(response)
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
            response = await _bounded_acompletion(
                litellm, reask_kwargs, timeout=outer_timeout
            )
            message, prompt_tokens, completion_tokens, cost, response_model = (
                await _account(response)
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
    """
    gov = _governed(get_governance_context())
    if gov is not None:
        await _enforce_governance(gov)

    litellm = _litellm()
    model = _provider_model(ModelRole.EMBEDDING)
    timeout = get_settings().llm_timeout_seconds

    async with genai_span(
        GenAIOperation.EMBEDDINGS, model_for(ModelRole.EMBEDDING)
    ) as span:
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
        set_usage(
            span,
            input_tokens=prompt_tokens,
            cost_usd=cost,
            response_model=model_for(ModelRole.EMBEDDING),
        )
        if gov is not None:
            await _ledger_write(
                gov,
                model=model_for(ModelRole.EMBEDDING),
                prompt_tokens=prompt_tokens,
                completion_tokens=0,
                cost_usd=cost,
            )
        return [item["embedding"] for item in response.data]


def last_trace_id() -> str | None:
    """Return the current OTel trace id (hex), for audit correlation."""
    return current_trace_id()
