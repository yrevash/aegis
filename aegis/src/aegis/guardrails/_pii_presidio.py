"""Microsoft Presidio PII engine — the industry-standard backend for ``pii``.

Presidio (``presidio-analyzer``) is the SOTA, widely-deployed open-source PII engine.
It recognises far more entity types than the legacy regex table (people, IBANs, and
more) and ships battle-tested detectors — a Luhn-validating credit-card recognizer, a
``phonenumbers``-backed phone recognizer, and a spaCy NER model for names.

This module adapts Presidio to the **exact existing contract** of
:mod:`aegis.guardrails.pii`:

* A curated :class:`RecognizerRegistry` — an explicit *allowlist* of entity types —
  keeps the low-false-positive philosophy of the old engine (no ``DATE_TIME`` on the
  word "quarterly", no ``URL`` inside an email, no ``ORGANIZATION`` on "SSN").
* Presidio entity types are mapped back to the legacy *kind* names
  (``EMAIL_ADDRESS`` → ``EMAIL``, ``PHONE_NUMBER`` → ``PHONE``, …) and each kind keeps
  its historical ``[REDACTED_<X>]`` placeholder.
* The analyzer is expensive to construct (it loads a spaCy model), so it is built
  **once**, lazily, as a module-level singleton — never per call.

All heavy imports (``presidio_analyzer``, ``spacy``) happen inside the builder, so
importing this module — or :mod:`aegis.guardrails.pii` — stays cheap. Use
:func:`is_available` (a ``require``-style guard) before relying on the engine; the
public facade falls back to the regex engine when it returns ``False``.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from aegis.core.types import PIIMatch

from ._pii_regex import _resolve_overlaps

if TYPE_CHECKING:
    from presidio_analyzer import AnalyzerEngine

_LOG = logging.getLogger(__name__)

#: Human-readable identifier reported by ``pii.active_engine()`` when this engine is live.
ENGINE_NAME = "presidio"

#: spaCy model Presidio uses for NER. Small, CPU-only (~12 MB). Overridable for hosts
#: that ship the larger, more accurate models.
_SPACY_MODEL = os.getenv("AEGIS_SPACY_MODEL", "en_core_web_sm")

#: Presidio entity type -> legacy Aegis "kind" name. This is the compatibility contract:
#: the kinds the historical tests assert must come out of Presidio identically.
_ENTITY_TO_KIND: dict[str, str] = {
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "CREDIT_CARD": "CREDIT_CARD",
    "US_SSN": "SSN",
    "IP_ADDRESS": "IP_ADDRESS",
    "IBAN_CODE": "IBAN",
    "PERSON": "PERSON",
    "AWS_ACCESS_KEY": "AWS_ACCESS_KEY",
    "API_KEY": "API_KEY",
}

#: The curated allowlist handed to ``analyze(entities=...)`` — the **floor**. Anything
#: not here and not asked for by a caller is dropped even if a recognizer fires, keeping
#: false positives low. A caller may *add* to it (see :func:`scan`'s ``entities``, which
#: carries a tenant's ``guardrails.pii.entities``); it can never subtract from it,
#: because the two are unioned rather than assigned.
_ENTITIES: list[str] = list(_ENTITY_TO_KIND)

#: Kind -> redaction placeholder. Preserves the exact legacy tokens (note ``CC`` for
#: credit cards); new kinds get a ``[REDACTED_<KIND>]`` token via :func:`_placeholder`.
_KIND_TO_PLACEHOLDER: dict[str, str] = {
    "EMAIL": "[REDACTED_EMAIL]",
    "PHONE": "[REDACTED_PHONE]",
    "CREDIT_CARD": "[REDACTED_CC]",
    "SSN": "[REDACTED_SSN]",
    "IP_ADDRESS": "[REDACTED_IP]",
    "IBAN": "[REDACTED_IBAN]",
    "PERSON": "[REDACTED_PERSON]",
    "AWS_ACCESS_KEY": "[REDACTED_AWS_KEY]",
    "API_KEY": "[REDACTED_API_KEY]",
}

#: Presidio drops results scoring below this. 0.4 keeps ``phonenumbers``-"possible"
#: matches (the legacy engine caught these) while the entity allowlist screens noise.
_SCORE_THRESHOLD = 0.4

_analyzer: AnalyzerEngine | None = None
_lock = threading.Lock()
_available: bool | None = None


def _placeholder(kind: str) -> str:
    """Return the redaction token for ``kind`` (legacy token, or a generated default)."""
    return _KIND_TO_PLACEHOLDER.get(kind, f"[REDACTED_{kind}]")


def effective_entities(entities: Sequence[str] | None) -> list[str]:
    """Return the curated allowlist unioned with ``entities`` — never a subset of it.

    The single expression behind "the platform's set is a floor, not a starting point".
    A tenant's ``guardrails.pii.entities`` is UNION-merged by the resolver and unioned
    again here against what this engine already screens, so there is no value a tenant
    can write that makes the rail detect **less** than it does today.

    Args:
        entities: Extra entity names, or ``None``.

    Returns:
        The entity list to hand ``AnalyzerEngine.analyze``, curated names first and
        additions in the order they were asked for, de-duplicated.
    """
    effective = dict.fromkeys(_ENTITIES)
    for name in entities or ():
        if isinstance(name, str) and name.strip():
            effective.setdefault(name.strip().upper(), None)
    return list(effective)


def _build_analyzer() -> AnalyzerEngine:
    """Construct the curated Presidio analyzer. Heavy; call once (guarded by the lock).

    Returns:
        A configured :class:`~presidio_analyzer.AnalyzerEngine` restricted to the
        curated recognizer registry and the ``en`` spaCy model.
    """
    from presidio_analyzer import (
        AnalyzerEngine,
        Pattern,
        PatternRecognizer,
        RecognizerRegistry,
    )
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import (
        CreditCardRecognizer,
        EmailRecognizer,
        IbanRecognizer,
        IpRecognizer,
        PhoneRecognizer,
        SpacyRecognizer,
        UsSsnRecognizer,
    )

    nlp_engine = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": _SPACY_MODEL}],
        }
    ).create_engine()

    registry = RecognizerRegistry()
    for recognizer in (
        EmailRecognizer(),
        CreditCardRecognizer(),  # Luhn-validated, like the legacy engine
        UsSsnRecognizer(),
        PhoneRecognizer(),
        IpRecognizer(),
        IbanRecognizer(),
        SpacyRecognizer(),  # PERSON (LOCATION/DATE_TIME/ORG filtered out by allowlist)
    ):
        registry.add_recognizer(recognizer)

    # Custom recognizers that mirror the legacy regex table. Presidio has no built-in
    # AWS/API-key recognizers, and its ``UsSsnRecognizer`` deliberately rejects the
    # canonical "123-45-6789" placeholder — this lenient SSN pattern preserves the old
    # contract (any ``NNN-NN-NNNN`` is redacted) without weakening real detection.
    _aws_pattern = Pattern("aws_access_key", r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[A-Z0-9]{16}\b", 0.9)
    registry.add_recognizer(
        PatternRecognizer(supported_entity="AWS_ACCESS_KEY", patterns=[_aws_pattern])
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="API_KEY",
            patterns=[Pattern("api_key", r"\b(?:sk|pk|rk|api)[-_][A-Za-z0-9]{16,}\b", 0.9)],
        )
    )
    registry.add_recognizer(
        PatternRecognizer(
            supported_entity="US_SSN",
            patterns=[Pattern("ssn_dashed", r"\b\d{3}-\d{2}-\d{4}\b", 0.85)],
        )
    )

    return AnalyzerEngine(
        nlp_engine=nlp_engine, registry=registry, supported_languages=["en"]
    )


def is_available() -> bool:
    """Return whether the Presidio engine can be built (imports + spaCy model present).

    Result is cached: the (expensive) construction runs at most once. On any failure
    — Presidio not installed, spaCy model missing — returns ``False`` and the facade
    falls back to the regex engine. Never raises.
    """
    global _available
    if _available is not None:
        return _available
    with _lock:
        if _available is not None:
            return _available
        try:
            _get_analyzer_locked()
            _available = True
        except Exception as exc:  # noqa: BLE001 - any import/model failure => fall back
            _LOG.warning("Presidio PII engine unavailable, falling back to regex: %s", exc)
            _available = False
    return _available


def _get_analyzer_locked() -> AnalyzerEngine:
    """Return the singleton analyzer, building it on first use. Caller may hold the lock."""
    global _analyzer
    if _analyzer is None:
        _analyzer = _build_analyzer()
    return _analyzer


def _analyzer_singleton() -> AnalyzerEngine:
    """Return the lazily-built analyzer singleton (thread-safe)."""
    if _analyzer is not None:
        return _analyzer
    with _lock:
        return _get_analyzer_locked()


def scan(text: str, *, entities: Sequence[str] | None = None) -> list[PIIMatch]:
    """Return every (non-overlapping) PII span Presidio finds in ``text``.

    Presidio results are mapped to legacy kinds, filtered to the effective allowlist,
    and passed through the shared longest-span overlap resolver so the output matches
    the regex engine's shape exactly: ordered, non-overlapping :class:`PIIMatch` spans.

    Args:
        text: The text to scan.
        entities: Extra Presidio entity types to screen for **as well as**
            :data:`_ENTITIES`. This is where a tenant's ``guardrails.pii.entities``
            arrives. It is unioned, never assigned: a caller naming a subset of the
            curated allowlist (which is exactly what the catalogue's platform default
            is) cannot switch the rest of it off. An entity with no registered
            recognizer simply never fires — Presidio ignores it — so an unknown name is
            inert rather than an error. Entities outside :data:`_ENTITY_TO_KIND` keep
            their own name as the kind and get a generated ``[REDACTED_<NAME>]`` token.

    Returns:
        Detected :class:`PIIMatch` spans, ordered by position. Empty when clean.
    """
    if not text:
        return []
    results: list[Any] = _analyzer_singleton().analyze(
        text=text,
        language="en",
        entities=effective_entities(entities),
        score_threshold=_SCORE_THRESHOLD,
    )
    hits: list[PIIMatch] = []
    for res in results:
        kind = _ENTITY_TO_KIND.get(res.entity_type, res.entity_type)
        hits.append(
            PIIMatch(
                kind=kind,
                start=res.start,
                end=res.end,
                placeholder=_placeholder(kind),
            )
        )
    return _resolve_overlaps(hits)
