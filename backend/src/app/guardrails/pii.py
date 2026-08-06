"""Self-built PII detection and redaction (no local model, no network).

The inbound *and* outbound guardrails both need to find and mask personally
identifiable information, and ``docs/security.md`` is explicit that detection
must be **pure code or an API call — never a local model** (16 GB, no GPU). This
module is the pure-code half: a curated set of anchored regexes plus cheap
heuristics (a Luhn check on candidate card numbers) that run in microseconds.

Design notes:
    * Patterns are **anchored and specific** to keep the false-positive rate low
      — an over-eager PII filter that mangles ordinary prose is worse than none.
    * Overlapping matches are resolved by preferring the **longest** span at each
      position, so a 16-digit card is never mis-attributed to a phone rail.
    * Redaction replaces each span with a stable ``[REDACTED_<KIND>]`` token,
      which keeps the text readable for the model/user while removing the secret.

The pattern table is data, not code — extend it by appending a
:class:`_Detector` rather than touching the scan logic.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import NamedTuple

from app.guardrails.models import PIIMatch


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


def scan(text: str) -> list[PIIMatch]:
    """Return every (non-overlapping) PII span found in ``text``.

    Args:
        text: The text to scan.

    Returns:
        Detected :class:`PIIMatch` spans, ordered by position. Empty when clean.
    """
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


def redact(text: str) -> tuple[str, list[str]]:
    """Replace every PII span in ``text`` with its redaction token.

    Args:
        text: The text to redact.

    Returns:
        A ``(redacted_text, kinds)`` tuple where ``kinds`` is the sorted list of
        unique detector names that fired (empty when nothing was redacted).
    """
    matches = scan(text)
    if not matches:
        return text, []
    redacted = text
    # Replace right-to-left so earlier offsets stay valid as the string mutates.
    for match in sorted(matches, key=lambda m: m.start, reverse=True):
        redacted = redacted[: match.start] + match.placeholder + redacted[match.end :]
    kinds = sorted({match.kind for match in matches})
    return redacted, kinds


def contains_pii(text: str) -> bool:
    """Return ``True`` if ``text`` contains any detectable PII.

    Args:
        text: The text to check.

    Returns:
        Whether at least one detector matched.
    """
    return bool(scan(text))
