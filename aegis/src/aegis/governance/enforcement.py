"""Governance data layer — budgets, the usage ledger, and admin rollups.

This module owns the durable side of multi-tenant governance:

- :func:`effective_limits` — resolve a principal's nearest-binding caps (user cap
  clamped inward to its tenant cap) for the per-request :class:`GovernanceContext`.
- :func:`enforce_governance` — the gateway-chokepoint check: sum the
  :class:`~aegis.governance.models.UsageLedger` for the tenant→user path over each
  budget's window and raise :class:`~aegis.gateway.types.BudgetExceededError` on the
  first breach.
- :func:`record_usage` — write one durable ledger row per model call.
- Admin queries backing ``/admin/tenants|users|budgets|usage`` (with tenant-level
  app-scoping as the belt-and-suspenders layer over Postgres RLS).

The **session factory** and **``set_tenant_scope``** are injected via
:func:`configure_enforcement` so the host owns the engine/session lifecycle; each
function opens its own short-lived session (like :mod:`aegis.governance.audit`) so
callers never thread a session through their signatures. All sums use
``func.coalesce(func.sum(...), 0)`` so they run identically on SQLite and Postgres.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.gateway.types import BudgetExceededError
from aegis.governance.models import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    UsageLedger,
    User,
)
from aegis.governance.rls import set_tenant_scope as _default_set_tenant_scope
from aegis.governance.security import hash_password
from aegis.governance.types import (
    AdminUserRow,
    BudgetRow,
    GovernanceLimits,
    Role,
    TenantRow,
    UsageByModel,
    UsageSeriesPoint,
)

__all__ = [
    "WINDOW_SECONDS",
    "DuplicateTenantError",
    "DuplicateUserError",
    "LastPlatformAdminError",
    "UserCapAboveTenantCapError",
    "configure_enforcement",
    "create_tenant",
    "create_user",
    "effective_limits",
    "enforce_governance",
    "list_budgets",
    "list_tenants",
    "list_users",
    "record_usage",
    "update_user_role",
    "upsert_budget",
    "usage_rollup",
    "user_tenant_id",
]

# ─────────────────────────────────────────────────────────────────────────────
# Injected host wiring — the session factory + the RLS scope binder.
# ─────────────────────────────────────────────────────────────────────────────

logger = logging.getLogger(__name__)

_SessionFactory = Callable[[], AsyncSession]
_SetTenantScope = Callable[[AsyncSession, int | None], Awaitable[None]]

_session_factory: _SessionFactory | None = None
_set_tenant_scope: _SetTenantScope = _default_set_tenant_scope


def configure_enforcement(
    *,
    session_factory: _SessionFactory | None = None,
    set_tenant_scope: _SetTenantScope | None = None,
) -> None:
    """Wire the injected session factory and (optionally) ``set_tenant_scope``.

    Call once at host startup (or import time of a strangler shim). ``set_tenant_scope``
    defaults to :func:`aegis.governance.rls.set_tenant_scope`; a host may inject its own
    (e.g. a late-binding wrapper) to keep an existing test seam.

    Args:
        session_factory: A zero-arg callable returning an :class:`AsyncSession`
            (used as ``async with session_factory() as session``).
        set_tenant_scope: The RLS scope binder; defaults to the package's own.
    """
    global _session_factory, _set_tenant_scope
    if session_factory is not None:
        _session_factory = session_factory
    if set_tenant_scope is not None:
        _set_tenant_scope = set_tenant_scope


def _session() -> AsyncSession:
    """Return a fresh :class:`AsyncSession` from the injected factory."""
    if _session_factory is None:
        raise RuntimeError(
            "aegis.governance enforcement is not configured; call "
            "configure_enforcement(session_factory=...) at startup."
        )
    return _session_factory()


async def apply_tenant_scope(session: AsyncSession, tenant_id: int | None) -> None:
    """Bind the tenant scope through the **currently configured** binder.

    The public seam for any module outside this one that needs the same binder, and the
    reason it exists is a real defect this project found: ``aegis.governance.dashboard``
    did ``from aegis.governance.enforcement import _set_tenant_scope`` at import time,
    which captures whatever the binder was *then*. A later ``configure_governance`` — the
    supported way a host injects its own binder, and the way the tests install a spy —
    rebinds this module's global and never touches that copy, so the dashboard's reads
    went out through the original binder and the seam was blind to them.

    Harmless in itself, and exactly the shape that makes an enumeration incomplete: the
    instrument reports "no unscoped reads" because it cannot see the reader. Resolving
    the name on every call is what makes one seam mean one thing.

    Args:
        session: The session to bind the scope on.
        tenant_id: The tenant, or ``None`` for a deliberate platform-wide read.
    """
    await _set_tenant_scope(session, tenant_id)


#: The rolling span, in seconds, for each budget window (token/usd caps). Public because
#: it is the one definition of what a window *means*: the dashboard, the config projection
#: and job admission (:mod:`aegis.jobs.admission`) all measure spend over it, and a second
#: copy would let one of them disagree with the caps this module enforces.
WINDOW_SECONDS: dict[BudgetWindow, int] = {
    BudgetWindow.DAY: 24 * 3600,
    BudgetWindow.MONTH: 30 * 24 * 3600,
}
# The rolling span for per-minute rate caps (RPM/TPM).
_RATE_SECONDS = 60


def _now_naive() -> datetime:
    """Return the current UTC time as a naive datetime.

    The ledger's ``ts`` column is ``TIMESTAMP WITHOUT TIME ZONE`` (naive UTC on
    both Postgres and SQLite), so window comparisons use a naive UTC bound to match.
    """
    return datetime.now(UTC).replace(tzinfo=None)


def _iso_utc(ts: datetime | None) -> str:
    """Render a (possibly naive) timestamp as an ISO 8601 UTC string."""
    if ts is None:
        return ""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# Limit resolution + enforcement (the chokepoint)
# ─────────────────────────────────────────────────────────────────────────────


def _clamp_inward(user_cap: int | float | None, tenant_cap: int | float | None):  # noqa: ANN202
    """Return the tighter (smaller) of a user cap and its tenant cap.

    ``None`` means uncapped, so a present cap always binds over an absent one and two
    present caps resolve to their minimum — the inward-enforcement semantics.

    **Defence in depth, not the only defence.** :func:`upsert_budget` now refuses to
    *store* a user sub-cap above its tenant's, so in a healthy database this clamp has
    nothing to clamp. It stays because the two answer different questions: the write
    guard makes the stored number and the enforced number agree, and this makes the
    enforced number safe whatever is in the row — a row written before the guard
    existed, by a migration, or by hand.
    """
    caps = [c for c in (user_cap, tenant_cap) if c is not None]
    return min(caps) if caps else None


async def _budgets_for(
    session: AsyncSession, *, tenant_id: int | None, user_id: int | None
) -> list[Budget]:
    """Return the tenant- and user-scoped budget rows for a principal (user first).

    **Every window, mixed together.** The result may hold a ``day`` row and a ``month``
    row for the same scope, and the ordering is by scope only. A caller that reduces
    this list to one number must group by ``Budget.window`` first: taking the first
    tenant row and the first user row and comparing them was the defect
    :func:`effective_limits` documents. :func:`enforce_governance` needs the mixture as
    it is — it checks each row over that row's own window — which is why the grouping
    belongs to the caller and not here.
    """
    conds = []
    if tenant_id is not None:
        conds.append(
            (Budget.scope_type == BudgetScope.TENANT) & (Budget.scope_id == tenant_id)
        )
    if user_id is not None:
        conds.append(
            (Budget.scope_type == BudgetScope.USER) & (Budget.scope_id == user_id)
        )
    if not conds:
        return []
    clause = conds[0]
    for extra in conds[1:]:
        clause = clause | extra
    rows = (await session.execute(select(Budget).where(clause))).scalars().all()
    # User-scope first so a user breach is attributed to the user when both trip.
    return sorted(rows, key=lambda b: 0 if b.scope_type is BudgetScope.USER else 1)


#: Windows narrowest-first. ``effective_limits`` reports the narrowest window that
#: actually governs the principal, because that is the cap that bites first.
_WINDOWS_NARROWEST_FIRST: tuple[BudgetWindow, ...] = (
    BudgetWindow.DAY,
    BudgetWindow.MONTH,
)


def _governing_window(budgets: list[Budget]) -> BudgetWindow | None:
    """Return the narrowest window any of ``budgets`` runs over, or ``None`` if empty."""
    present = {b.window for b in budgets}
    return next((w for w in _WINDOWS_NARROWEST_FIRST if w in present), None)


def _tightest(budgets: list[Budget], field: str) -> int | float | None:
    """Return the smallest non-``None`` ``field`` across ``budgets`` (``None`` = uncapped)."""
    caps = [getattr(b, field) for b in budgets]
    present = [c for c in caps if c is not None]
    return min(present) if present else None


async def effective_limits(
    tenant_id: int | None,
    user_id: int | None,
    *,
    window: BudgetWindow | None = None,
) -> GovernanceLimits:
    """Resolve the nearest-binding caps for a principal (user clamped to tenant).

    **Windows are never mixed.** ``_budgets_for`` returns every row for the principal,
    across every window, and this used to take the *first* tenant row and the *first*
    user row regardless of which window each ran over — so a tenant's ``month`` cap
    could clamp a user's ``day`` cap and the resulting figure described no cap that
    exists. A monthly quantity and a daily quantity are not comparable, and
    ``min(month, day)`` is a category error however small the number it returns.

    This was **a display defect, not an enforcement one**:
    :func:`enforce_governance` reads the same rows but sums the ledger over *each row's
    own* window, so what actually bound a call was always right. What was wrong was the
    number the operator read — which is precisely the failure §7 exists to remove.

    ``token_cap`` / ``usd_cap`` are resolved for **one** window and
    :attr:`GovernanceLimits.window` names it, so the figure and its denominator travel
    together. ``rpm`` / ``tpm`` are per-minute quantities that
    :func:`enforce_governance` checks on *every* governing row whatever window that row
    runs over, so they are resolved across all of them — the tightest binds, which is
    what the enforcer does.

    Args:
        tenant_id: The tenant the principal belongs to.
        user_id: The acting user.
        window: The accounting window to report ``token_cap``/``usd_cap`` for. Omit for
            the narrowest window that actually governs this principal (a day cap bites
            before a month cap), or ``day`` when nothing governs it at all.

    Returns:
        The merged :class:`GovernanceLimits` for the per-request context; every field
        is uncapped (``None``) when no budget row governs it.
    """
    if tenant_id is None:
        return GovernanceLimits()
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        budgets = await _budgets_for(session, tenant_id=tenant_id, user_id=user_id)
    reported = window or _governing_window(budgets) or BudgetWindow.DAY
    in_window = [b for b in budgets if b.window is reported]
    tenants = [b for b in in_window if b.scope_type is BudgetScope.TENANT]
    users = [b for b in in_window if b.scope_type is BudgetScope.USER]
    all_tenants = [b for b in budgets if b.scope_type is BudgetScope.TENANT]
    all_users = [b for b in budgets if b.scope_type is BudgetScope.USER]
    return GovernanceLimits(
        token_cap=_clamp_inward(
            _tightest(users, "token_cap"), _tightest(tenants, "token_cap")
        ),
        usd_cap=_clamp_inward(
            _tightest(users, "usd_cap"), _tightest(tenants, "usd_cap")
        ),
        rpm=_clamp_inward(_tightest(all_users, "rpm"), _tightest(all_tenants, "rpm")),
        tpm=_clamp_inward(_tightest(all_users, "tpm"), _tightest(all_tenants, "tpm")),
        window=reported.value,
    )


async def _usage_sums(
    session: AsyncSession,
    *,
    scope_col: Any,  # noqa: ANN401 - a SQLAlchemy column expression (tenant/user id)
    scope_id: int,
    since: datetime,
) -> tuple[int, float, int]:
    """Return ``(tokens, cost_usd, calls)`` for a scope since ``since`` (naive UTC)."""
    tokens_expr = func.coalesce(
        func.sum(UsageLedger.prompt_tokens + UsageLedger.completion_tokens), 0
    )
    cost_expr = func.coalesce(func.sum(UsageLedger.cost_usd), 0.0)
    calls_expr = func.count(UsageLedger.id)
    row = (
        await session.execute(
            select(tokens_expr, cost_expr, calls_expr).where(
                scope_col == scope_id, UsageLedger.ts >= since
            )
        )
    ).one()
    return int(row[0] or 0), float(row[1] or 0.0), int(row[2] or 0)


async def enforce_governance(*, tenant_id: int | None, user_id: int | None) -> None:
    """Raise on the first breached cap along the tenant→user path.

    For each governing budget row this sums the ledger for that scope over the row's
    window (token/usd caps) or the last minute (rpm/tpm) and blocks when consumption
    has already reached the cap. User rows are checked first so a user breach is
    attributed to the user.

    Args:
        tenant_id: The acting tenant (no-op when ``None``).
        user_id: The acting user, if any.

    Raises:
        BudgetExceededError: When a token/usd/rpm/tpm cap is at or over its limit.
    """
    if tenant_id is None:
        return

    now = _now_naive()
    async with _session() as session:
        # Engage Postgres RLS for this connection (no-op on SQLite; H1).
        await _set_tenant_scope(session, tenant_id)
        budgets = await _budgets_for(session, tenant_id=tenant_id, user_id=user_id)
        for b in budgets:
            scope_col = (
                UsageLedger.tenant_id
                if b.scope_type is BudgetScope.TENANT
                else UsageLedger.user_id
            )
            window_since = now - timedelta(
                seconds=WINDOW_SECONDS.get(b.window, WINDOW_SECONDS[BudgetWindow.DAY])
            )
            tokens, cost, _calls = await _usage_sums(
                session, scope_col=scope_col, scope_id=b.scope_id, since=window_since
            )
            if b.token_cap is not None and tokens >= b.token_cap:
                _raise(BudgetExceededError, b, "token_cap", b.token_cap, tokens)
            if b.usd_cap is not None and cost >= b.usd_cap:
                _raise(BudgetExceededError, b, "usd_cap", b.usd_cap, cost)
            if b.rpm is not None or b.tpm is not None:
                rate_since = now - timedelta(seconds=_RATE_SECONDS)
                r_tokens, _r_cost, r_calls = await _usage_sums(
                    session, scope_col=scope_col, scope_id=b.scope_id, since=rate_since
                )
                if b.rpm is not None and r_calls >= b.rpm:
                    _raise(BudgetExceededError, b, "rpm", b.rpm, r_calls)
                if b.tpm is not None and r_tokens >= b.tpm:
                    _raise(BudgetExceededError, b, "tpm", b.tpm, r_tokens)


def _raise(exc_cls, budget: Budget, limit_type: str, limit, used) -> None:  # noqa: ANN001
    """Raise a :class:`BudgetExceededError` describing the tripped cap."""
    raise exc_cls(
        scope=budget.scope_type.value,
        scope_id=budget.scope_id,
        limit_type=limit_type,
        limit=float(limit) if limit is not None else None,
        used=float(used) if used is not None else None,
    )


async def record_usage(
    *,
    tenant_id: int | None,
    user_id: int | None,
    model: str | None,
    prompt_tokens: int,
    completion_tokens: int,
    cost_usd: float,
    trace_id: str | None,
    audio_seconds: float = 0.0,
    images: int = 0,
    run_id: str | None = None,
) -> None:
    """Write one durable usage-ledger row for a governed model call.

    ``audio_seconds`` / ``images`` are the non-token billable units of a
    transcription or vision call (see :class:`~aegis.governance.models.UsageLedger`).
    Both default to zero, so every existing token-only call site is unchanged.
    Note that ``cost_usd`` already prices them, which is what makes a USD cap
    bite on a per-minute-billed call — the token caps deliberately do not, since
    an audio minute is not a token.

    ``run_id`` is the agent run this call was made for, and ``None`` — the default —
    is the honest answer for a call that belongs to no run. It is stored as written:
    nothing here infers a run from ``trace_id``, because that inference is precisely
    the one that made per-run cost and total spend two different numbers.
    """
    async with _session() as session:
        # Engage Postgres RLS for this connection (no-op on SQLite; H1).
        await _set_tenant_scope(session, tenant_id)
        session.add(
            UsageLedger(
                tenant_id=tenant_id,
                user_id=user_id,
                model=model,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                audio_seconds=audio_seconds,
                images=images,
                cost_usd=cost_usd,
                trace_id=trace_id,
                run_id=run_id,
            )
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# Admin surfaces — tenants / users / budgets / usage rollup
# ─────────────────────────────────────────────────────────────────────────────


class DuplicateTenantError(RuntimeError):
    """Raised when creating a tenant whose name already exists (names are unique)."""


class DuplicateUserError(RuntimeError):
    """Raised when creating a user whose username already exists (usernames are unique)."""


async def list_tenants() -> list[TenantRow]:
    """Return every tenant, newest first (platform-admin surface)."""
    async with _session() as session:
        rows = (
            await session.execute(select(Tenant).order_by(Tenant.id.desc()))
        ).scalars().all()
    return [
        TenantRow(
            id=t.id,
            name=t.name,
            status=t.status.value,
            created_at=_iso_utc(t.created_at),
        )
        for t in rows
    ]


async def create_tenant(
    name: str,
    *,
    usd_cap: float,
    window: BudgetWindow = BudgetWindow.DAY,
) -> TenantRow:
    """Create a tenant (client) **and its spend cap**, and return its row.

    The cap is required, and the tenant and its ``budgets`` row are committed in one
    transaction, so a tenant that exists without a cap is not a state this system can
    reach. That is deliberate: an absent ``budgets`` row means *uncapped* — the same
    answer :func:`enforce_governance` gives — so a tenant onboarded without one would
    spend without limit, and the omission would show up as a bill rather than as an
    error. Requiring it here is the only place the requirement can be enforced once.

    The tenant admin then allocates each of their users a cap under this one; a user's
    effective limit is clamped inward by it (see :func:`effective_limits`).

    Args:
        name: The unique tenant (client) name.
        usd_cap: The tenant's spend cap in USD over ``window``. Must be positive — a
            zero cap would create a tenant that cannot make a single call, which is
            almost certainly a typo rather than an intention.
        window: The accounting window the cap runs over.

    Raises:
        DuplicateTenantError: If a tenant with this name already exists.
        ValueError: If ``usd_cap`` is not positive.
    """
    if usd_cap <= 0:
        raise ValueError(
            f"usd_cap must be positive, got {usd_cap!r} — a tenant with a zero cap "
            "cannot make a single call"
        )
    async with _session() as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        try:
            await session.flush()
            session.add(
                Budget(
                    tenant_id=tenant.id,
                    scope_type=BudgetScope.TENANT,
                    scope_id=tenant.id,
                    window=window,
                    usd_cap=usd_cap,
                )
            )
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DuplicateTenantError(f"tenant name {name!r} already exists") from exc
        await session.refresh(tenant)
        return TenantRow(
            id=tenant.id,
            name=tenant.name,
            status=tenant.status.value,
            created_at=_iso_utc(tenant.created_at),
        )


async def create_user(
    username: str,
    *,
    role: Role,
    tenant_id: int | None,
    email: str | None = None,
    password: str | None = None,
) -> AdminUserRow:
    """Create a user with a hashed password and return its admin row.

    The password (when given) is Argon2-hashed via
    :func:`aegis.governance.security.hash_password` before it is ever persisted —
    the plaintext is never stored. Usernames are unique; a clash raises
    :class:`DuplicateUserError` (the API maps it to a 409). The tenant scope is
    applied for the belt-and-suspenders RLS layer, mirroring the other admin writes.

    Args:
        username: The new principal's unique login name.
        role: The coarse :class:`~aegis.governance.types.Role` to grant.
        tenant_id: The tenant the user belongs to (``None`` for a platform user).
        email: Optional contact email.
        password: Optional plaintext password; hashed on write. ``None`` leaves the
            row without a usable password (an operator-provisioned shell account).

    Returns:
        The created :class:`~aegis.governance.types.AdminUserRow`.

    Raises:
        DuplicateUserError: If the username already exists.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        user = User(
            username=username,
            role=role,
            tenant_id=tenant_id,
            email=email,
            password_hash=hash_password(password) if password else None,
            is_active=True,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError as exc:
            await session.rollback()
            raise DuplicateUserError(f"username {username!r} already exists") from exc
        await session.refresh(user)
        return AdminUserRow(
            id=user.id,
            username=user.username,
            role=user.role,
            tenant_id=user.tenant_id,
            email=user.email,
            is_active=user.is_active,
        )


async def list_users(tenant_id: int | None = None) -> list[AdminUserRow]:
    """Return users, optionally app-scoped to one tenant (belt-and-suspenders RLS)."""
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        stmt = select(User).order_by(User.id.asc())
        if tenant_id is not None:
            stmt = stmt.where(User.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()
    return [
        AdminUserRow(
            id=u.id,
            username=u.username,
            role=u.role,
            tenant_id=u.tenant_id,
            email=u.email,
            is_active=u.is_active,
        )
        for u in rows
    ]


async def list_budgets(
    scope_type: str | None = None,
    scope_id: int | None = None,
    *,
    tenant_id: int | None = None,
) -> list[BudgetRow]:
    """Return budget rows, optionally filtered by scope type/id and owning tenant.

    Args:
        scope_type: Filter to ``tenant``/``user`` caps when given.
        scope_id: Filter to a single scope id when given.
        tenant_id: App-scope to one tenant's caps (belt-and-suspenders over RLS).
            ``None`` returns every tenant's caps (the platform-admin view).
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        stmt = select(Budget).order_by(Budget.id.asc())
        if scope_type is not None:
            stmt = stmt.where(Budget.scope_type == BudgetScope(scope_type))
        if scope_id is not None:
            stmt = stmt.where(Budget.scope_id == scope_id)
        if tenant_id is not None:
            stmt = stmt.where(Budget.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()
    return [_budget_row(b) for b in rows]


async def user_tenant_id(user_id: int, *, tenant_scope: int | None = None) -> int | None:
    """Return the tenant a user belongs to, or ``None`` if the user is unknown.

    Used to authorise a user-scoped budget write or a role change: the caller's tenant
    must own the target user. A ``None`` return means "no such user *in scope*", which
    the API surface treats as a 404 rather than silently allowing a cross-tenant write.

    This ran with no tenant scope applied at all — the only governed read in this
    module that did not call :func:`~aegis.governance.rls.set_tenant_scope`, so on
    Postgres the RLS policy never engaged for it. It now binds the scope like every
    sibling read, and a caller that passes ``tenant_scope`` additionally gets the
    app-level check: a user outside that tenant reads back as unknown.

    Args:
        user_id: The target user's id.
        tenant_scope: When set (a tenant-admin caller), a user outside that tenant is
            reported as unknown (``None``). ``None`` (a platform-admin caller, and the
            back-compatible default) may resolve any user.

    Returns:
        The user's ``tenant_id``, or ``None`` when there is no such user in scope.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_scope)
        user = await session.get(User, user_id)
        if user is None:
            return None
        if tenant_scope is not None and user.tenant_id != tenant_scope:
            return None
        return user.tenant_id


class CrossTenantBudgetError(RuntimeError):
    """Raised when a budget write would overwrite a cap owned by another tenant.

    The ``(scope_type, scope_id, window)`` natural key is global, so two tenants can
    name the same triple. Rather than let the later writer take the row over, the
    data layer refuses — a caller may only write a cap its own tenant owns (or one
    that is unowned). Platform-admin callers are exempt.
    """


class UserCapAboveTenantCapError(ValueError):
    """Raised when a user sub-cap would be **stored** above the tenant cap that binds it.

    §7.16 row 2 — *"a tenant admin may set sub-caps on their own users, always <= the
    tenant cap"* — reads two ways, and only one of them used to hold. The *effective*
    limit was always inward (:func:`_clamp_inward`), but the *stored* one was whatever
    was posted: a tenant admin could set a user's cap to $500 under a $50 tenant cap,
    the row saved, the budgets screen read back $500, and $50 was what bound. A
    control whose displayed value is not the value in force is the ``gate_min_risk``
    defect wearing a budget's clothes, so the write is refused here rather than silently
    clamped at read time.

    A ``ValueError`` because it is a statement about the *value*, not about the writer's
    authority: no role — tenant admin or platform admin — may store a cap that cannot
    bind, so this is never a 403.
    """


class LastPlatformAdminError(RuntimeError):
    """Raised when a role change would remove the platform's last platform-admin.

    A platform-admin is a :class:`~aegis.governance.types.Role.ADMIN` user with **no**
    tenant (global operator). Demoting the only such user out of ``admin`` would lock
    the platform out of every platform-admin surface, so it is refused defensively.
    """


async def update_user_role(
    user_id: int,
    role: Role,
    *,
    tenant_scope: int | None = None,
) -> AdminUserRow | None:
    """Reassign a user's coarse RBAC role, returning the updated row.

    Mirrors the short-lived-session pattern of :func:`user_tenant_id`: opens its own
    session, applies the tenant scope for the belt-and-suspenders RLS layer, and
    commits a single update.

    Args:
        user_id: The target user's id.
        role: The new coarse :class:`~aegis.governance.types.Role` to assign.
        tenant_scope: When set (a tenant-admin caller), the update is pinned to that
            tenant — a user outside it is treated as not found (``None``). ``None``
            (a platform-admin caller) may target any user.

    Returns:
        The updated :class:`~aegis.governance.types.AdminUserRow`, or ``None`` if no
        such user exists within the given scope.

    Raises:
        LastPlatformAdminError: If the change would demote the last platform-admin.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_scope)
        user = await session.get(User, user_id)
        if user is None or (tenant_scope is not None and user.tenant_id != tenant_scope):
            return None
        # Defensive lockout guard: never demote the last global platform-admin.
        is_platform_admin = user.role is Role.ADMIN and user.tenant_id is None
        if is_platform_admin and role is not Role.ADMIN:
            remaining = (
                await session.execute(
                    select(func.count())
                    .select_from(User)
                    .where(User.role == Role.ADMIN, User.tenant_id.is_(None))
                )
            ).scalar_one()
            if remaining <= 1:
                raise LastPlatformAdminError(
                    "Cannot demote the last platform-admin; the platform would be "
                    "left with no global operator."
                )
        user.role = role
        await session.commit()
        await session.refresh(user)
        return AdminUserRow(
            id=user.id,
            username=user.username,
            role=user.role,
            tenant_id=user.tenant_id,
            email=user.email,
            is_active=user.is_active,
        )


def _budget_row(b: Budget) -> BudgetRow:
    """Project a :class:`Budget` ORM row onto the wire :class:`BudgetRow`."""
    return BudgetRow(
        id=b.id,
        scope_type=b.scope_type.value,
        scope_id=b.scope_id,
        window=b.window.value,
        token_cap=b.token_cap,
        usd_cap=b.usd_cap,
        rpm=b.rpm,
        tpm=b.tpm,
    )


#: The four cap columns a budget row carries, and how each is spelled to an operator.
#: Derived nowhere and listed once: a fifth cap added to :class:`Budget` without an
#: entry here would be the one field a user could still store above its tenant's.
_CAP_FIELDS: tuple[tuple[str, str], ...] = (
    ("token_cap", "tokens"),
    ("usd_cap", "usd"),
    ("rpm", "count"),
    ("tpm", "count"),
)


def _spell_cap(value: float | int, unit: str) -> str:
    """Render a cap the way an operator reads it on the budgets screen."""
    return f"${value:,.2f}" if unit == "usd" else f"{value:,}"


async def _tenant_cap_row(
    session: AsyncSession, *, tenant_id: int | None, window: BudgetWindow
) -> Budget | None:
    """Return the tenant-scoped cap governing ``window``, or ``None`` if uncapped.

    The comparison is **same-window**, deliberately: a monthly tenant cap and a daily
    user cap are different quantities, and refusing a $50/day user cap because the
    tenant's *month* is capped at $40 would be arithmetic nobody could defend.
    :func:`enforce_governance` measures each row over its own window for the same
    reason.
    """
    if tenant_id is None:
        return None
    return (
        (
            await session.execute(
                select(Budget).where(
                    Budget.scope_type == BudgetScope.TENANT,
                    Budget.scope_id == tenant_id,
                    Budget.window == window,
                )
            )
        )
        .scalars()
        .first()
    )


async def _refuse_user_cap_above_tenant(
    session: AsyncSession,
    *,
    user_id: int,
    window: BudgetWindow,
    caps: dict[str, int | float | None],
    fallback_tenant: int | None,
) -> None:
    """Refuse a user sub-cap that the tenant cap would override, naming that cap.

    The governing tenant is read from the ``users`` row rather than trusted from the
    caller: the API layer resolves it too, but the data layer must not depend on that —
    the same reason :class:`CrossTenantBudgetError` is raised here and not only upstream.
    ``fallback_tenant`` is used only when there is no such user row to read (a cap
    written for a principal the users table does not know).

    Raises:
        UserCapAboveTenantCapError: When any of the four caps is above its tenant's.
    """
    user = await session.get(User, user_id)
    governing = user.tenant_id if user is not None else fallback_tenant
    tenant_row = await _tenant_cap_row(session, tenant_id=governing, window=window)
    if tenant_row is None:
        return
    clauses: list[str] = []
    for field, unit in _CAP_FIELDS:
        asked = caps.get(field)
        binds = getattr(tenant_row, field)
        if asked is not None and binds is not None and asked > binds:
            clauses.append(
                f"{field} {_spell_cap(asked, unit)} is above tenant {governing}'s "
                f"{_spell_cap(binds, unit)}"
            )
    if not clauses:
        return
    raise UserCapAboveTenantCapError(
        f"{'; '.join(clauses)} for the {window.value} window. A user sub-cap can never "
        "exceed the cap on its own tenant — the tenant's figure is what binds — so the "
        "larger number would be stored, shown back to you, and never reached. Lower it "
        "to the tenant cap, or raise the tenant cap first."
    )


async def _narrow_user_caps_to_tenant(
    session: AsyncSession,
    *,
    tenant_id: int,
    window: BudgetWindow,
    caps: dict[str, int | float | None],
) -> None:
    """Pull this tenant's user sub-caps down to a tenant cap that has just been lowered.

    **The ordering hazard, decided.** A write-time refusal on the *user* path alone
    leaves the same lie reachable from the *tenant* path: set a $500 user cap under a
    $1000 tenant cap (legal), then lower the tenant to $50, and the $500 row is
    back — stored, displayed, and not what binds. The alternatives were to refuse the
    tenant write (which would make a tenant admin's own sub-caps a lock on tightening
    their tenant, exactly backwards) or to leave the rows alone (the lie). So a tenant
    cap that moves **takes its sub-caps with it**, in the same transaction as the write
    that moved it: after any tenant write, every user sub-cap of that tenant is at most
    the tenant's own, which is the invariant the refusal above enforces from the other
    side.

    Only *downward*: raising a tenant cap leaves sub-caps where their admin set them.
    """
    rows = (
        (
            await session.execute(
                select(Budget)
                .outerjoin(User, User.id == Budget.scope_id)
                .where(
                    Budget.scope_type == BudgetScope.USER,
                    Budget.window == window,
                    or_(Budget.tenant_id == tenant_id, User.tenant_id == tenant_id),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        narrowed: list[str] = []
        for field, unit in _CAP_FIELDS:
            binds = caps.get(field)
            stored = getattr(row, field)
            if binds is not None and stored is not None and stored > binds:
                setattr(row, field, binds)
                narrowed.append(
                    f"{field} {_spell_cap(stored, unit)} -> {_spell_cap(binds, unit)}"
                )
        if narrowed:
            logger.warning(
                "Tenant %s's %s cap was lowered beneath user %s's sub-cap; the sub-cap "
                "was narrowed with it (%s) so the stored figure stays the figure that "
                "binds.",
                tenant_id,
                window.value,
                row.scope_id,
                ", ".join(narrowed),
            )


async def upsert_budget(
    *,
    scope_type: str,
    scope_id: int,
    window: str = "day",
    token_cap: int | None = None,
    usd_cap: float | None = None,
    rpm: int | None = None,
    tpm: int | None = None,
    tenant_id: int | None = None,
) -> BudgetRow:
    """Create or update the cap for a ``(scope_type, scope_id, window)`` triple.

    Idempotent on the natural key so the admin UI can re-post the same scope+window
    to adjust caps rather than accumulate duplicate rows. ``tenant_id`` stamps the
    owning tenant for tenant-scoped listing/isolation.

    **Cross-tenant writes are refused here, not only upstream.** The lookup matched on
    ``(scope_type, scope_id, window)`` with no tenant predicate and then assigned
    ``existing.tenant_id = tenant_id`` unconditionally, so a caller scoped to tenant B
    could land on tenant A's row, overwrite A's caps and re-stamp the row as B's — a
    silent takeover of another tenant's spend limit. The API layer authorises the
    write, but the data layer must not depend on that: the natural-key lookup is now
    tenant-checked and a mismatch raises :class:`CrossTenantBudgetError` instead of
    writing.

    The lookup stays on the *full* natural key rather than being narrowed to the
    tenant: narrowing it would silently insert a second row for the same
    scope+window when a conflict exists, and the enforcement reader
    (:func:`_budgets_for`) would then pick between duplicates arbitrarily. Detecting
    the conflict and refusing is the safe behaviour.

    A platform-admin caller (``tenant_id=None``) may write any row, and an existing
    row with no owner may be claimed — only two *different, non-null* tenants
    collide.

    Returns:
        The persisted :class:`BudgetRow`.

    Raises:
        CrossTenantBudgetError: When the ``(scope_type, scope_id, window)`` row is
            already owned by a different tenant.
    """
    scope = BudgetScope(scope_type)
    win = BudgetWindow(window)
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        existing = (
            await session.execute(
                select(Budget).where(
                    Budget.scope_type == scope,
                    Budget.scope_id == scope_id,
                    Budget.window == win,
                )
            )
        ).scalars().first()
        if (
            existing is not None
            and tenant_id is not None
            and existing.tenant_id is not None
            and existing.tenant_id != tenant_id
        ):
            raise CrossTenantBudgetError(
                f"budget {scope_type}:{scope_id}/{window} is owned by tenant "
                f"{existing.tenant_id}; tenant {tenant_id} may not overwrite it"
            )
        caps: dict[str, int | float | None] = {
            "token_cap": token_cap,
            "usd_cap": usd_cap,
            "rpm": rpm,
            "tpm": tpm,
        }
        if scope is BudgetScope.USER:
            # Refuse a figure that could never bind, BEFORE it is stored and read back.
            await _refuse_user_cap_above_tenant(
                session,
                user_id=scope_id,
                window=win,
                caps=caps,
                fallback_tenant=tenant_id,
            )
        if existing is None:
            existing = Budget(scope_type=scope, scope_id=scope_id, window=win)
            session.add(existing)
        # A platform-admin write (``tenant_id is None``) must not erase an existing
        # owner stamp — that would orphan the row out of its tenant's listing.
        if tenant_id is not None or existing.tenant_id is None:
            existing.tenant_id = tenant_id
        existing.token_cap = token_cap
        existing.usd_cap = usd_cap
        existing.rpm = rpm
        existing.tpm = tpm
        if scope is BudgetScope.TENANT:
            # The other half of the same invariant: a tenant cap that moves down takes
            # every sub-cap it now overrides with it, in this transaction.
            await _narrow_user_caps_to_tenant(
                session, tenant_id=scope_id, window=win, caps=caps
            )
        await session.commit()
        await session.refresh(existing)
        return _budget_row(existing)


def _bucket_hour(ts: datetime | None) -> str:
    """Truncate a timestamp to the top of its UTC hour (ISO string) for the series."""
    if ts is None:
        ts = _now_naive()
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()


async def savings_buckets(tenant_id: int | None = None) -> dict[str, dict[str, float]]:
    """Split one tenant's whole usage ledger into token-work and non-token-work.

    The savings figure is ``baseline − actual``, and the baseline is only meaningful
    for **token** work: a frontier *chat* model cannot do an audio minute or an image
    at all, so those rows have no cheaper-or-dearer alternative to price against (see
    :func:`aegis.gateway.llm._baseline_cost`). Splitting them here lets the caller
    price each bucket by its own rule without this module importing the gateway's
    pricing tables.

    Args:
        tenant_id: When given, app-scope the read to one tenant (over RLS). ``None``
            aggregates every tenant and is for platform-wide callers only.

    Returns:
        ``{"token": {...}, "other": {...}}``, each with ``prompt_tokens``,
        ``completion_tokens`` and ``cost_usd``. Both buckets are always present, at
        zero when empty, so a caller never branches on absence.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        stmt = select(UsageLedger)
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        rows = (await session.execute(stmt)).scalars().all()

    buckets = {
        "token": {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0},
        "other": {"prompt_tokens": 0.0, "completion_tokens": 0.0, "cost_usd": 0.0},
    }
    for r in rows:
        key = "other" if (r.audio_seconds > 0.0 or r.images > 0) else "token"
        buckets[key]["prompt_tokens"] += r.prompt_tokens
        buckets[key]["completion_tokens"] += r.completion_tokens
        buckets[key]["cost_usd"] += r.cost_usd
    return buckets


async def token_usage_by_model(tenant_id: int | None = None) -> dict[str, dict[str, float]]:
    """Return this scope's **token** work broken down by the deployment that served it.

    The evidence :func:`savings_buckets` cannot carry. That function splits the ledger
    by *billing unit*, which is enough to price a baseline but not enough to justify
    calling the result a saving: the savings figure subtracts a frontier baseline from
    actual spend and attributes the difference to small-model routing, and that reading
    holds only if a model other than the baseline's actually answered some of the
    calls. Which models answered is a fact about the ledger, so it is read from the
    ledger rather than inferred from the routing table — a role can point at a
    deployment that never serves a single call, and on a half-migrated fleet that is
    the normal case, not the exotic one.

    Naming the model per row also separates work that *has* a cheaper-or-dearer
    alternative from work that does not. An embedding is token-billed and so lands in
    the same bucket as a chat turn, but no frontier chat model is an alternative way to
    embed; pricing its tokens at the chat baseline books a saving against a choice
    nobody could have made. The caller classifies, because the classification needs the
    gateway's routing table and this module deliberately does not import it.

    Rows with audio or image units are excluded, as in :func:`savings_buckets`: they
    are not token work.

    Args:
        tenant_id: When given, app-scope the read to one tenant (over RLS). ``None``
            aggregates every tenant and is for platform-wide callers only.

    Returns:
        ``{deployment_id: {"calls", "prompt_tokens", "completion_tokens", "cost_usd"}}``,
        empty when the scope has spent nothing. Rows with no recorded model are skipped
        rather than folded under a placeholder, so a caller never mistakes "unknown"
        for a distinct model.
    """
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        stmt = select(
            UsageLedger.model,
            func.count(),
            func.sum(UsageLedger.prompt_tokens),
            func.sum(UsageLedger.completion_tokens),
            func.sum(UsageLedger.cost_usd),
        ).where(
            UsageLedger.audio_seconds <= 0.0,
            UsageLedger.images <= 0,
            UsageLedger.model.is_not(None),
        )
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        rows = (await session.execute(stmt.group_by(UsageLedger.model))).all()

    return {
        model: {
            "calls": float(calls or 0),
            "prompt_tokens": float(prompt or 0),
            "completion_tokens": float(completion or 0),
            "cost_usd": float(cost or 0.0),
        }
        for model, calls, prompt, completion, cost in rows
        if model
    }


async def usage_rollup(
    tenant_id: int | None = None, window: str = "day"
) -> tuple[int, int, float, list[UsageByModel], list[UsageSeriesPoint]]:
    """Roll up ledger spend for the ``/admin/usage`` dashboard.

    Args:
        tenant_id: When given, app-scope the rollup to one tenant (over RLS).
        window: ``"day"`` | ``"month"`` — the rolling span to aggregate over.

    Returns:
        ``(total_prompt_tokens, total_completion_tokens, total_cost_usd, by_model,
        series)``. ``by_model`` is per-model spend; ``series`` is hourly cost buckets
        oldest-first (Python-side bucketing keeps the query portable across dialects).
    """
    win = BudgetWindow(window)
    since = _now_naive() - timedelta(seconds=WINDOW_SECONDS[win])
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        stmt = select(UsageLedger).where(UsageLedger.ts >= since)
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        rows = (await session.execute(stmt.order_by(UsageLedger.ts.asc()))).scalars().all()

    total_pt = total_ct = 0
    total_cost = 0.0
    by_model_tokens: dict[str, int] = defaultdict(int)
    by_model_cost: dict[str, float] = defaultdict(float)
    series_cost: dict[str, float] = defaultdict(float)
    for r in rows:
        total_pt += r.prompt_tokens
        total_ct += r.completion_tokens
        total_cost += r.cost_usd
        key = r.model or "unknown"
        by_model_tokens[key] += r.prompt_tokens + r.completion_tokens
        by_model_cost[key] += r.cost_usd
        series_cost[_bucket_hour(r.ts)] += r.cost_usd

    by_model = [
        UsageByModel(model=m, cost_usd=by_model_cost[m], tokens=by_model_tokens[m])
        for m in sorted(by_model_cost, key=lambda m: by_model_cost[m], reverse=True)
    ]
    series = [
        UsageSeriesPoint(ts=ts, cost_usd=series_cost[ts])
        for ts in sorted(series_cost)
    ]
    return total_pt, total_ct, total_cost, by_model, series
