"""Dashboard-data accessors + effective config: consistency, isolation, ordering.

These lock the three Phase-1 governance guarantees the later UI/tests depend on:

- **Data consistency** — every dashboard figure equals the underlying authoritative
  rows: the usage summary totals equal ``sum(UsageLedger)`` and each budget's
  ``tokens_used`` equals the same ledger sum the enforcer compares against a cap.
- **Tenant isolation** — a tenant-A-scoped accessor never returns tenant-B rows.
- **Config / RBAC ladder** — the effective config surfaces the knobs and the RBAC
  ladder is strictly ordered by administrative privilege.

They run against the offline SQLite ``db`` fixture from ``conftest.py`` (RLS is a
documented no-op there; app-level scoping is the layer under test).
"""

from __future__ import annotations

from sqlalchemy import func, select

from aegis.governance import (
    MEMBER,
    PLATFORM_ADMIN,
    TENANT_ADMIN,
    Budget,
    BudgetScope,
    BudgetWindow,
    Role,
    Tenant,
    UsageLedger,
    User,
    budget_status,
    effective_config,
    enforce_governance,
    governance_dashboard,
    record_audit,
    role_rank,
    usage_summary,
)
from aegis.governance.config import RBAC_LADDER
from aegis.governance.security import DEFAULT_JWT_SECRET


async def _seed(db, *rows):
    async with db() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def _ledger_totals(db, tenant_id=None):
    """Ground truth: sum the UsageLedger straight from the rows."""
    async with db() as session:
        stmt = select(
            func.coalesce(func.sum(UsageLedger.prompt_tokens), 0),
            func.coalesce(func.sum(UsageLedger.completion_tokens), 0),
            func.coalesce(func.sum(UsageLedger.cost_usd), 0.0),
            func.count(UsageLedger.id),
        )
        if tenant_id is not None:
            stmt = stmt.where(UsageLedger.tenant_id == tenant_id)
        row = (await session.execute(stmt)).one()
    return int(row[0]), int(row[1]), float(row[2]), int(row[3])


# ── usage summary: figures EQUAL the ledger rows (data consistency) ──────────


async def test_usage_summary_totals_equal_ledger_sum(db):
    await _seed(
        db,
        UsageLedger(tenant_id=1, model="gpt", prompt_tokens=10, completion_tokens=5, cost_usd=0.5),
        UsageLedger(tenant_id=1, model="gpt", prompt_tokens=7, completion_tokens=3, cost_usd=0.2),
        UsageLedger(tenant_id=1, model="claude", prompt_tokens=4, completion_tokens=1, cost_usd=0.1),  # noqa: E501
    )
    pt, ct, cost, calls = await _ledger_totals(db, tenant_id=1)
    summary = await usage_summary(tenant_id=1)
    assert summary.total_prompt_tokens == pt
    assert summary.total_completion_tokens == ct
    assert summary.total_tokens == pt + ct
    assert round(summary.total_cost_usd, 6) == round(cost, 6)
    assert summary.calls == calls == 3
    # by_model splits also sum back to the ledger total.
    assert sum(m.tokens for m in summary.by_model) == pt + ct
    assert round(sum(m.cost_usd for m in summary.by_model), 6) == round(cost, 6)


async def test_usage_summary_empty_is_all_zero(db):
    summary = await usage_summary(tenant_id=1)
    assert (summary.total_prompt_tokens, summary.total_completion_tokens) == (0, 0)
    assert summary.total_cost_usd == 0.0
    assert summary.calls == 0
    assert summary.by_model == [] and summary.series == []


# ── budget status: spend EQUALS the enforcer's ledger sum (one source) ───────


async def test_budget_status_spend_equals_ledger_and_remaining(db):
    await _seed(
        db,
        Budget(
            tenant_id=1,
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            token_cap=100,
            usd_cap=1.0,
        ),
        UsageLedger(tenant_id=1, prompt_tokens=30, completion_tokens=10, cost_usd=0.4),
        UsageLedger(tenant_id=1, prompt_tokens=5, completion_tokens=5, cost_usd=0.1),
    )
    [status] = await budget_status(tenant_id=1)
    pt, ct, cost, _calls = await _ledger_totals(db, tenant_id=1)
    # tokens_used is the exact ledger sum over the window.
    assert status.tokens_used == pt + ct == 50
    assert round(status.cost_usd_used, 6) == round(cost, 6) == 0.5
    # remaining = cap − used (floored at 0).
    assert status.tokens_remaining == 100 - 50
    assert round(status.usd_remaining, 6) == round(1.0 - 0.5, 6)
    assert status.budget.token_cap == 100


