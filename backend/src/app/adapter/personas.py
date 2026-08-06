"""Personas — who the agent is and whom it serves.

This is **piece 5a of 5** of the adapter (paired with :mod:`app.adapter.prompts`).
It defines the two personas the platform ships with for the example domain and,
crucially, **the scope each one carries**: which data it may see and which tools
it may call. The core routes a request to a persona (via ``QueryRequest.persona``)
and uses that persona to filter retrieval and gate actions.

The two personas:

* ``operations_lead`` — an **admin** support operations lead. Sees the whole
  desk; may run every action tool.
* ``client`` — an **end-user / customer**. Sees only their own requests; may only
  append notes to them.

Tool scope is not duplicated here — it is read straight from
:data:`app.adapter.tools.ALLOWLIST`, keeping a single source of truth. Data scope
is expressed as a small, typed :class:`DataScope` the retrieval/data layers apply.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from app.adapter.tools import ALLOWLIST
from app.api.schemas import Role


class ScopeKind(StrEnum):
    """How broadly a persona may see domain records."""

    ALL = "all"        # every record on the desk (admin)
    OWN = "own"        # only records belonging to a subject (a customer)


class DataScope(BaseModel):
    """Declarative data visibility for a persona.

    The data/retrieval layers translate this into a filter. ``OWN`` scope is
    bound at request time to the authenticated subject id; :attr:`subject_field`
    names the record field that must equal that subject.
    """

    kind: ScopeKind
    subject_field: str | None = Field(
        default=None,
        description="Record field constrained for OWN scope, e.g. 'customer_id'.",
    )


class Persona(BaseModel):
    """A concrete persona the agent can adopt.

    Attributes:
        id: Stable persona id used in ``QueryRequest.persona`` and the allowlist.
        role: Coarse RBAC role (admin/user) from the locked API schema.
        display_name: Human label for the UI.
        description: One-line summary of who this persona is.
        data_scope: What data this persona may see.
        prompt_key: Key into :data:`app.adapter.prompts.SYSTEM_PROMPTS`.
    """

    id: str
    role: Role
    display_name: str
    description: str
    data_scope: DataScope
    prompt_key: str

    @property
    def tool_names(self) -> frozenset[str]:
        """Return the tool names this persona may call (from the allowlist)."""
        return ALLOWLIST.get(self.id, frozenset())


OPERATIONS_LEAD = Persona(
    id="operations_lead",
    role=Role.ADMIN,
    display_name="Operations Lead",
    description="Support operations lead who triages, assigns and resolves cases.",
    data_scope=DataScope(kind=ScopeKind.ALL),
    prompt_key="operations_lead",
)

CLIENT = Persona(
    id="client",
    role=Role.CLIENT,
    display_name="Customer",
    description="End customer tracking and annotating their own service requests.",
    data_scope=DataScope(kind=ScopeKind.OWN, subject_field="customer_id"),
    prompt_key="client",
)


PERSONAS: dict[str, Persona] = {p.id: p for p in (OPERATIONS_LEAD, CLIENT)}
"""Persona id → :class:`Persona` for every persona the domain exposes."""

DEFAULT_PERSONA_ID: str = OPERATIONS_LEAD.id
"""Persona used when a request does not specify one."""


def get_persona(persona_id: str | None) -> Persona:
    """Return the persona for ``persona_id`` (or the default if ``None``).

    Args:
        persona_id: The requested persona id, or ``None`` for the default.

    Returns:
        The matching :class:`Persona`.

    Raises:
        KeyError: If ``persona_id`` is given but unknown.
    """
    if persona_id is None:
        return PERSONAS[DEFAULT_PERSONA_ID]
    return PERSONAS[persona_id]
