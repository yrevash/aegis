"""A memory spec missing a required field, over a playbook the selector cannot name.

Two breaks, both silent. ``IMPORTANCE_HINTS`` is absent, which nothing checks at install
time — the member is read deep inside consolidation, which in a demo never runs. And the
keyword table names one of the two playbooks on disk, so the other can never be selected
and nothing says so.

Everything here is the fixture's own. It used to import ``select_skills``, ``FACT_TYPES``
and the fact models from the *production* adapter, which made the intended break a
property of the shipped domain rather than of this fixture: the shipped hints named
``de_escalation`` while this directory held ``closing_cases.md``, so the moment a real
retarget re-pointed those literals the break evaporated and the meta-test's ``12 failed,
1 passed`` quietly became ``11 failed, 2 passed``. A fixture that proves a suite can fail
must not depend on code the suite is meant to let you rewrite.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, Field

FACT_TYPES: list[str] = ["preference", "constraint", "commitment"]

PROFILE_FIELDS: list[str] = ["display_name", "region", "preferred_channel"]

FACT_EXTRACTION_PROMPT = "Extract durable facts from the conversation."

# THE BREAK: no IMPORTANCE_HINTS. Every other member is here.

SKILLS_DIR = str(Path(__file__).parent / "skills")

#: THE SECOND BREAK: 'handling_notes' sits in skills/ and is named nowhere, so it can
#: never be selected — and the selector filters its answer by `skill in available`, so
#: nothing raises and no line of the trace says a playbook was wanted and missed.
SKILL_HINTS: dict[str, str] = {
    "urgent": "urgent_path",
    "now": "urgent_path",
}


class FactSchema(BaseModel):
    """One durable fact the extractor emits."""

    fact_type: str = "preference"
    subject: str = ""
    predicate: str = ""
    object: str = ""
    text: str = ""
    confidence: float = 0.5
    importance: int = 5
    valid_at: datetime | None = None


class FactExtraction(BaseModel):
    """The extractor's container object."""

    facts: list[FactSchema] = Field(default_factory=list)


def memory_subject_for(user_id: str | None, persona_id: str | None) -> str:
    """Return the subject long-term memory is scoped to."""
    return f"user:{user_id}" if user_id else f"persona:{persona_id}"


def render_profile(profile: dict) -> str:
    """Render the structured profile as the always-injected human block."""
    if not profile:
        return ""
    lines = ["Known about this caller:"]
    lines += [f"- {k}: {v}" for k, v in profile.items() if v]
    return "\n".join(lines) if len(lines) > 1 else ""


def select_skills(query: str, persona_id: str | None, available: list[str]) -> list[str] | None:
    """Select procedural playbooks for a query by keyword.

    The table is a module constant rather than a local, deliberately: that is the shape
    the reachability check used to be blind to.
    """
    q = query.lower()
    chosen: list[str] = []
    for keyword, skill in SKILL_HINTS.items():
        if keyword in q and skill in available and skill not in chosen:
            chosen.append(skill)
    return chosen or None