async def test_budget_status_matches_enforcer_view(db):
    # The dashboard spend and the enforcer read the SAME ledger sum: at the cap the
    # dashboard shows zero remaining and the enforcer blocks — they never disagree.
    import pytest

    from aegis.gateway.types import BudgetExceededError

    await _seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=40),
        UsageLedger(tenant_id=1, prompt_tokens=30, completion_tokens=10, cost_usd=0.0),
    )
    [status] = await budget_status(tenant_id=1)
    assert status.tokens_used == 40
    assert status.tokens_remaining == 0  # exactly at the cap
    with pytest.raises(BudgetExceededError):
        await enforce_governance(tenant_id=1, user_id=None)


async def test_budget_status_uncapped_remaining_is_none(db):
    await _seed(db, Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1))
    [status] = await budget_status(tenant_id=1)
    assert status.tokens_remaining is None
    assert status.usd_remaining is None


async def test_budget_status_over_cap_floors_remaining_at_zero(db):
    await _seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=10),
        UsageLedger(tenant_id=1, prompt_tokens=50, completion_tokens=0, cost_usd=0.0),
    )
    [status] = await budget_status(tenant_id=1)
    assert status.tokens_used == 50  # overage is still visible via used…
    assert status.tokens_remaining == 0  # …but remaining never goes negative.


# ── tenant isolation on the accessors (A cannot read B) ──────────────────────


async def test_usage_summary_is_tenant_scoped(db):
    await _seed(
        db,
        UsageLedger(tenant_id=1, prompt_tokens=10, completion_tokens=0, cost_usd=1.0),
        UsageLedger(tenant_id=2, prompt_tokens=999, completion_tokens=0, cost_usd=99.0),
    )
    a = await usage_summary(tenant_id=1)
    # Tenant A sees only its own 10 tokens / $1 — never tenant B's 999 / $99.
    assert a.total_tokens == 10
    assert a.total_cost_usd == 1.0


async def test_budget_status_is_tenant_scoped(db):
    await _seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
        Budget(tenant_id=2, scope_type=BudgetScope.TENANT, scope_id=2, token_cap=200),
    )
    rows = await budget_status(tenant_id=1)
    assert [r.budget.scope_id for r in rows] == [1]  # tenant B's cap is not returned


async def test_governance_dashboard_is_tenant_scoped(db):
    async with db() as session:
        session.add_all(
            [
                Tenant(name="acme"),
                Tenant(name="globex"),
                User(username="a-user", role=Role.CLIENT, tenant_id=1),
                User(username="b-user", role=Role.CLIENT, tenant_id=2),
                Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
                Budget(tenant_id=2, scope_type=BudgetScope.TENANT, scope_id=2, token_cap=200),
                UsageLedger(tenant_id=1, prompt_tokens=10, completion_tokens=0, cost_usd=1.0),
                UsageLedger(tenant_id=2, prompt_tokens=50, completion_tokens=0, cost_usd=5.0),
            ]
        )
        await session.commit()
    await record_audit(
        action="admin.test", actor="a", model=None, trace_id=None, payload={}, tenant_id=1
    )
    await record_audit(
        action="admin.test", actor="b", model=None, trace_id=None, payload={}, tenant_id=2
    )

    dash = await governance_dashboard(tenant_id=1)
    assert [t.id for t in dash.tenants] == [1]
    assert [u.username for u in dash.users] == ["a-user"]
    assert [b.budget.scope_id for b in dash.budgets] == [1]
    assert dash.usage.total_tokens == 10  # not tenant B's 50
    assert all(r.actor == "a" for r in dash.recent_audit)


async def test_governance_dashboard_platform_view_sees_all(db):
    async with db() as session:
        session.add_all([Tenant(name="acme"), Tenant(name="globex")])
        await session.commit()
    dash = await governance_dashboard(tenant_id=None)
    assert {t.name for t in dash.tenants} == {"acme", "globex"}


