"""PII detection and redaction — Microsoft Presidio backed, with a regex fallback.

This is the stable public facade the whole pipeline and its tests depend on. The
detection engine underneath is now **Microsoft Presidio** (``presidio-analyzer``), the
industry-standard open-source PII engine — a deliberate move away from homegrown
regexes toward a battle-tested library that recognises far more PII (people, IBANs,
``phonenumbers``-validated phones, and more) while keeping the exact same interface:

    * ``scan(text) -> list[PIIMatch]``     — ordered, non-overlapping spans.
    * ``redact(text) -> (masked, kinds)``  — ``[REDACTED_<KIND>]`` tokens; sorted kinds.
    * ``contains_pii(text) -> bool``.
    * ``_luhn_valid(candidate) -> bool``   — re-exported for the card-heuristic tests.

Engine selection is **lazy and self-healing**: the heavy Presidio/spaCy dependencies
are imported only on first use, and if they are unavailable (not installed, or the
spaCy model is missing) the module transparently falls back to the pure-code regex
engine in :mod:`._pii_regex` and logs which engine is live. It never crashes and never
silently stops redacting. Call :func:`active_engine` to see which engine is running.

The engine can be pinned via the ``AEGIS_PII_ENGINE`` environment variable
(``"presidio"`` or ``"regex"``); tests use this seam to exercise the fallback path.
"""

from __future__ import annotations

import logging
import os
from types import ModuleType

from aegis.core.types import PIIMatch

from . import _pii_regex
from ._pii_regex import _luhn_valid  # noqa: F401 - re-exported; exercised by PII tests

__all__ = ["PIIMatch", "active_engine", "contains_pii", "redact", "scan"]

_LOG = logging.getLogger(__name__)

#: Optional pin: "presidio" | "regex" (case-insensitive). Read lazily so tests can
#: monkeypatch ``os.environ`` / this attribute and reset the cache.
_ENGINE_ENV = "AEGIS_PII_ENGINE"

#: Cached selected engine module (either ``_pii_presidio`` or ``_pii_regex``).
_engine: ModuleType | None = None


def _select_engine() -> ModuleType:
    """Pick and cache the detection engine: Presidio when available, else regex.

    Honours the ``AEGIS_PII_ENGINE`` pin. Falls back to the regex engine on any
    Presidio import/model failure. Logs the active engine exactly once per selection.
    """
    pin = os.getenv(_ENGINE_ENV, "").strip().lower()

    if pin == "regex":
        _LOG.info("PII engine: regex (pinned via %s)", _ENGINE_ENV)
        return _pii_regex

    try:
        from . import _pii_presidio
    except Exception as exc:  # noqa: BLE001 - defensive; import should not fail on its own
        if pin == "presidio":
            raise
        _LOG.warning("PII engine: could not import Presidio adapter (%s); using regex", exc)
        return _pii_regex

    if _pii_presidio.is_available():
        _LOG.info("PII engine: presidio (spaCy-backed, industry-standard)")
        return _pii_presidio

    if pin == "presidio":
        raise RuntimeError(
            "AEGIS_PII_ENGINE=presidio but Presidio/spaCy are unavailable "
            "(install 'aegis[pii]' and the en_core_web_sm model)."
        )
    _LOG.warning("PII engine: presidio unavailable; falling back to regex")
    return _pii_regex


def _active() -> ModuleType:
    """Return the cached engine module, selecting it on first use."""
    global _engine
    if _engine is None:
        _engine = _select_engine()
    return _engine


def _reset_engine_cache() -> None:
    """Clear the cached engine selection (test seam; forces re-selection next call)."""
    global _engine
    _engine = None


def active_engine() -> str:
    """Return the name of the live detection engine: ``"presidio"`` or ``"regex"``."""
    return getattr(_active(), "ENGINE_NAME", "unknown")


def scan(text: str) -> list[PIIMatch]:
    """Return every (non-overlapping) PII span found in ``text``.

    Args:
        text: The text to scan.

    Returns:
        Detected :class:`PIIMatch` spans, ordered by position. Empty when clean.
    """
    return _active().scan(text)


def redact(text: str) -> tuple[str, list[str]]:
    """Replace every PII span in ``text`` with its redaction token.

    Args:
        text: The text to redact.

    Returns:
        A ``(redacted_text, kinds)`` tuple where ``kinds`` is the sorted list of
        unique detector names that fired (empty when nothing was redacted). Masks use
        the stable ``[REDACTED_<KIND>]`` form (e.g. ``[REDACTED_EMAIL]``).
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
