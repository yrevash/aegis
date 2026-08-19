"""Backend shim: the guardrail system now lives in ``aegis.guardrails``.

This package used to own the full layered, RAM-friendly, defense-in-depth
guardrail implementation described in ``docs/security/overview.md`` §3. That
implementation has moved to the standalone, LLM-agnostic ``aegis.guardrails``
package (see ``/aegis``) so it can be imported by any component without pulling
in this platform's LLM gateway. This module is the **strangler shim**: it
re-exports the package's public API and wires the platform's LiteLLM gateway
(``app.core.llm.complete``) as the injected ``ChatCompleter``, preserving the
previous behaviour (and the previous no-arg ``check_input``/``check_output``
call sites) for the agent graph and the existing test suite.

Public contract (unchanged from before the migration):
    * :class:`GuardResult` — ``verdict`` / ``reason`` / ``text`` (text may be
      redacted).
    * :func:`check_input` — schema/format validation -> PII redaction -> API
      injection/jailbreak classifier.
    * :func:`check_output` — schema/format validation -> content filter -> PII
      redaction.

Two enforcement front doors over **one** policy:
    * The fast programmatic API here (:func:`check_input`/:func:`check_output`),
      which the agent graph calls directly and which the tests exercise offline.
    * A declarative **NeMo Guardrails / Colang** policy (loaded via
      :mod:`app.guardrails.nemo`, itself a shim over ``aegis.guardrails.nemo``)
      whose flows call custom actions that delegate back to the same rails. The
      Colang file doubles as a human-readable security artifact for the jury.

Streaming caveat: the output rail assumes the *complete* answer. When the
answer is streamed token-by-token the rail cannot scan-then-emit, so the caller
must either **buffer briefly** and run :func:`check_output` on the buffered
text, or stream optimistically and **scan post-hoc**, redacting/retracting on a
hit. Never stream raw tokens straight past the output rail.
"""

from __future__ import annotations

import logging

from aegis.core.types import GuardResult, GuardVerdict, InjectionVerdict, PIIMatch
from aegis.guardrails import Guardrails

from app.adapter import DOMAIN_DESCRIPTION
from app.config import get_settings
from app.guardrails import classifier, nemo, pii, schema  # noqa: F401 - re-exported submodules

logger = logging.getLogger(__name__)


async def _gateway_completer(
    messages: list[dict], *, response_format: dict | None = None
) -> str:
    """Adapt ``app.core.llm.complete`` to the ``aegis`` ``ChatCompleter`` protocol.

    This is the **only** place under ``app.guardrails`` that references the
    platform's LLM gateway (``app.core.llm`` / ``app.core.models``); every other
    module here is LLM-agnostic, matching ``aegis.guardrails``. Imports are
    deferred so importing this package never requires the gateway or its
    dependencies (keeps the offline unit tests fast and network-free).

    Args:
        messages: OpenAI-style chat messages.
        response_format: Optional structured-output hint (e.g. JSON mode).

    Returns:
        The assistant's raw text.
    """
    from app.core.llm import complete
    from app.core.models import ModelRole

    result = await complete(
        ModelRole.CHEAP, messages, temperature=0.0, response_format=response_format
    )
    return result.content


#: The process-wide guardrail pipeline, wired with the platform's cheap-model
#: completer. A single instance is fine — ``Guardrails`` holds no per-call state.
#: ``ground_answers=True`` activates the output grounding self-check (OWASP LLM09):
#: when :func:`check_output` is given the retrieved ``contexts``, the answer is judged
#: for groundedness against them (advisory FLAG by default; ``grounding_block`` in
#: settings flips it to a hard BLOCK). With no contexts the grounding rail is a no-op.
#:
#: ``allowed_topics`` is the adapter's own :data:`~app.adapter.DOMAIN_DESCRIPTION`,
#: which is the one sentence in this codebase that says what the platform is *for*.
#: Without it :func:`aegis.guardrails.topical.screen_topic` returns "rail disabled" on
#: every call and ``guardrails.topical.block`` is a control over a rail that never runs
#: — a toggle that saves and changes nothing, which is the defect this whole seam is
#: about. It is the domain seam by construction: retarget the adapter and the topical
#: rail retargets with it, with no edit here.
#:
#: **This object is the platform layer, not the whole policy.** The four ``guardrails.*``
#: catalogue keys are per tenant and are folded onto it per request by
#: :func:`_request_guard`; what is wired here is the floor none of them can go under.
_guard = Guardrails(
    completer=_gateway_completer,
    ground_answers=True,
    grounding_block=get_settings().grounding_block,
    allowed_topics=DOMAIN_DESCRIPTION,
)


async def _request_guard() -> Guardrails:
    """Return the pipeline enforcing **this request's tenant's** rails.

    ``_guard`` is the platform layer. This folds the tenant's four ``guardrails.*``
    settings onto it — tighten-only for the two block toggles, union for the denied
    terms and the PII entity kinds — and returns a per-request pipeline sharing the
    expensive collaborators. It is called on every rail entry rather than once per run
    because the rails are entered from several places (the graph's input and output
    nodes, every tool result) and there is no request-scoped object common to all of
    them to hang a resolution off; a settings resolution is one indexed query, and
    correctness at that price is the right trade against a rail that enforces the wrong
    tenant's policy.

    Returns:
        ``_guard`` itself when the tenant added nothing, otherwise a per-request copy.
        Never retained: it is dropped when the rail call returns.
    """
    from app.guardrails.policy import resolve_request_policy

    return _guard.with_policy(await resolve_request_policy(_guard.policy))


