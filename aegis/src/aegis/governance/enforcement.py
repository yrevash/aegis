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

from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
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
    "DuplicateTenantError",
    "DuplicateUserError",
    "LastPlatformAdminError",
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


# The rolling span, in seconds, for each budget window (token/usd caps).
_WINDOW_SECONDS: dict[BudgetWindow, int] = {
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
    """
    caps = [c for c in (user_cap, tenant_cap) if c is not None]
    return min(caps) if caps else None


async def _budgets_for(
    session: AsyncSession, *, tenant_id: int | None, user_id: int | None
) -> list[Budget]:
    """Return the tenant- and user-scoped budget rows for a principal (user first)."""
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


async def effective_limits(
    tenant_id: int | None, user_id: int | None
) -> GovernanceLimits:
    """Resolve the nearest-binding caps for a principal (user clamped to tenant).

    Args:
        tenant_id: The tenant the principal belongs to.
        user_id: The acting user.

    Returns:
        The merged :class:`GovernanceLimits` for the per-request context; every field
        is uncapped (``None``) when no budget row governs it.
    """
    if tenant_id is None:
        return GovernanceLimits()
    async with _session() as session:
        await _set_tenant_scope(session, tenant_id)
        budgets = await _budgets_for(session, tenant_id=tenant_id, user_id=user_id)
    tenant = next((b for b in budgets if b.scope_type is BudgetScope.TENANT), None)
    user = next((b for b in budgets if b.scope_type is BudgetScope.USER), None)
    t = tenant or Budget()
    u = user or Budget()
    return GovernanceLimits(
        token_cap=_clamp_inward(u.token_cap, t.token_cap),
        usd_cap=_clamp_inward(u.usd_cap, t.usd_cap),
        rpm=_clamp_inward(u.rpm, t.rpm),
        tpm=_clamp_inward(u.tpm, t.tpm),
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
                seconds=_WINDOW_SECONDS.get(b.window, _WINDOW_SECONDS[BudgetWindow.DAY])
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
) -> None:
    """Write one durable usage-ledger row for a governed model call."""
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
                cost_usd=cost_usd,
                trace_id=trace_id,
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


async def create_tenant(name: str) -> TenantRow:
    """Create a tenant (client) and return its row (platform-admin surface).

    Tenant names are unique; a clash raises :class:`DuplicateTenantError` (the API
    surface maps it to a 409) rather than a raw driver error. New tenants default to
    the ``active`` status.

    Raises:
        DuplicateTenantError: If a tenant with this name already exists.
    """
    async with _session() as session:
        tenant = Tenant(name=name)
        session.add(tenant)
        try:
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


async def user_tenant_id(user_id: int) -> int | None:
    """Return the tenant a user belongs to, or ``None`` if the user is unknown.

    Used to authorise a user-scoped budget write: the caller's tenant must own the
    target user. A ``None`` return means no such user, which the API surface treats
    as a 404 rather than silently allowing a cross-tenant cap.
    """
    async with _session() as session:
        user = await session.get(User, user_id)
        return user.tenant_id if user is not None else None


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
    owning tenant for tenant-scoped listing/isolation; the API resolves and authorises
    it before the write.

    Returns:
        The persisted :class:`BudgetRow`.
    """
    scope = BudgetScope(scope_type)
    win = BudgetWindow(window)
    async with _session() as session:
        existing = (
            await session.execute(
                select(Budget).where(
                    Budget.scope_type == scope,
                    Budget.scope_id == scope_id,
                    Budget.window == win,
                )
            )
        ).scalars().first()
        if existing is None:
            existing = Budget(scope_type=scope, scope_id=scope_id, window=win)
            session.add(existing)
        existing.tenant_id = tenant_id
        existing.token_cap = token_cap
        existing.usd_cap = usd_cap
        existing.rpm = rpm
        existing.tpm = tpm
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
    since = _now_naive() - timedelta(seconds=_WINDOW_SECONDS[win])
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
