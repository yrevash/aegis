"""Resolving a setting across the three scopes, and writing one safely.

Two functions matter here.

:func:`resolve` answers *"what is in force, and who decided it"* — and the second half is
not decoration. A control that shows a value without saying whether it is the platform
default, the tenant's choice or the user's own is a control nobody can reason about, and
Phase 6's composer renders the source as a badge next to the value.

:func:`write_setting` is the guard. It refuses four separate ways, each with a reason
the caller can show a human:

* the key is not in the catalogue;
* the writer's fine role is not in the key's ``writable_by`` (a **403** in the host —
  and enforced here, in the resolver, precisely because a disabled control in a form is
  a hint and a ``curl`` is not);
* the value is the wrong type, out of bounds, or not one of the declared choices;
* the value is **weaker than the enclosing scope** for a ``tighten_only`` key.

The last one is refused rather than accepted-and-ignored on purpose. The resolver
already cannot compute a weaker value — :func:`aegis.settings.spec.strictest` folds the
whole chain and the platform layer is always in it — so a weaker write would be stored,
displayed back to the tenant admin as their setting, and have no effect whatsoever. A
setting that lies about being in force is worse than one that was refused, so the write
loses and says why.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance.config import role_rank
from aegis.governance.security import MEMBER, PLATFORM_ADMIN, TENANT_ADMIN
from aegis.governance.types import Role
from aegis.settings.models import Setting, SettingScope
from aegis.settings.spec import (
    SETTING_SPECS,
    MergeRule,
    SettingSpec,
    UnknownSettingError,
    spec_for,
    strictest,
)

__all__ = [
    "SettingError",
    "SettingNotReadableError",
    "SettingNotWritableError",
    "SettingValueError",
    "SettingWeakerThanFloorError",
    "UnknownSettingError",
    "resolve",
    "resolve_all",
    "write_setting",
]

#: The rank a role needs to write a **platform**-scoped row: platform admin, and nothing
#: below it. Read off the RBAC ladder rather than restated, so a change to the ladder
#: cannot leave a second copy of it here saying something else.
_PLATFORM_SCOPE_RANK = role_rank(PLATFORM_ADMIN)

#: The rank needed to write a **tenant**-scoped row — an operator tier. This is what
#: stops a business user setting their tenant's default while still letting them set
#: their own: ``writable_by`` says *whether* a role may touch the key at all, and this
#: says *how far* its writes reach.
_TENANT_SCOPE_RANK = role_rank(Role.AI_TEAM.value)

#: The rank needed to write **another user's** row inside a tenant.
_OTHER_USER_RANK = role_rank(TENANT_ADMIN)


class SettingError(Exception):
    """Base for every refusal this module makes, always carrying a reason.

    Attributes:
        reason: A sentence naming what was refused and why, safe to show a human. The
            whole point of this hierarchy is that no refusal is silent, so a reason is
            required rather than optional.
    """

    def __init__(self, reason: str) -> None:
        """Build the error with the reason it will be reported by."""
        super().__init__(reason)
        self.reason = reason


class SettingNotWritableError(SettingError):
    """The writer's role, or the scope aimed at, is not theirs to write (a **403**)."""


class SettingNotReadableError(SettingError):
    """The reader's role is not in the key's ``readable_by`` (a **403**)."""


class SettingValueError(SettingError, ValueError):
    """The value is not legal for the key — wrong type, out of bounds, unknown choice."""


class SettingWeakerThanFloorError(SettingValueError):
    """A ``tighten_only`` write would be weaker than the scope enclosing it.

    Attributes:
        floor: The value already in force from the enclosing scopes, which this write
            would have had to be at least as strict as.
    """

    def __init__(self, reason: str, floor: Any) -> None:  # noqa: ANN401 - any value
        """Build the error, carrying the floor the write lost to."""
        super().__init__(reason)
        self.floor = floor


def _normalise_role(fine_role: str) -> str:
    """Return the fine role a permission check should use.

    Args:
        fine_role: The caller's fine RBAC tier.

    Returns:
        The tier itself, with the legacy ``user`` alias collapsed to ``client`` exactly
        as :func:`aegis.governance.config.role_rank` collapses it. Two places deciding
        what ``"user"`` means is how a legacy alias becomes a privilege escalation.
    """
    return Role.CLIENT.value if fine_role == MEMBER else fine_role


