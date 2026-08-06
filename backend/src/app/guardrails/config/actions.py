"""Custom NeMo Guardrails actions — the bridge from Colang to our Python rails.

Each ``@action`` here is referenced by an ``execute`` in ``rails/*.co`` and
delegates to the same pure/API checks that back
:func:`app.guardrails.check_input` / :func:`app.guardrails.check_output`. Keeping
the logic in one place means the declarative Colang policy and the fast
programmatic API can never drift apart.

Actions are declared ``is_system_action=True`` so NeMo runs them locally and
injects the conversation ``context`` (from which we read ``user_message`` /
``bot_message``). They return either a boolean (allow/deny, used by the Colang
``if not $x`` branches) or the redacted string (assigned back to the message
variable).

``nemoguardrails`` is imported at module top *intentionally*: this module is only
imported at live-integration time (via :mod:`app.guardrails.nemo`), never by the
offline unit tests. Verified against NeMo Guardrails 0.23, Colang 1.0.
"""

from __future__ import annotations

from nemoguardrails.actions import action

from app.guardrails import classifier, pii, schema


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

    PII is redacted before the classifier call so no secret leaves for the API.

    Args:
        context: The NeMo conversation context (``user_message`` is read).

    Returns:
        ``True`` when safe to proceed, ``False`` to block. Fails closed.
    """
    text = (context or {}).get("user_message", "")
    redacted, _ = pii.redact(text)
    verdict = await classifier.detect_injection(redacted)
    return not verdict.injection


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
