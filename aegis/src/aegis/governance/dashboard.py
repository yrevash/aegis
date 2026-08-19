"""Read-only governance dashboard accessors — the UI/test data plane.

The later governance UI (and the focused tests) need the dashboard *figures* as
plain data, not a re-implementation of the maths behind them. These accessors return
exactly that, and they are **single-sourced**: every spend figure is derived from the
durable :class:`~aegis.governance.models.UsageLedger` via the *same* summation
:func:`aegis.governance.enforcement.enforce_governance` runs at the gateway chokepoint
(:func:`~aegis.governance.enforcement._usage_sums`), so a dashboard number always
equals what the enforcer sees. The caps come from the same :class:`Budget` rows the
enforcer reads.

Every accessor is **tenant-scoped and RBAC-safe by construction**: passing a
``tenant_id`` filters the authoritative rows to that tenant (and applies the RLS scope
binder), so a tenant's dashboard never contains another tenant's data. ``tenant_id=None``
is the platform-admin view (all tenants) — the API surface gates who may pass ``None``.

Like the rest of the data layer, each function opens its own short-lived session from
the injected factory (see :func:`aegis.governance.configure_governance`).
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from aegis.governance.audit import list_recent_audit
from aegis.governance.enforcement import (
    WINDOW_SECONDS,
    _budget_row,
    _now_naive,
    _session,
    _usage_sums,
    apply_tenant_scope,
    list_tenants,
    list_users,
    usage_rollup,
)
from aegis.governance.models import Budget, BudgetScope, BudgetWindow, UsageLedger
from aegis.governance.types import (
    BudgetStatusRow,
    GovernanceDashboard,
    UsageSummary,
)

__all__ = [
    "budget_status",
    "governance_dashboard",
    "usage_summary",
]


async def budget_status(
    tenant_id: int | None = None,
    *,
    scope_type: str | None = None,
    scope_id: int | None = None,
) -> list[BudgetStatusRow]:
    """Return every governing budget cap joined with its live spend over its window.

    The spend (tokens / usd / calls) for each cap is summed from the ledger with the
    identical query the gateway enforcer uses, so ``BudgetStatusRow.tokens_used`` equals
    the value :func:`~aegis.governance.enforce_governance` would compare against the cap.

    Args:
        tenant_id: App-scope to one tenant's caps (belt-and-suspenders over RLS);
            ``None`` returns every tenant's caps (the platform-admin view).
        scope_type: Filter to ``tenant``/``user`` caps when given.
        scope_id: Filter to a single scope id when given.

    Returns:
        One :class:`BudgetStatusRow` per matching cap, ordered by id ascending.
    """
    async with _session() as session:
        await apply_tenant_scope(session, tenant_id)
        stmt = select(Budget).order_by(Budget.id.asc())
        if tenant_id is not None:
            stmt = stmt.where(Budget.tenant_id == tenant_id)
        if scope_type is not None:
            stmt = stmt.where(Budget.scope_type == BudgetScope(scope_type))
        if scope_id is not None:
            stmt = stmt.where(Budget.scope_id == scope_id)
        budgets = (await session.execute(stmt)).scalars().all()

        now = _now_naive()
        out: list[BudgetStatusRow] = []
        for b in budgets:
            scope_col = (
                UsageLedger.tenant_id
                if b.scope_type is BudgetScope.TENANT
                else UsageLedger.user_id
            )
            since = now - timedelta(
                seconds=WINDOW_SECONDS.get(b.window, WINDOW_SECONDS[BudgetWindow.DAY])
            )
            tokens, cost, calls = await _usage_sums(
                session, scope_col=scope_col, scope_id=b.scope_id, since=since
            )
            out.append(
                BudgetStatusRow(
                    budget=_budget_row(b),
                    tokens_used=tokens,
                    cost_usd_used=cost,
                    calls=calls,
                    tokens_remaining=(
                        None if b.token_cap is None else max(b.token_cap - tokens, 0)
                    ),
                    usd_remaining=(
                        None if b.usd_cap is None else max(b.usd_cap - cost, 0.0)
                    ),
                )
            )
    return out


async def usage_summary(
    tenant_id: int | None = None, window: str = "day"
) -> UsageSummary:
    """Return the ledger-rolled usage summary (calls / tokens / cost) for a scope.

    Wraps :func:`~aegis.governance.usage_rollup` (the single source for the totals,
    per-model split and hourly series) and adds the call count over the same window,
    so ``total_prompt_tokens + total_completion_tokens == sum(ledger)`` by construction.

    Args:
        tenant_id: App-scope the rollup to one tenant; ``None`` rolls up the platform.
        window: ``"day"`` | ``"month"`` — the rolling span to aggregate over.
    """
    pt, ct, cost, by_model, series = await usage_rollup(tenant_id, window)
    win = BudgetWindow(window)
    since = _now_naive() - timedelta(seconds=WINDOW_SECONDS[win])
    async with _session() as session:
        await apply_tenant_scope(session, tenant_id)
        stmt = select(func.count(UsageLedger.id)).where(UsageLedger.ts >= since)
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        calls = int((await session.execute(stmt)).scalar_one() or 0)
    return UsageSummary(
        tenant_id=tenant_id,
        window=window,
        total_prompt_tokens=pt,
        total_completion_tokens=ct,
        total_tokens=pt + ct,
        total_cost_usd=cost,
        calls=calls,
        by_model=by_model,
        series=series,
    )


async def governance_dashboard(
    tenant_id: int | None = None,
    *,
    window: str = "day",
    audit_limit: int = 50,
) -> GovernanceDashboard:
    """Assemble the full governance dashboard snapshot for a tenant scope.

    Combines the tenants list, per-cap budget/spend/remaining, users + roles, the usage
    summary and the recent audit tail — every part tenant-scoped when ``tenant_id`` is
    set, so a tenant's dashboard never leaks another tenant's rows.

    Args:
        tenant_id: The tenant to scope every figure to; ``None`` is the platform view.
        window: ``"day"`` | ``"month"`` — the usage rollup window.
        audit_limit: Maximum audit rows to include (newest first).
    """
    tenants = await list_tenants()
    if tenant_id is not None:
        tenants = [t for t in tenants if t.id == tenant_id]
    return GovernanceDashboard(
        tenant_id=tenant_id,
        window=window,
        tenants=tenants,
        budgets=await budget_status(tenant_id),
        users=await list_users(tenant_id),
        usage=await usage_summary(tenant_id, window),
        recent_audit=await list_recent_audit(audit_limit, tenant_id=tenant_id),
    )