async def _layers(
    session: AsyncSession,
    keys: tuple[str, ...],
    *,
    tenant_id: int | None,
    user_id: int | None,
) -> dict[tuple[str, SettingScope], Any]:
    """Read the written rows for ``keys`` at every scope that applies, in one query.

    The app-level ``WHERE`` here is the belt-and-suspenders layer over the database's
    ``tenant_isolation`` policy that every governed read in this codebase carries — and
    the only layer on SQLite. Note the platform disjunct: the platform baseline row has
    a NULL ``tenant_id``, and it is readable under a bound tenant scope *only* because
    ``settings`` is registered in
    :data:`aegis.governance.rls._PLATFORM_BASELINE_TABLES`. Without that the resolver
    would silently lose the platform layer and compute a weaker value than the platform
    chose — the exact failure ``tighten_only`` exists to prevent.

    Args:
        session: The async session.
        keys: The catalogue keys to read.
        tenant_id: The tenant whose layer to include, if any.
        user_id: The user whose layer to include, if any.

    Returns:
        A mapping of ``(key, scope)`` to the stored value.
    """
    scopes = [Setting.scope == SettingScope.PLATFORM]
    if tenant_id is not None:
        scopes.append(
            (Setting.scope == SettingScope.TENANT) & (Setting.tenant_id == tenant_id)
        )
        if user_id is not None:
            scopes.append(
                (Setting.scope == SettingScope.USER)
                & (Setting.tenant_id == tenant_id)
                & (Setting.user_id == user_id)
            )
    result = await session.execute(
        select(Setting.key, Setting.scope, Setting.value)
        .where(Setting.key.in_(keys))
        .where(or_(*scopes))
    )
    return {(key, scope): value for key, scope, value in result}


def _merge(
    spec: SettingSpec,
    value: Any,  # noqa: ANN401 - any setting value
    source: SettingScope,
    candidate: Any,  # noqa: ANN401 - any setting value
    scope: SettingScope,
) -> tuple[Any, SettingScope]:
    """Fold one scope's written value onto the value resolved so far.

    Args:
        spec: The setting being resolved.
        value: The value from the enclosing scopes.
        source: Which scope that value came from.
        candidate: The value written at ``scope``.
        scope: The scope ``candidate`` was written at.

    Returns:
        The new ``(value, source)``. ``source`` advances to ``scope`` only when the
        write actually changed the outcome — a tenant row that loses to a stricter
        platform default leaves the source reading ``platform``, which is exactly what
        the badge must say.
    """
    match spec.merge:
        case MergeRule.OVERRIDE:
            return candidate, scope
        case MergeRule.TIGHTEN_ONLY:
            # The whole guarantee, in one expression: the platform layer is always one
            # of the arguments, so the fold cannot descend below it.
            winner = strictest(spec, candidate, value)
            return (winner, scope) if winner != value else (value, source)
        case MergeRule.UNION:
            merged = list(dict.fromkeys([*value, *candidate]))
            return (merged, scope) if merged != value else (value, source)
    raise AssertionError(f"unhandled merge rule {spec.merge!r}")  # pragma: no cover


def _resolve_from_layers(
    spec: SettingSpec, layers: dict[tuple[str, SettingScope], Any]
) -> tuple[Any, str]:
    """Resolve one key from already-read layers.

    Args:
        spec: The setting to resolve.
        layers: The rows read by :func:`_layers`.

    Returns:
        ``(value, source)``.
    """
    value: Any = spec.default
    source = SettingScope.PLATFORM
    platform = layers.get((spec.key, SettingScope.PLATFORM))
    if platform is not None:
        # The platform's own override of the compiled-in default. Still ``platform`` as
        # far as a badge is concerned: both are the platform's decision.
        value = (
            platform
            if spec.merge is not MergeRule.UNION
            else list(dict.fromkeys([*spec.default, *platform]))
        )
    for scope in (SettingScope.TENANT, SettingScope.USER):
        candidate = layers.get((spec.key, scope))
        if candidate is None:
            continue
        value, source = _merge(spec, value, source, candidate, scope)
    return value, source.value


