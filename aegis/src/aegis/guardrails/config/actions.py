"""Custom NeMo Guardrails actions — the bridge from Colang to our Python rails.

Each ``@action`` here is referenced by an ``execute`` in ``rails/*.co`` and
delegates to the same pure/API checks that back
:func:`aegis.guardrails.pipeline.check_input` and
:func:`aegis.guardrails.pipeline.check_output`. Keeping the logic in one place
means the declarative Colang policy and the fast programmatic API can never
drift apart.

Actions are declared ``is_system_action=True`` so NeMo runs them locally and
injects the conversation ``context`` (from which we read ``user_message`` /
``bot_message``). They return either a boolean (allow/deny, used by the Colang
``if not $x`` branches) or the redacted string (assigned back to the message
variable).

``nemoguardrails`` is accessed via :func:`aegis.core.lazy.require`: this module is only
imported at live-integration time (via :mod:`aegis.guardrails.nemo`), never by the
offline unit tests. Verified against NeMo Guardrails 0.23, Colang 1.0.
"""

from __future__ import annotations

from aegis.core.lazy import require
from aegis.guardrails import classifier, content_safety, pii, schema

nemoguardrails_actions = require("aegis[nemo]", "nemoguardrails.actions")
action = nemoguardrails_actions.action


def _completer():  # noqa: ANN202 - returns aegis.core.interfaces.ChatCompleter | None
    """Return the host-wired ``ChatCompleter`` for the model-based rails, if any.

    Read lazily (per call) from :mod:`aegis.guardrails.nemo` so the actions pick up
    whatever completer the host wired via ``nemo.set_completer`` — exactly how the
    programmatic pipeline (:class:`aegis.guardrails.Guardrails`) receives its
    completer. ``None`` (the offline default) runs the deterministic backstop only.
    """
    from aegis.guardrails import nemo

    return nemo.get_completer()


@action(is_system_action=True)
async def validate_input_schema(context: dict | None = None) -> bool:
    """Return ``True`` if the inbound user message is structurally valid.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        Whether the message passes schema/format validation.
    """
    text = (context or {}).get("user_message", "")
    return schema.validate_input_format(text).ok


@action(is_system_action=True)
async def redact_pii_input(context: dict | None = None) -> str:
    """Return the inbound user message with any PII masked.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        The redacted message text.
    """
    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    return redacted


@action(is_system_action=True)
async def self_check_injection(context: dict | None = None) -> bool:
    """Return ``True`` if the inbound message is *safe* (not prompt injection).

    PII is redacted before the injection check so no secret would leak to a
    model-based classifier call.

    The full model-based layer runs when the host has wired a ``ChatCompleter``
    via :func:`aegis.guardrails.nemo.set_completer` (the backend shim does this
    with its cost-routed cheap-model gateway); with no completer wired the
    deterministic signature backstop runs on its own (logged, not silent — see
    :func:`aegis.guardrails.classifier.detect_injection`). This is the same
    completer the programmatic pipeline
    (:func:`aegis.guardrails.pipeline.Guardrails.check_input`) uses, so the two
    front doors enforce identically.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        ``True`` when safe to proceed, ``False`` to block. Fails closed.
    """
    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await classifier.detect_injection(redacted, completer=_completer())
    return not verdict.injection


@action(is_system_action=True)
async def self_check_content_safety(context: dict | None = None) -> bool:
    """Return ``True`` if the message clears the MLCommons content-safety screen.

    Screens against the MLCommons / Llama Guard hazard taxonomy (S1–S13) via
    :func:`aegis.guardrails.content_safety.screen_content`, delegating to the same
    primitive the programmatic pipeline uses on both the inbound and outbound path
    — so the declarative NeMo policy covers the same layers. Reads ``bot_message``
    on the output rail, falling back to ``user_message`` on the input rail. PII is
    redacted first so no secret reaches a model-based safety call. Fails closed.

    Args:
        context: The NeMo conversation context (``bot_message``/``user_message``).

    Returns:
        ``True`` when safe to proceed, ``False`` to block.
    """
    ctx = context or {}
    text = ctx.get("bot_message") or ctx.get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await content_safety.screen_content(redacted, completer=_completer())
    return not verdict.unsafe


@action(is_system_action=True)
async def validate_output_schema(context: dict | None = None) -> bool:
    """Return ``True`` if the model's answer passes schema + content filter.

    Args:
        context: The NeMo conversation context (``bot_message`` is read).

    Returns:
        Whether the output is structurally valid and clears the content filter.
    """
    text = (context or {}).get("bot_message", "")
    return schema.validate_output_format(text).ok and schema.content_filter(text).ok


@action(is_system_action=True)
async def redact_pii_output(context: dict | None = None) -> str:
    """Return the model's answer with any PII masked.

    Args:
        context: The NeMo conversation context (``bot_message`` is read).

    Returns:
        The redacted answer text.
    """
    text = (context or {}).get("bot_message", "")
    redacted, _ = pii.redact(text)
    return redacted
