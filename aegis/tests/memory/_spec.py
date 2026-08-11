"""A minimal fake MemorySpec for the memory suite (mirrors a real adapter's shape).

Structurally satisfies :class:`aegis.memory.spec.MemorySpec`: it carries the extraction
prompt, profile fields, skills dir, fact schema/extraction pydantic models, and the
``render_profile`` / ``select_skills`` domain hooks — exactly the surface recall and
consolidate read. Offline and domain-neutral (a support-desk flavour, like the platform's
own adapter), so the ported tests exercise the real injection seam.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

FACT_TYPES: list[str] = ["preference", "entity_attr", "commitment", "constraint"]

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

SKILLS_DIR: str = str(Path(__file__).parent / "skills")

IMPORTANCE_HINTS: str = (
    "Rate 1-3 for trivia, 4-6 for useful preferences/attributes, 7-8 for commitments, "
    "9-10 for safety/legal constraints."
)


class FactSchema(BaseModel):
    """One durable fact the extractor emits (the injected typed target)."""

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
    "You maintain the long-term memory of a support assistant. Extract only DURABLE "
    "facts about the customer/user. Return JSON of the form {\"facts\": [...]}."
)


class FakeMemorySpec:
    """An instance-based fake spec (satisfies the MemorySpec Protocol structurally)."""

    FACT_TYPES = FACT_TYPES
    PROFILE_FIELDS = PROFILE_FIELDS
    SKILLS_DIR = SKILLS_DIR
    IMPORTANCE_HINTS = IMPORTANCE_HINTS
    FACT_EXTRACTION_PROMPT = FACT_EXTRACTION_PROMPT
    FactSchema = FactSchema
    FactExtraction = FactExtraction

    def render_profile(self, profile: dict[str, Any]) -> str:
        """Render the structured profile JSON as a compact human block (or "")."""
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

    def select_skills(
        self, query: str, persona: str | None, available: list[str]
    ) -> list[str] | None:
        """Keyword-match procedural skills for a query (subset of ``available``), or None."""
        q = query.lower()
        hints = {
            "refund": "handling_refunds",
            "billing": "handling_refunds",
            "charge": "handling_refunds",
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


FAKE_SPEC = FakeMemorySpec()
