"""A default persona id that is not a key of PERSONAS."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class Persona:
    """The minimum a persona is: an id, a prompt key and a data scope."""

    id: str
    prompt_key: str
    data_scope: dict[str, Any] = field(default_factory=dict)


DESK_LEAD = Persona(id="desk_lead", prompt_key="desk_lead", data_scope={"kind": "all"})
REQUESTER = Persona(id="requester", prompt_key="requester", data_scope={"kind": "own"})

PERSONAS: dict[str, Persona] = {p.id: p for p in (DESK_LEAD, REQUESTER)}

DEFAULT_PERSONA_ID = "supervisor"
"""THE BREAK: a default that is not a key of PERSONAS, so every request naming no
persona resolves to a KeyError."""

PERSONA_BY_ROLE: dict[str, str] = {
    "admin": DESK_LEAD.id,
    "ai_team": DESK_LEAD.id,
    "devops": DESK_LEAD.id,
    "client": REQUESTER.id,
}


def persona_for_role(role: Any) -> str:
    """Return the persona id a principal holding ``role`` adopts."""
    return PERSONA_BY_ROLE[str(getattr(role, "value", role))]


def get_persona(persona_id: str | None) -> Persona:
    """Return the persona for ``persona_id`` (the default when ``None``)."""
    return PERSONAS[persona_id if persona_id is not None else DEFAULT_PERSONA_ID]
