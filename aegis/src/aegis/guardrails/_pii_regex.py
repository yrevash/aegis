"""Pure-code regex PII engine — the offline fallback for :mod:`aegis.guardrails.pii`.

Historically this *was* the whole PII engine (curated anchored regexes plus a Luhn
check). It now sits behind Microsoft Presidio (see ``_pii_presidio.py``) and is used
only when Presidio/spaCy are unavailable, so Aegis never crashes and never silently
stops redacting. It is deliberately dependency-free (regex + stdlib only) and runs
in microseconds.

Design notes (unchanged from the original engine):
    * Patterns are **anchored and specific** to keep the false-positive rate low.
    * Overlapping matches are resolved by preferring the **longest** span at each
      position, so a 16-digit card is never mis-attributed to a phone rail.
    * Redaction replaces each span with a stable ``[REDACTED_<KIND>]`` token.

The public facade (:mod:`aegis.guardrails.pii`) imports :func:`scan`,
:func:`_luhn_valid`, and :func:`_resolve_overlaps` from here; keep those signatures
stable.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from typing import NamedTuple

from aegis.core.types import PIIMatch

_LOG = logging.getLogger(__name__)

#: Human-readable identifier reported by ``pii.active_engine()`` when this engine is live.
ENGINE_NAME = "regex"


def _luhn_valid(candidate: str) -> bool:
    """Return ``True`` if ``candidate``'s digits pass the Luhn checksum.

    Used to suppress false positives: any 13–16 digit run *looks* like a card,
    but only Luhn-valid runs are treated as real card numbers.

    Args:
        candidate: The raw matched text (may contain spaces or dashes).

    Returns:
        Whether the stripped digit string is a valid Luhn sequence.
    """
    digits = [int(c) for c in candidate if c.isdigit()]
    if not 13 <= len(digits) <= 16:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


class _Detector(NamedTuple):
    """One PII pattern: a kind, its regex, its redaction token, and a validator."""

    kind: str
    placeholder: str
    pattern: re.Pattern[str]
    validator: Callable[[str], bool] | None


# Ordered by specificity. Overlap resolution prefers the longest span, but the
# ordering keeps ties deterministic and the table self-documenting.
_DETECTORS: tuple[_Detector, ...] = (
    _Detector(
        "EMAIL",
        "[REDACTED_EMAIL]",
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        None,
    ),
    _Detector(
        "SSN",
        "[REDACTED_SSN]",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
        None,
    ),
    _Detector(
        "CREDIT_CARD",
        "[REDACTED_CC]",
        re.compile(r"\b\d(?:[ -]?\d){12,15}\b"),
        _luhn_valid,
    ),
    _Detector(
        "AWS_ACCESS_KEY",
        "[REDACTED_AWS_KEY]",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}\b"),
        None,
    ),
    _Detector(
        "API_KEY",
        "[REDACTED_API_KEY]",
        re.compile(r"\b(?:sk|pk|rk|api)[-_][A-Za-z0-9]{16,}\b"),
        None,
    ),
    _Detector(
        "IP_ADDRESS",
        "[REDACTED_IP]",
        re.compile(
            r"\b(?:(?:25[0-5]|2[0-4]\d|1?\d?\d)\.){3}(?:25[0-5]|2[0-4]\d|1?\d?\d)\b"
        ),
        None,
    ),
    _Detector(
        "PHONE",
        "[REDACTED_PHONE]",
        re.compile(
            r"\b(?:\+?\d{1,3}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}\b"
        ),
        None,
    ),
)


def _resolve_overlaps(matches: list[PIIMatch]) -> list[PIIMatch]:
    """Drop overlapping matches, keeping the longest span at each position.

    Args:
        matches: All raw detector hits, in any order.

    Returns:
        A non-overlapping, start-sorted subset.
    """
    # Longest-first so the greedy pass keeps the widest span; then earliest-start.
    ordered = sorted(matches, key=lambda m: (m.start, -(m.end - m.start)))
    kept: list[PIIMatch] = []
    last_end = -1
    for match in ordered:
        if match.start >= last_end:
            kept.append(match)
            last_end = match.end
    return kept


#: The Presidio entity names this engine's fixed detector table actually covers, so a
#: caller asking for something else is *told* rather than quietly served less. The
#: fallback engine is allowed to detect fewer kinds than Presidio — that is what makes
#: it a fallback — but it is not allowed to accept an instruction it cannot carry out
#: and say nothing.
SUPPORTED_ENTITIES: frozenset[str] = frozenset(
    {
        "EMAIL_ADDRESS",
        "US_SSN",
        "CREDIT_CARD",
        "AWS_ACCESS_KEY",
        "API_KEY",
        "IP_ADDRESS",
        "PHONE_NUMBER",
    }
)

#: Entity names already warned about, so an unscreenable kind logs once per process
#: rather than once per inbound message.
_WARNED: set[str] = set()


def _warn_unsupported(entities: Sequence[str] | None) -> None:
    """Log, once per name, any requested entity this engine has no detector for."""
    for name in entities or ():
        if not isinstance(name, str) or not name.strip():
            continue
        normalised = name.strip().upper()
        if normalised in SUPPORTED_ENTITIES or normalised in _WARNED:
            continue
        _WARNED.add(normalised)
        _LOG.warning(
            "PII engine 'regex' has no detector for %s, so it is NOT being screened "
            "for. The setting is honoured by the Presidio engine; install "
            "'aegis[pii]' (and the spaCy model) for it to take effect here.",
            normalised,
        )


def scan(text: str, *, entities: Sequence[str] | None = None) -> list[PIIMatch]:
    """Return every (non-overlapping) PII span found in ``text``.

    Args:
        text: The text to scan.
        entities: Extra entity names the caller wants screened (a tenant's
            ``guardrails.pii.entities``). This engine's detector table is fixed and
            already covers every kind the catalogue's platform default names, so the
            argument can only ever *add* — and any name it has no detector for is
            logged once rather than silently dropped.

    Returns:
        Detected :class:`PIIMatch` spans, ordered by position. Empty when clean.
    """
    _warn_unsupported(entities)
    hits: list[PIIMatch] = []
    for detector in _DETECTORS:
        for match in detector.pattern.finditer(text):
            if detector.validator is not None and not detector.validator(match.group()):
                continue
            hits.append(
                PIIMatch(
                    kind=detector.kind,
                    start=match.start(),
                    end=match.end(),
                    placeholder=detector.placeholder,
                )
            )
    return _resolve_overlaps(hits)
