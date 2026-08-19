"""Personas — who the agent is and whom it serves.

This is **piece 5 of 10** of the adapter (paired with :mod:`app.adapter.prompts`, piece 6).
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

PERSONA_BY_ROLE: dict[Role, str] = {
    Role.ADMIN: OPERATIONS_LEAD.id,
    Role.AI_TEAM: OPERATIONS_LEAD.id,
    Role.DEVOPS: OPERATIONS_LEAD.id,
    Role.CLIENT: CLIENT.id,
}
"""Coarse RBAC role → the persona an authenticated principal of that role adopts.

**This table is domain knowledge, and it used to live in the core.**
``app/api/routes.py`` decided it with a hardcoded
``"client" if role is Role.CLIENT else "operations_lead"``, so re-voicing
:data:`PERSONAS` — step 5 of the retargeting procedure, which the same document
instructs an integrator to perform — made *every* authenticated request resolve to a
persona id that no longer existed, and ``get_persona`` raised ``KeyError`` on sign-in.
Nothing failed until a human logged in: the adapter tests, the agent tests, the
conformance suite and ruff all stayed green, because none of them go through the login
path. RBAC roles are the platform's; *which persona a role adopts* is the domain's, so
the mapping belongs here with the personas it names.

Every :class:`~aegis.governance.types.Role` must appear as a key — a role with no
persona cannot sign in — and every value must be a key of :data:`PERSONAS`. Both halves
are asserted by ``aegis.conformance``'s persona check.
"""


def persona_for_role(role: Role | str) -> str:
    """Return the persona id a principal holding ``role`` adopts.

    Args:
        role: The coarse RBAC role, as the enum or its string value.

    Returns:
        The persona id, always a key of :data:`PERSONAS`.

    Raises:
        KeyError: If ``role`` is not a known role, or the domain declares no persona
            for it. Deliberately loud: the alternative is a request that runs under
            some other persona's data scope and tool allowlist, which is a silent
            authorisation change.
    """
    key = role if isinstance(role, Role) else Role(role)
    persona_id = PERSONA_BY_ROLE.get(key)
    if persona_id is None:
        raise KeyError(
            f"No persona is declared for role {key.value!r}. Add it to "
            f"app.adapter.personas.PERSONA_BY_ROLE — every role the platform can "
            f"authenticate must map to one of {sorted(PERSONAS)}."
        )
    return persona_id


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