def _use_nemo_engine() -> bool:
    """Whether this request should enforce via the NeMo Colang engine.

    True only when the operator selected ``guardrails_engine="nemo"`` AND the
    optional ``nemoguardrails`` package is importable. Any other value (default
    ``"programmatic"``) — or an unavailable package — keeps the fast programmatic
    pipeline, which is also the fallback so the live path never loses its rails.
    """
    if get_settings().guardrails_engine.strip().lower() != "nemo":
        return False
    if not nemo.nemo_available():
        logger.warning(
            "guardrails_engine='nemo' but nemoguardrails is not importable; "
            "falling back to the programmatic pipeline."
        )
        return False
    # Wire the platform's cheap-model completer into the NeMo custom actions so
    # their model-based layers (injection + content-safety) match the programmatic
    # pipeline. Idempotent; safe to call every request.
    nemo.set_completer(_gateway_completer)
    return True


def _fail_closed(stage: str, exc: Exception) -> GuardResult:
    """Map an engine error to a BLOCK verdict (never silently pass) and log it."""
    logger.exception("NeMo guardrail %s engine errored; failing closed to BLOCK.", stage)
    return GuardResult(
        verdict=GuardVerdict.BLOCK,
        reason=f"Guardrail engine error on the {stage} path; blocked (fail-closed): {exc}",
        text="",
        layer=f"nemo-{stage}",
    )


async def check_input(text: str) -> GuardResult:
    """Run the full input rail (schema -> PII redaction -> injection) via aegis.

    Routes through the engine selected by ``settings.guardrails_engine``: the
    declarative NeMo Colang policy when ``"nemo"`` (and the package is available),
    otherwise the fast programmatic pipeline (the default and the fallback). Both
    emit the same :class:`GuardResult` so the verdict streams identically to the
    frontend. A NeMo engine error fails closed to a BLOCK — never a silent pass.

    Args:
        text: The raw inbound query.

    Returns:
        A :class:`GuardResult`. ``block`` when the payload is malformed or judged
        to be prompt injection; ``redact`` when it was clean of injection but
        carried PII (``text`` is the redacted form); otherwise ``pass``.
    """
    if _use_nemo_engine():
        try:
            return await nemo.nemo_check_input(text)
        except Exception as exc:  # noqa: BLE001 - fail closed, never silently pass
            return _fail_closed("input", exc)
    return await (await _request_guard()).check_input(text)


async def check_output(
    text: str, contexts: list[str] | None = None
) -> GuardResult:
    """Run the full output rail (schema -> content filter -> grounding -> PII) via aegis.

    Routes through the engine selected by ``settings.guardrails_engine`` (see
    :func:`check_input`). A NeMo engine error fails closed to a BLOCK.

    Args:
        text: The model's answer text (assumed complete; see streaming caveat).
        contexts: The retrieved passages the answer was generated from. When
            provided, the output grounding self-check judges the answer against
            them (advisory FLAG by default; ``settings.grounding_block`` hard-blocks).
            ``None``/empty (the default, and every non-graph call site) is a
            grounding no-op, so existing callers are unaffected. The programmatic
            path only — the NeMo Colang engine has no grounding action.

    Returns:
        A :class:`GuardResult`. ``block`` when the output is malformed or trips
        the content filter; ``redact`` when it carried PII (``text`` is the
        redacted form); ``flag`` when it was judged ungrounded (advisory);
        otherwise ``pass``.
    """
    if _use_nemo_engine():
        try:
            return await nemo.nemo_check_output(text)
        except Exception as exc:  # noqa: BLE001 - fail closed, never silently pass
            return _fail_closed("output", exc)
    return await (await _request_guard()).check_output(text, contexts=contexts)


async def check_tool_result(text: str, *, tool_name: str | None = None) -> GuardResult:
    """Run the ``TOOL_RESULT`` rail over one tool's output, via aegis.

    The third rail stage. A record, row, page or summary a tool returns is untrusted
    third-party text that a model will read as instructions-adjacent context — the OWASP
    LLM01 surface — so it is screened by the **inbound** chain (schema → PII → injection
    → content-safety → topical) before it is allowed anywhere near a prompt. The rail
    existed and, until the agent graph was wired to it, web search was its only caller:
    every other tool's output went into the generation prompt and into every sub-agent's
    transcript unscreened.

    Deliberately NOT routed through the NeMo Colang engine even when it is selected:
    that engine models a *conversation* (user says X, bot says Y) and has no tool-result
    action, so sending a tool payload through it would screen it as if a human had typed
    it. The programmatic pipeline is the same set of layers without that mismodelling.

    Args:
        text: Exactly what the tool returned.
        tool_name: The tool that produced it; named in the verdict's rationale so a
            console never shows an anonymous block.

    Returns:
        A :class:`GuardResult`. ``block`` means the content must not reach the agent's
        context at all; ``redact`` means use ``result.text``; ``flag`` is advisory.
    """
    return await (await _request_guard()).check_tool_result(text, tool_name=tool_name)


__all__ = [
    "GuardResult",
    "Guardrails",
    "InjectionVerdict",
    "PIIMatch",
    "check_input",
    "check_output",
    "check_tool_result",
    "classifier",
    "nemo",
    "pii",
    "schema",
]
