"""Memory contract — **piece 7 of 10** of the adapter (the ONLY domain seam for memory).

The core memory subsystem (:mod:`app.memory`) is domain-agnostic: *how* to persist,
score, recall, budget and consolidate is core; *what counts as a durable fact*, *how to
extract it*, *who memory is scoped to*, and *how the profile reads* are domain meaning
and live here. On the hackathon day this file (+ ``skills/*.md``) is rewritten for the
new domain and nothing in ``app/memory/*`` changes — exactly like ``ml_spec.py`` makes
the ML spine domain-neutral.

Current domain: enterprise customer support. A "durable fact" is a stable attribute or
preference about a customer/user (tier, region, preferred channel, timezone, standing
constraints), distilled from conversation — NOT transient ticket detail.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

#: The typed kinds of durable fact this domain distils (guides the extractor).
FACT_TYPES: list[str] = [
    "preference",      # how the customer likes to be handled (channel, language)
    "entity_attr",     # a stable attribute (tier, region, timezone, product)
    "commitment",      # something the org promised the customer
    "constraint",      # a standing limitation (SLA, compliance, do-not-contact window)
]

#: Structured profile fields (the always-injected "human block"). Small + high-value.
PROFILE_FIELDS: list[str] = [
    "display_name",
    "tier",
    "region",
    "timezone",
    "preferred_channel",
    "preferred_language",
    "open_commitments",
    "notes",
]

#: Where procedural skills (how-to-act markdown) live for this domain.
SKILLS_DIR: str = str(Path(__file__).parent / "skills")

#: Domain guidance for the 1..10 importance (poignancy) rating during extraction.
IMPORTANCE_HINTS: str = (
    "Rate 1-3 for trivia, 4-6 for useful preferences/attributes, 7-8 for commitments "
    "or SLA/compliance constraints, 9-10 for safety/legal/do-not-contact constraints."
)


class FactSchema(BaseModel):
    """One durable fact the cheap-model extractor must emit (typed target).

    Attributes:
        fact_type: One of :data:`FACT_TYPES`.
        subject: Canonical entity the fact is about (e.g. ``"customer"`` / an id).
        predicate: The relation (e.g. ``"prefers_channel"``, ``"tier"``, ``"timezone"``).
        object: The value (e.g. ``"email"``, ``"enterprise"``, ``"Europe/Berlin"``).
        text: Natural-language rendering (what gets injected + embedded).
        confidence: Extractor confidence in [0, 1].
        importance: Poignancy 1..10 (see :data:`IMPORTANCE_HINTS`).
        valid_at: World-time the fact became true, if the turn states it (else null).
    """

    fact_type: str = Field(default="entity_attr")
    subject: str = Field(default="customer")
    predicate: str
    object: str
    text: str
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    importance: int = Field(default=5, ge=1, le=10)
    valid_at: datetime | None = Field(default=None)


class FactExtraction(BaseModel):
    """Container the extractor returns (a JSON object with a ``facts`` list)."""

    facts: list[FactSchema] = Field(default_factory=list)


FACT_EXTRACTION_PROMPT: str = (
    "You maintain the long-term memory of an enterprise support assistant. From the "
    "conversation turns, extract only DURABLE facts about the customer/user that will "
    "still matter in future conversations — stable preferences, attributes, commitments "
    "made to them, and standing constraints. Do NOT extract transient ticket details, "
    "one-off questions, or the assistant's own statements. Merge duplicates. For each "
    "fact give a short predicate and object, a one-sentence natural-language `text`, a "
    "`confidence` in [0,1], and an `importance` 1-10. " + IMPORTANCE_HINTS + " Return "
    'JSON of the form {"facts": [{"fact_type":..., "subject":..., "predicate":..., '
    '"object":..., "text":..., "confidence":..., "importance":...}, ...]}. Return an '
    'empty list if nothing durable was said.'
)


def memory_subject_for(user_id: str | int | None, persona_id: str | None = None) -> str | None:
    """Resolve the memory subject a run is scoped to (the app-level isolation key).

    **This function is THE adapter seam for memory scoping, and it is load-bearing
    twice.** It decides the app-level isolation key that every memory query filters on —
    ``WHERE subject_id = …`` is the primary, NULL-safe isolator for the whole subsystem,
    with tenant RLS underneath it as the belt — and it decides *what a memory is about*,
    which is a domain statement, not an infrastructure one.

    Defaults to the authenticated user (``"user:<id>"``). A domain that scopes memory to
    a **business entity** rather than a person — the account, the case, the vehicle, the
    customer a persona is acting on behalf of — changes this one function and nothing
    else, which is exactly why nothing outside it may compose the key itself.

    Every consumer therefore goes through it rather than around it:

    * ``POST /query`` (:mod:`app.api.routes`) resolves the subject a run recalls and
      persists under;
    * the read surfaces (``/memory/facts``, ``/profile``, ``/sessions``, ``/writes``)
      authorise "is this the caller's own subject?" against its output;
    * the control plane (:mod:`app.api.routes_memory`) builds the whole set of subjects a
      principal may write, correct or erase by mapping it over the users in that
      principal's sealed tenant scope — so a domain change reshapes the picker, the
      write path and every scope check together;
    * the browser never composes it at all. ``GET /memory/subjects`` hands the console
      opaque strings, and the console echoes one back. (``components/console/
      memorySubject.ts`` is the one exception — the rail derives its own key from the
      login response's ``user_id`` — and it says in its own docstring that it must change
      when this does.)

    Returns ``None`` when there is no subject (anonymous / single-shot) → memory stays
    inert, which is a real answer rather than a failure.

    Args:
        user_id: The authenticated user's id (or ``None``).
        persona_id: The active persona (unused here; available for entity-scoped domains).

    Returns:
        A stable subject key, or ``None`` to disable memory for this run.
    """
    if user_id is None or user_id == "":
        return None
    return f"user:{user_id}"


def render_profile(profile: dict[str, Any]) -> str:
    """Render the structured profile JSON as a compact prompt "human block".

    Args:
        profile: The stored ``MemoryProfile.data`` (a subset of :data:`PROFILE_FIELDS`).

    Returns:
        A short multi-line block, or an empty string when the profile is empty.
    """
    if not profile:
        return ""
    lines = ["Known about this customer/user:"]
    for field in PROFILE_FIELDS:
        value = profile.get(field)
        if value in (None, "", [], {}):
            continue
        label = field.replace("_", " ")
        rendered = ", ".join(map(str, value)) if isinstance(value, list) else str(value)
        lines.append(f"- {label}: {rendered}")
    return "\n".join(lines) if len(lines) > 1 else ""


def select_skills(query: str, persona_id: str | None, available: list[str]) -> list[str] | None:
    """Deterministically select procedural skills for a query (adapter override).

    Returns a subset of ``available`` skill names (filenames without ``.md``) by simple
    keyword match — cheap, no infra, and reshapeable on the day. Returning ``None`` (or an
    empty list) means "no skill selected for this turn" — the core injects none. (A
    vector-similarity fallback over skill *descriptions* is a possible future enhancement;
    the core does not do it today, so the adapter is the sole skill selector.)

    Args:
        query: The user query.
        persona_id: The active persona (for allowlisting, if a domain needs it).
        available: Skill names discovered under :data:`SKILLS_DIR`.

    Returns:
        The selected skill names, or ``None`` to defer to core vector selection.
    """
    q = query.lower()
    hints = {
        "close": "closing_requests",
        "resolve": "closing_requests",
        "duplicate": "closing_requests",
        "angry": "de_escalation",
        "frustrated": "de_escalation",
        "escalate": "de_escalation",
        "complaint": "de_escalation",
    }
    chosen: list[str] = []
    for keyword, skill in hints.items():
        if keyword in q and skill in available and skill not in chosen:
            chosen.append(skill)
    return chosen or None


__all__ = [
    "FACT_EXTRACTION_PROMPT",
    "FACT_TYPES",
    "IMPORTANCE_HINTS",
    "PROFILE_FIELDS",
    "SKILLS_DIR",
    "FactExtraction",
    "FactSchema",
    "memory_subject_for",
    "render_profile",
    "select_skills",
]