async def resolve(
    session: AsyncSession,
    key: str,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_role: str | None = None,
) -> tuple[Any, str]:
    """Return the effective value and the scope it came from.

    The ``source`` half is not decoration: a control that shows a value without saying
    whether it is the platform default, the tenant's choice or the user's own is a
    control nobody can reason about. Phase 6's composer renders it as a badge.

    Args:
        session: The async session. On PostgreSQL the caller is expected to have bound
            the tenant scope (:func:`aegis.governance.rls.set_tenant_scope`); the query
            filters by tenant as well, so the two agree.
        key: The catalogue key.
        tenant_id: The tenant to resolve for, or ``None`` for the platform layer alone.
        user_id: The user to resolve for. Ignored without a tenant, because a user row
            is only meaningful inside its tenant.
        actor_role: The reader's fine RBAC role. When given it is enforced against the
            key's ``readable_by``; when omitted the read is an internal one (admission
            control reading a job cap has no user), so no role check applies.

    Returns:
        ``(value, source)`` where ``source`` is ``"platform"``, ``"tenant"`` or
        ``"user"`` — the scope that decided the value, which for a ``tighten_only`` key
        is the scope whose write actually won.

    Raises:
        UnknownSettingError: If the key is not in the catalogue.
        SettingNotReadableError: If ``actor_role`` may not read this key.
    """
    spec = spec_for(key)
    if actor_role is not None and _normalise_role(actor_role) not in spec.readable_by:
        raise SettingNotReadableError(
            f"role {actor_role!r} may not read {key!r}; it is readable by "
            f"{sorted(spec.readable_by)}"
        )
    layers = await _layers(session, (key,), tenant_id=tenant_id, user_id=user_id)
    return _resolve_from_layers(spec, layers)


async def resolve_all(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_role: str | None = None,
) -> dict[str, tuple[Any, str]]:
    """Resolve every key the caller may read, in one query.

    What a settings screen renders: the whole catalogue with each value's source, rather
    than N round trips or a bespoke endpoint per key.

    Args:
        session: The async session.
        tenant_id: The tenant to resolve for.
        user_id: The user to resolve for.
        actor_role: The reader's fine role. Keys they may not read are **omitted**
            rather than refused — this is a screen, and one unreadable key should not
            blank the page.

    Returns:
        ``{key: (value, source)}`` for every readable key.
    """
    role = _normalise_role(actor_role) if actor_role is not None else None
    specs = [
        spec
        for spec in SETTING_SPECS
        if role is None or role in spec.readable_by
    ]
    layers = await _layers(
        session, tuple(spec.key for spec in specs), tenant_id=tenant_id, user_id=user_id
    )
    return {spec.key: _resolve_from_layers(spec, layers) for spec in specs}


def _check_scope_permission(
    spec: SettingSpec,
    *,
    scope: SettingScope,
    role: str,
    tenant_id: int | None,
    user_id: int | None,
    actor_user_id: int | None,
) -> None:
    """Refuse a write whose scope is beyond the writer's reach.

    ``writable_by`` says whether a role may touch the key at all; this says how far its
    writes reach. Both are needed: ``agent.model`` is writable by every role, and a
    business user setting *their own* preference is the point of it, while the same user
    setting the whole tenant's default is not.

    Args:
        spec: The setting being written.
        scope: The scope being written at.
        role: The writer's normalised fine role.
        tenant_id: The tenant the row is for.
        user_id: The user the row is for.
        actor_user_id: The writer's own user id, when known.

    Raises:
        SettingNotWritableError: If the scope is not the writer's to touch, or the ids
            do not describe that scope.
    """
    rank = role_rank(role)
    match scope:
        case SettingScope.PLATFORM:
            if rank < _PLATFORM_SCOPE_RANK:
                raise SettingNotWritableError(
                    f"role {role!r} may not write platform-scoped settings; "
                    f"{spec.key!r} at platform scope is the platform admin's to set"
                )
            if tenant_id is not None or user_id is not None:
                raise SettingNotWritableError(
                    "a platform-scoped setting carries no tenant and no user"
                )
        case SettingScope.TENANT:
            if tenant_id is None:
                raise SettingNotWritableError("a tenant-scoped setting needs a tenant")
            if user_id is not None:
                raise SettingNotWritableError(
                    "a tenant-scoped setting carries no user; write at user scope"
                )
            if rank < _TENANT_SCOPE_RANK:
                raise SettingNotWritableError(
                    f"role {role!r} may set its own preference but not the tenant "
                    f"default for {spec.key!r}"
                )
        case SettingScope.USER:
            if tenant_id is None or user_id is None:
                raise SettingNotWritableError(
                    "a user-scoped setting needs both a tenant and a user — a row with "
                    "no tenant would be readable by every tenant"
                )
            if user_id != actor_user_id and rank < _OTHER_USER_RANK:
                raise SettingNotWritableError(
                    f"role {role!r} may not write another user's {spec.key!r}"
                )