async def test_governance_dashboard_usage_equals_ledger_sum(db):
    await _seed(
        db,
        UsageLedger(tenant_id=1, prompt_tokens=12, completion_tokens=8, cost_usd=2.0),
        UsageLedger(tenant_id=1, prompt_tokens=3, completion_tokens=1, cost_usd=0.5),
    )
    pt, ct, cost, calls = await _ledger_totals(db, tenant_id=1)
    dash = await governance_dashboard(tenant_id=1)
    assert dash.usage.total_tokens == pt + ct
    assert round(dash.usage.total_cost_usd, 6) == round(cost, 6)
    assert dash.usage.calls == calls


# ── effective config: the knobs + the RBAC ladder ordering ───────────────────


def test_effective_config_surfaces_the_knobs():
    cfg = effective_config()
    # JWT knobs (never the secret itself).
    assert cfg.jwt.algorithm == "HS256"
    assert cfg.jwt.expire_minutes == 720
    assert isinstance(cfg.jwt.secret_is_dev_default, bool)
    assert "secret" not in cfg.jwt.model_dump()  # the secret is not surfaced
    # Budget-window knobs.
    assert cfg.budgets.default_window == "day"
    assert cfg.budgets.day_window_seconds == 24 * 3600
    assert cfg.budgets.month_window_seconds == 30 * 24 * 3600
    assert cfg.budgets.rate_window_seconds == 60
    # RLS posture: Postgres-only, the governed tables, and NOT fail-closed.
    #
    # This assertion was inverted deliberately. It previously pinned
    # ``fail_closed is True`` while the installed predicate admits every row when
    # the tenant GUC is unbound — so the test was holding a false assurance in
    # place, and that value is rendered as a green "fail-closed" badge on the
    # console's Security page. A bound numeric scope IS strictly enforced; an
    # unbound one is not. Reporting the weaker truth is the point.
    #
    # Flip this back to True only when the auth path binds a scope before querying
    # ``users`` and the predicate is tightened to match.
    assert cfg.rls.fail_closed is False
    assert cfg.rls.enforced_on == "postgresql"
    assert "users" in cfg.rls.tables and "usage_ledger" in cfg.rls.tables


def test_effective_config_reflects_injected_security():
    from aegis.governance import configure_security

    try:
        configure_security(
            "rotated-secret-0123456789abcdef0123456789abcdef",
            jwt_expire_minutes=45,
        )
        cfg = effective_config()
        assert cfg.jwt.expire_minutes == 45
        assert cfg.jwt.secret_is_dev_default is False
    finally:
        configure_security(DEFAULT_JWT_SECRET)  # restore for later tests


def test_effective_config_default_secret_is_flagged():
    from aegis.governance import configure_security

    configure_security(DEFAULT_JWT_SECRET)
    assert effective_config().jwt.secret_is_dev_default is True


def test_rbac_ladder_is_strictly_ordered_by_privilege():
    # platform_admin > tenant_admin > (ai_team == devops) > client.
    assert role_rank(PLATFORM_ADMIN) > role_rank(TENANT_ADMIN)
    assert role_rank(TENANT_ADMIN) > role_rank("ai_team")
    assert role_rank("ai_team") == role_rank("devops")  # peer operational tiers
    assert role_rank("devops") > role_rank("client")
    # The legacy MEMBER ("user") alias ranks as client; unknown tiers fail closed at 0.
    assert role_rank(MEMBER) == role_rank("client")
    assert role_rank("nonexistent-role") == 0


def test_rbac_ladder_data_is_consistent():
    fine = [t.fine_role for t in RBAC_LADDER]
    assert fine == [PLATFORM_ADMIN, TENANT_ADMIN, "ai_team", "devops", "client"]
    # Only platform_admin is un-pinned from a tenant; the admin sub-tiers are coarse admin.
    by_fine = {t.fine_role: t for t in RBAC_LADDER}
    assert by_fine[PLATFORM_ADMIN].tenant_scoped is False
    assert by_fine[TENANT_ADMIN].tenant_scoped is True
    assert by_fine[PLATFORM_ADMIN].coarse_role == "admin"
    assert by_fine[TENANT_ADMIN].coarse_role == "admin"
    # effective_config carries the very same ladder.
    assert effective_config().role_ladder == list(RBAC_LADDER)
