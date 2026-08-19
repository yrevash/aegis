"""A memory spec missing a required field, over a playbook that has been renamed."""

from __future__ import annotations

from pathlib import Path

from app.adapter.memory_spec import (  # noqa: F401
    FACT_TYPES,
    PROFILE_FIELDS,
    FactExtraction,
    FactSchema,
    memory_subject_for,
    render_profile,
    select_skills,
)

FACT_EXTRACTION_PROMPT = "Extract durable facts from the conversation."
SKILLS_DIR = str(Path(__file__).parent / "skills")