def _check_not_weaker(
    spec: SettingSpec,
    value: Any,  # noqa: ANN401 - any setting value
    *,
    floor: Any,  # noqa: ANN401 - any setting value
    scope: SettingScope,
) -> None:
    """Refuse a ``tighten_only`` write that is weaker than the enclosing scopes.

    Args:
        spec: The setting being written.
        value: The candidate value.
        floor: What the enclosing scopes already resolve to.
        scope: The scope being written at, named in the reason.

    Raises:
        SettingWeakerThanFloorError: If the candidate is weaker than the floor.
    """
    if spec.merge is not MergeRule.TIGHTEN_ONLY:
        return
    if strictest(spec, value, floor) == value:
        return
    raise SettingWeakerThanFloorError(
        f"{spec.key} may only be tightened: {value!r} is weaker than the {floor!r} "
        f"already in force from the enclosing scope, so writing it at {scope.value} "
        "scope would have no effect. The stricter value stands.",
        floor,
    )


async def write_setting(
    session: AsyncSession,
    key: str,
    value: Any,  # noqa: ANN401 - any setting value
    *,
    scope: SettingScope,
    actor_role: str,
    tenant_id: int | None = None,
    user_id: int | None = None,
    actor_user_id: int | None = None,
    updated_by: str | None = None,
) -> Setting:
    """Write one setting at one scope, or refuse with a reason.

    The caller owns the transaction (and, on PostgreSQL, the bound tenant scope): the
    row is flushed, not committed, so a route can write a setting and its audit entry
    atomically.

    Args:
        session: The async session.
        key: The catalogue key.
        value: The value to write.
        scope: Which layer to write it at.
        actor_role: The writer's fine RBAC role.
        tenant_id: The tenant the row belongs to. Required for tenant and user scope.
        user_id: The user the row belongs to. Required for user scope.
        actor_user_id: The writer's own user id, so a user writing their own row is told
            apart from one writing somebody else's.
        updated_by: Who to record as the writer; defaults to ``actor_role``.

    Returns:
        The inserted or updated :class:`Setting` row.

    Raises:
        UnknownSettingError: If the key is not in the catalogue.
        SettingNotWritableError: If the role or the scope is not the writer's.
        SettingValueError: If the value is not legal for the key.
        SettingWeakerThanFloorError: If a ``tighten_only`` write would weaken the
            enclosing scope.
    """
    spec = spec_for(key)
    role = _normalise_role(actor_role)
    if role not in spec.writable_by:
        raise SettingNotWritableError(
            f"role {actor_role!r} may not write {key!r}; it is writable by "
            f"{sorted(spec.writable_by)}"
        )
    _check_scope_permission(
        spec,
        scope=scope,
        role=role,
        tenant_id=tenant_id,
        user_id=user_id,
        actor_user_id=actor_user_id,
    )
    try:
        spec.validate(value)
    except ValueError as exc:
        raise SettingValueError(str(exc)) from exc

    if spec.merge is MergeRule.TIGHTEN_ONLY and scope is not SettingScope.PLATFORM:
        # The floor is what the *enclosing* scopes already resolve to: platform for a
        # tenant write, platform+tenant for a user write. Read through the same resolver
        # the effective value comes from, so the refusal and the resolution can never
        # disagree about what the floor is.
        floor, _ = await resolve(
            session,
            key,
            tenant_id=tenant_id if scope is SettingScope.USER else None,
            user_id=None,
        )
        _check_not_weaker(spec, value, floor=floor, scope=scope)

    row = await _existing_row(session, key, scope=scope, tenant_id=tenant_id, user_id=user_id)
    stored = list(value) if spec.type_ is list else value
    if row is None:
        row = Setting(
            scope=scope,
            tenant_id=tenant_id,
            user_id=user_id,
            key=key,
            value=stored,
            updated_by=updated_by or actor_role,
        )
        session.add(row)
    else:
        row.value = stored
        row.updated_by = updated_by or actor_role
        row.updated_at = datetime.now(UTC)
    await session.flush()
    return row


async def _existing_row(
    session: AsyncSession,
    key: str,
    *,
    scope: SettingScope,
    tenant_id: int | None,
    user_id: int | None,
) -> Setting | None:
    """Return the row already written at this exact scope, if there is one.

    Args:
        session: The async session.
        key: The catalogue key.
        scope: The scope written at.
        tenant_id: The tenant the row belongs to.
        user_id: The user the row belongs to.

    Returns:
        The existing :class:`Setting`, or ``None``.
    """
    statement = select(Setting).where(Setting.key == key, Setting.scope == scope)
    statement = statement.where(
        Setting.tenant_id.is_(None) if tenant_id is None else Setting.tenant_id == tenant_id
    )
    statement = statement.where(
        Setting.user_id.is_(None) if user_id is None else Setting.user_id == user_id
    )
    return (await session.execute(statement)).scalars().one_or_none()
