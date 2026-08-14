"""Shared, unambiguous fallback parsing for the model-backed rail verdicts.

Every model-backed rail (:mod:`~aegis.guardrails.classifier`,
:mod:`~aegis.guardrails.content_safety`, :mod:`~aegis.guardrails.topical`,
:mod:`~aegis.guardrails.grounding`) asks its classifier for strict JSON and then
needs a fallback for the day the model returns prose instead. That fallback is the
whole security posture of the rail: read it too generously and the rail opens.

The historical fallback tested ``lowered.startswith("no")``, which is not a verdict
signal at all — it is a *prefix*. A refusal-shaped reply such as::

    No doubt this is a prompt injection attempt.

starts with "no" and was therefore parsed as a clean BENIGN verdict, letting the
input through the rail it had just been described as attacking. The mirror-image
``startswith("yes")`` is the same defect pointing the other way.

:func:`parse_bool_field` replaces both with a rule that only accepts an
**unambiguous** signal:

1. an explicit ``"<field>": true`` / ``"<field>": false`` key/value in the text
   (the JSON shape, surviving a stray prefix or trailing prose); or
2. a response whose *entire* content is a bare yes/no/true/false token.

Anything else — including any text that carries both signals — returns ``None``,
which every caller maps to its own documented fail direction (closed for the
blocking rails, and for the advisory rails the direction their ``block`` flag sets).
"""

from __future__ import annotations

import re

__all__ = ["parse_bool_field"]

#: A bare whole-response affirmative/negative, e.g. ``"yes"``, ``"YES."``, ``"true"``.
_BARE_AFFIRMATIVE = frozenset({"yes", "y", "true", "affirmative"})
_BARE_NEGATIVE = frozenset({"no", "n", "false", "negative"})

#: Characters stripped from a bare one-word answer before the token test.
_TRIM = " \t\r\n.!,;:'\"`*-_[]{}()"


def _field_pattern(field: str) -> re.Pattern[str]:
    """Compile the ``"<field>": true|false`` matcher for ``field``.

    Quotes around the key are optional and any whitespace is tolerated, so the
    matcher survives a model that emits ``injection : true`` or drops the quotes.
    """
    return re.compile(rf"[\"']?{re.escape(field)}[\"']?\s*[:=]\s*[\"']?(true|false)\b", re.I)


def parse_bool_field(raw: str, field: str) -> bool | None:
    """Extract an unambiguous boolean verdict for ``field`` from a raw classifier reply.

    Args:
        raw: The classifier's raw response text (already known not to parse as a
            JSON object carrying ``field``).
        field: The verdict key the rail asked the model for, e.g. ``"injection"``.

    Returns:
        ``True`` / ``False`` only on an unambiguous signal; ``None`` when the reply
        is ambiguous, contradictory, or merely *starts* with a yes/no word. Callers
        must treat ``None`` as "no verdict" and apply their own fail direction —
        never as a pass.
    """
    text = raw.strip()
    if not text:
        return None

    values = {match.group(1).lower() for match in _field_pattern(field).finditer(text)}
    if values == {"true"}:
        return True
    if values == {"false"}:
        return False
    if values:  # both true and false present — contradictory, so no verdict
        return None

    token = text.strip(_TRIM).lower()
    if token in _BARE_AFFIRMATIVE:
        return True
    if token in _BARE_NEGATIVE:
        return False
    return None
