"""A default persona id that is not a key of PERSONAS."""

from __future__ import annotations

from app.adapter.personas import PERSONAS, Persona, get_persona  # noqa: F401

DEFAULT_PERSONA_ID = "supervisor"
