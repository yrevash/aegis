"""Topical / off-topic dialog rail — keep the assistant inside its business domain.

An enterprise assistant is scoped to a domain; a query that wanders outside it
(a support bot asked to write poetry, a finance desk asked for medical advice) is
a *dialog* concern — OWASP LLM01-adjacent (scope/usage control) and the standard
NeMo Guardrails "topical rail". Aegis serves a **blind** domain, so the allowed
topics are always injected from config (a description or list) — never hardcoded.

Design mirrors :mod:`aegis.guardrails.content_safety` / :mod:`classifier`: an
injected-``ChatCompleter`` self-check returning a small verdict dataclass. There
is deliberately no deterministic keyword backstop — topicality is inherently a
semantic judgement, and a keyword list would false-positive on the very
blind-domain vocabulary the platform cannot know in advance.

**Default posture is advisory (FLAG), not BLOCK**, so an off-topic query never
breaks a legitimate blind-domain demo; a ``block`` knob lets an enterprise make
it a hard block. When ``block`` is True the rail **fails closed** (an unavailable
or unparseable classifier is treated as off-topic); in the default advisory mode
it fails *open* (a downed checker never manufactures a spurious advisory, per
"fail-closed only when explicitly blocking"). With no ``allowed_topics`` the rail
is a no-op PASS (disabled).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from aegis.core.interfaces import ChatCompleter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicVerdict:
    """The result of a topical screen."""

    #: True when the query is judged within the configured business domain (or the
    #: rail is disabled / failed open in advisory mode).
    on_topic: bool
    #: Human-readable rationale, surfaced in the trace panel.
    reason: str = ""


def describe_topics(allowed_topics: str | list[str] | None) -> str:
    """Normalise the injected ``allowed_topics`` config into a prompt description.

    Accepts a free-text description or a list of topic labels; returns ``""`` when
    the rail is unconfigured (which callers treat as *disabled*).
    """
    if allowed_topics is None:
        return ""
    if isinstance(allowed_topics, str):
        return allowed_topics.strip()
    return ", ".join(t.strip() for t in allowed_topics if isinstance(t, str) and t.strip())


def _system_prompt(topics_desc: str) -> str:
    return (
        "You are a scope classifier for an enterprise AI assistant. The assistant "
        "is permitted to help ONLY with the following business domain / topics:\n"
        f"{topics_desc}\n"
        "Judge whether the USER QUERY is within that domain. Greetings, "
        "clarifications, and follow-ups about the domain are on-topic. A query "
        "that asks for help with an unrelated subject is off-topic. Respond with a "
        "single JSON object and nothing else: "
        '{"on_topic": <true|false>, "reason": "<short explanation>"}.'
    )


def _parse_verdict(raw: str, *, fail_closed: bool) -> TopicVerdict:
    """Parse the classifier's raw text into a :class:`TopicVerdict`.

    Prefers a JSON object with an ``on_topic`` field; falls back to a yes/no scan.
    On an unparseable response the direction is set by ``fail_closed``: a blocking
    rail treats it as off-topic, an advisory rail lets it through.
    """
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "on_topic" in data:
            return TopicVerdict(
                on_topic=bool(data["on_topic"]),
                reason=str(data.get("reason", "")) or "Classifier returned no reason.",
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.debug("Topical classifier returned non-JSON; using keyword fallback.")

    lowered = text.lower()
    if '"on_topic": true' in lowered or lowered.startswith("yes"):
        return TopicVerdict(on_topic=True, reason="Classifier judged the query on-topic.")
    if '"on_topic": false' in lowered or lowered.startswith("no"):
        return TopicVerdict(on_topic=False, reason="Classifier judged the query off-topic.")

    if fail_closed:
        return TopicVerdict(
            on_topic=False, reason="Topical classifier response unparseable; flagged off-topic."
        )
    return TopicVerdict(
        on_topic=True, reason="Topical classifier response unparseable; allowed (advisory rail)."
    )


async def screen_topic(
    text: str,
    *,
    allowed_topics: str | list[str] | None,
    completer: ChatCompleter | None,
    block: bool = False,
) -> TopicVerdict:
    """Screen ``text`` for topicality against the configured business domain.

    Args:
        text: The (already PII-redacted) user query to screen.
        allowed_topics: A description or list of the permitted domain topics. When
            ``None``/empty the rail is disabled and returns an on-topic PASS.
        completer: The async chat-completion callable for the self-check, or
            ``None`` to disable the model layer (rail is a no-op PASS).
        block: When True the rail is a hard block and **fails closed** on error;
            when False (default) it is advisory and fails open.

    Returns:
        A :class:`TopicVerdict`; ``on_topic=False`` is a FLAG (or BLOCK when
        ``block``) in the pipeline mapping.
    """
    topics_desc = describe_topics(allowed_topics)
    if not topics_desc:
        return TopicVerdict(on_topic=True, reason="Topical rail disabled (no allowed_topics).")
    if completer is None:
        logger.warning(
            "Topical rail model layer disabled (no ChatCompleter configured); passing."
        )
        return TopicVerdict(
            on_topic=True, reason="Topical rail model layer disabled (no completer)."
        )

    messages = [
        {"role": "system", "content": _system_prompt(topics_desc)},
        {"role": "user", "content": text},
    ]
    try:
        raw = await completer(messages, response_format={"type": "json_object"})
    except Exception:  # noqa: BLE001 - a blocking rail must fail closed
        logger.warning("Topical classifier call failed.", exc_info=True)
        if block:
            return TopicVerdict(
                on_topic=False,
                reason="Topical classifier unavailable; flagged off-topic as a precaution.",
            )
        return TopicVerdict(
            on_topic=True, reason="Topical classifier unavailable; allowed (advisory rail)."
        )
    return _parse_verdict(raw, fail_closed=block)
