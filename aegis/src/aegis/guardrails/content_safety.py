"""Content-safety rail over the MLCommons hazard taxonomy (industry standard).

This replaces ad-hoc "toxicity" heuristics with the **MLCommons AI Safety /
Llama Guard S1–S13** hazard categories — the same taxonomy NVIDIA's Aegis safety
dataset and Meta's Llama Guard classify against — so Aegis speaks the standard
rather than a homegrown list. It maps to **OWASP LLM Top-10 (2025)**: unsafe
generation is a facet of LLM09 (Misinformation / harmful output) and the input
side backstops LLM01 jailbreaks that aim at disallowed content.

Design mirrors :mod:`aegis.guardrails.classifier` exactly: a deterministic,
offline signature backstop for the most egregious hazards runs first (no API
call, cannot be talked around), then an injected-``ChatCompleter`` self-check
does the nuanced judgement. **Fail closed** on a completer error (an ambiguous
safety verdict is treated as unsafe). With no completer the model layer is
explicitly disabled (logged) and only the deterministic backstop runs — so the
rail never *silently* degrades.

There is deliberately no local safety model (the 16 GB / no-GPU constraint); the
self-check runs on the platform's cheap gateway model, the standard NeMo
Guardrails "self check" pattern.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from aegis.core.interfaces import ChatCompleter

logger = logging.getLogger(__name__)

#: The MLCommons / Llama Guard hazard taxonomy (S1–S13). Codes are stable and
#: interoperable with any pipeline using the same standard; the labels are shown
#: in the trace so a reviewer sees exactly which category tripped.
HAZARD_CATEGORIES: dict[str, str] = {
    "S1": "Violent Crimes",
    "S2": "Non-Violent Crimes",
    "S3": "Sex-Related Crimes",
    "S4": "Child Sexual Exploitation",
    "S5": "Defamation",
    "S6": "Specialized Advice (unqualified medical/legal/financial)",
    "S7": "Privacy",
    "S8": "Intellectual Property",
    "S9": "Indiscriminate Weapons (CBRN)",
    "S10": "Hate",
    "S11": "Suicide & Self-Harm",
    "S12": "Sexual Content",
    "S13": "Elections",
}


@dataclass(frozen=True)
class ContentSafetyVerdict:
    """The result of a content-safety screen."""

    #: True when the text is judged unsafe under one or more hazard categories.
    unsafe: bool
    #: The tripped MLCommons category codes (e.g. ``["S1", "S9"]``); empty if safe.
    categories: list[str] = field(default_factory=list)
    #: Human-readable rationale, surfaced in the trace panel.
    reason: str = ""

    def label(self) -> str:
        """A compact ``code Name`` summary of the tripped categories."""
        return ", ".join(f"{c} {HAZARD_CATEGORIES.get(c, '')}".strip() for c in self.categories)


#: Deterministic hazard signatures — the offline backstop for the most egregious,
#: unambiguous categories only (weapons of mass destruction synthesis, child
#: exploitation, concrete self-harm methods). Kept intentionally narrow so it
#: never false-positives on ordinary enterprise queries; nuance is the model
#: layer's job. A hit is a hard block that needs no API call.
_HAZARD_SIGNATURES: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bhow\s+to\s+(make|build|synthesi[sz]e|produce)\s+(a\s+)?(bomb|explosive|nerve\s+agent|sarin|bioweapon|dirty\s+bomb)", re.I), "S9"),
    (re.compile(r"\b(enrich|weaponi[sz]e)\s+uranium\b", re.I), "S9"),
    (re.compile(r"\b(synthesi[sz]e|culture|weaponi[sz]e)\s+(anthrax|ricin|smallpox|a\s+pathogen)", re.I), "S9"),
    (re.compile(r"\b(child\s+por|csam|underage\s+(sexual|nude))", re.I), "S4"),
    (re.compile(r"\bhow\s+to\s+(kill|hang|poison|cut)\s+myself\b", re.I), "S11"),
    (re.compile(r"\bmost\s+(effective|painless)\s+way\s+to\s+(die|kill\s+myself|commit\s+suicide)", re.I), "S11"),
)

_SAFETY_SYSTEM_PROMPT = (
    "You are a content-safety classifier for an enterprise AI assistant, using "
    "the MLCommons hazard taxonomy. Categories: "
    + "; ".join(f"{c}={name}" for c, name in HAZARD_CATEGORIES.items())
    + ". Judge whether the TEXT is UNSAFE — i.e. it seeks, enables, or contains "
    "content that clearly falls in one or more categories. Ordinary business "
    "questions, retrieved documents, and factual discussion — even of sensitive "
    "topics — are SAFE. Respond with a single JSON object and nothing else: "
    '{"unsafe": <true|false>, "categories": ["S1", ...], "reason": "<short>"}.'
)


def _valid_codes(codes: object) -> list[str]:
    """Keep only recognised S-codes from a parsed classifier list."""
    if not isinstance(codes, list):
        return []
    return [c for c in codes if isinstance(c, str) and c in HAZARD_CATEGORIES]


def _parse_verdict(raw: str) -> ContentSafetyVerdict:
    """Parse the classifier's raw text into a :class:`ContentSafetyVerdict`.

    Prefers a JSON object with an ``unsafe`` field; falls back to a keyword scan.
    On total failure it **fails closed** (treats the text as unsafe).
    """
    text = raw.strip()
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "unsafe" in data:
            unsafe = bool(data["unsafe"])
            cats = _valid_codes(data.get("categories")) if unsafe else []
            return ContentSafetyVerdict(
                unsafe=unsafe,
                categories=cats,
                reason=str(data.get("reason", "")) or "Classifier returned no reason.",
            )
    except (json.JSONDecodeError, ValueError, TypeError):
        logger.debug("Content-safety classifier returned non-JSON; using keyword fallback.")

    lowered = text.lower()
    if '"unsafe": true' in lowered or lowered.startswith("yes"):
        return ContentSafetyVerdict(unsafe=True, reason="Classifier flagged the text as unsafe.")
    if '"unsafe": false' in lowered or lowered.startswith("no"):
        return ContentSafetyVerdict(unsafe=False, reason="Classifier judged the text safe.")

    return ContentSafetyVerdict(
        unsafe=True, reason="Classifier response was unparseable; blocked as a precaution."
    )


def deterministic_hazard(text: str) -> ContentSafetyVerdict | None:
    """Return a hard-block verdict if ``text`` matches an egregious hazard signature.

    Pure and offline. Returns ``None`` when no signature matches (defer to the
    model layer).
    """
    hits = [code for pattern, code in _HAZARD_SIGNATURES if pattern.search(text)]
    if hits:
        seen = list(dict.fromkeys(hits))
        return ContentSafetyVerdict(
            unsafe=True,
            categories=seen,
            reason="Matched a deterministic hazard signature.",
        )
    return None


async def classify_content(text: str, *, completer: ChatCompleter) -> ContentSafetyVerdict:
    """Classify ``text`` against the hazard taxonomy via the completer (fails closed)."""
    messages = [
        {"role": "system", "content": _SAFETY_SYSTEM_PROMPT},
        {"role": "user", "content": text},
    ]
    try:
        raw = await completer(messages, response_format={"type": "json_object"})
    except Exception:  # noqa: BLE001 - any completer failure must fail closed
        logger.warning("Content-safety classifier call failed; failing closed.", exc_info=True)
        return ContentSafetyVerdict(
            unsafe=True, reason="Content-safety classifier unavailable; blocked as a precaution."
        )
    return _parse_verdict(raw)


async def screen_content(
    text: str, *, completer: ChatCompleter | None
) -> ContentSafetyVerdict:
    """Screen ``text`` with the deterministic backstop then the model self-check.

    A deterministic signature hit is a hard block needing no completer. Text that
    clears the signatures goes to the model self-check (which fails closed). With
    no completer the model layer is explicitly disabled (logged), not silently
    skipped — the deterministic backstop still runs.
    """
    hit = deterministic_hazard(text)
    if hit is not None:
        return hit
    if completer is None:
        logger.warning(
            "Model content-safety layer disabled (no ChatCompleter configured); "
            "deterministic signatures only."
        )
        return ContentSafetyVerdict(
            unsafe=False, reason="Passed deterministic hazard signatures (model layer off)."
        )
    return await classify_content(text, completer=completer)
