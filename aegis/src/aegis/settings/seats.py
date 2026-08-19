"""Named seats — the §7.8 answer to *"let a tenant admin make sub-roles"*.

The literal request was a ``tenant_roles(tenant_id, key, label, permissions jsonb)``
table, a closed permission catalogue, a ``require_permission`` dependency layered under
the eight role guards, and — because it needs a foreign key **and** a NOT NULL defaulted
column on an existing table, which
:func:`aegis.governance.schema.reconcile_additive_columns` refuses by design — Alembic.
Seven days whose demo sentence is *"I made a role called Analyst."* §7.8 cuts it and
ships the same sentence out of mechanisms that already exist.

**A seat is a named grant, not a role object.** ``seat.label`` gives it a name; the
``seat.can_*`` toggles say what it may do; ``settings.updated_by`` / ``updated_at``
already record who granted it and when. That is the operator's actual question —
*"who can do what, and who gave it to them"* — answered without a new table, a new
audit path or a new authorisation mechanism.

**Every toggle can only ever take capability away, and that is arithmetic, not
discipline.** Each key is :attr:`~aegis.settings.spec.MergeRule.TIGHTEN_ONLY` with
``default=True`` and ``stricter=Strictness.LOWER``, so:

* the platform layer is ``True`` — the coarse role guard alone decides, exactly as
  before seats existed;
* a tenant-scope ``False`` revokes the capability for everyone in that tenant;
* a user-scope row is folded with :func:`aegis.settings.spec.strictest`, so a
  user-scope ``True`` **cannot** re-admit what the tenant revoked, and no scope can
  ever resolve above the platform's ``True``.

The consequence is the property §7.16 row 15 asks for, and it is structural: there is
no value any tenant-scoped writer can put in any of these rows that makes
:func:`seat_allows` return ``True`` where the coarse guard already said no. A seat is
read **after** the role guard has admitted the request, so the composition is
``coarse_role_permits AND seat_allows`` — an ``AND`` has no branch that adds a
capability. A tenant admin cannot grant platform authority with a seat for the same
reason they cannot grant it with a smaller number: nothing here is an addition.

**The honest limitation, stated so nobody oversells it** (§7.8's own words): these are
per-user flags, not a role object assignable to many users. Twelve identical Analysts
means setting the toggles twelve times. That is a real ergonomic shortcoming and it is
worth the six-and-a-quarter days it saves.

Why the closed set is declared here rather than inferred from the key prefix: a seat
key that nothing enforces is a control that saves, audits and changes nothing — the
exact defect ``inert_reason`` and
``aegis/tests/settings/test_forbidden_controls.py::test_a_key_that_claims_to_be_in_force_is_named_by_live_code_somewhere``
exist to catch. :data:`SEAT_CAPABILITIES` names, for each toggle, the guard that reads
it, so the catalogue entry and its enforcement site are declared together and a test
can walk the pairs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from aegis.settings.resolver import resolve
from aegis.settings.spec import spec_for

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

__all__ = [
    "SEAT_CAPABILITIES",
    "SEAT_KEYS",
    "SEAT_LABEL_KEY",
    "Seat",
    "SeatCapability",
    "seat_allows",
    "seat_of",
]

#: The seat's name. Descriptive only — it grants nothing and revokes nothing, which is
#: why it is an ``OVERRIDE`` string rather than a ``TIGHTEN_ONLY`` toggle. It is what
#: makes the grant *named*: "Support Lead" on the row a reviewer is reading is the
#: difference between a permission audit and a list of booleans.
SEAT_LABEL_KEY = "seat.label"


@dataclass(frozen=True, slots=True)
class SeatCapability:
    """One seat toggle, declared beside the guard that reads it.

    Attributes:
        key: The catalogue key.
        title: The short human name a screen renders.
        gates: Where the narrowing check lives, named as prose. Not a callable: the
            enforcement is in the host's HTTP layer, which this package must not import
            (``aegis`` is host-free). Naming it here is what stops a toggle being added
            with no reader — the pairing a test can walk and a reviewer can check.
    """

    key: str
    title: str
    gates: str


#: **The closed set.** Adding a seventh capability means adding a catalogue entry, a
#: narrowing check at a real guard, and a line here — in one change, because a toggle
#: without the other two is the control that lies about being in force.
SEAT_CAPABILITIES: tuple[SeatCapability, ...] = (
    SeatCapability(
        key="seat.can_upload_documents",
        title="Upload documents",
        gates="POST /jobs/ingest and POST /jobs/{job_id}/requeue in app.api.routes",
    ),
    SeatCapability(
        key="seat.can_edit_memory",
        title="Edit memory",
        gates="POST /memory/forget and DELETE /memory/facts/{id} in app.api.routes",
    ),
    SeatCapability(
        key="seat.can_approve",
        title="Approve gated actions",
        gates="POST /approval and POST /approvals/{id}/decision in app.api.routes",
    ),
    SeatCapability(
        key="seat.can_view_tenant_audit",
        title="View the tenant audit trail",
        gates="GET /audit in app.api.routes",
    ),
    SeatCapability(
        key="seat.can_change_agent_mode",
        title="Change agent behaviour",
        gates="PUT /settings/{key} in app.api.routes_console, for every agent.* key",
    ),
)

#: Every key a seat is made of, label included — what a screen fetches and what the
#: audit payload for a grant names.
SEAT_KEYS: tuple[str, ...] = (SEAT_LABEL_KEY, *(cap.key for cap in SEAT_CAPABILITIES))


@dataclass(frozen=True, slots=True)
class Seat:
    """One principal's seat: its name, what it may do, and where each answer came from.

    Attributes:
        tenant_id: The tenant the seat lives in.
        user_id: The user holding it.
        label: The seat's name, or ``""`` when nobody has named it.
        capabilities: ``{key: bool}`` for every entry in :data:`SEAT_CAPABILITIES`.
        sources: ``{key: scope}`` — ``"platform"``, ``"tenant"`` or ``"user"``, the
            layer whose write actually decided each answer. Carried because a revoked
            capability that does not say *which* layer revoked it sends an operator
            hunting through three screens.
    """

    tenant_id: int
    user_id: int
    label: str
    capabilities: dict[str, bool]
    sources: dict[str, str]


async def seat_allows(
    session: AsyncSession,
    key: str,
    *,
    tenant_id: int | None,
    user_id: int | None,
) -> bool:
    """Return whether this principal's seat permits ``key``.

    **This is a narrowing check and nothing else.** Call it *after* the coarse role
    guard has already admitted the request; the effective permission is the ``AND`` of
    the two. It cannot admit a request the guard refused, because it is never consulted
    for one.

    A principal with no tenant — platform staff — has no seat: seats are a tenant's way
    of dividing up authority it already holds, and applying one to the platform's own
    operators would let a tenant-scoped row narrow a platform action. ``True`` is
    therefore returned before any query runs, so an untenanted request also costs no
    round trip.

    Args:
        session: An async session with the tenant scope already bound (see
            :func:`aegis.governance.rls.set_tenant_scope`).
        key: One of :data:`SEAT_CAPABILITIES`' keys.
        tenant_id: The principal's tenant, or ``None`` for platform staff.
        user_id: The principal's user id, or ``None``.

    Returns:
        ``True`` when the seat permits the action.

    Raises:
        ValueError: If ``key`` is not a seat capability. A typo would otherwise resolve
            some unrelated control and gate an action on it.
    """
    if key not in {cap.key for cap in SEAT_CAPABILITIES}:
        raise ValueError(
            f"{key!r} is not a seat capability; the closed set is "
            f"{[cap.key for cap in SEAT_CAPABILITIES]}"
        )
    if tenant_id is None:
        return True
    value, _source = await resolve(session, key, tenant_id=tenant_id, user_id=user_id)
    return bool(value)


async def seat_of(
    session: AsyncSession, *, tenant_id: int, user_id: int
) -> Seat:
    """Return one principal's whole seat — the "who can do what" row.

    One resolve per key rather than a bespoke query, so the seat a screen renders and
    the value a guard enforces come from the same fold. The label is read through the
    catalogue's own default, so an unnamed seat is ``""`` and never ``None``.

    Args:
        session: An async session with the tenant scope already bound.
        tenant_id: The tenant the seat lives in.
        user_id: The user holding it.

    Returns:
        The resolved :class:`Seat`.
    """
    capabilities: dict[str, bool] = {}
    sources: dict[str, str] = {}
    for cap in SEAT_CAPABILITIES:
        value, source = await resolve(
            session, cap.key, tenant_id=tenant_id, user_id=user_id
        )
        capabilities[cap.key] = bool(value)
        sources[cap.key] = source
    label_value, label_source = await resolve(
        session, SEAT_LABEL_KEY, tenant_id=tenant_id, user_id=user_id
    )
    sources[SEAT_LABEL_KEY] = label_source
    return Seat(
        tenant_id=tenant_id,
        user_id=user_id,
        label=str(label_value or ""),
        capabilities=capabilities,
        sources=sources,
    )


def seat_spec(key: str) -> Any:  # noqa: ANN401 - SettingSpec, kept loose for import weight
    """Return the catalogue spec for a seat key, refusing anything else.

    A thin, deliberate wrapper: it is the one place that asserts a seat key is a
    catalogue key, so the closed set above and :data:`~aegis.settings.spec.SETTING_SPECS`
    cannot drift apart silently.
    """
    if key not in SEAT_KEYS:
        raise ValueError(f"{key!r} is not part of a seat; the seat is {list(SEAT_KEYS)}")
    return spec_for(key)
