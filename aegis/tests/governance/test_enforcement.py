"""Budget/rate enforcement, the usage ledger, and the admin rollups.

These exercise ``aegis.governance.enforcement`` directly against the private PostgreSQL
database bound by the ``db`` fixture, so the budget reads, ledger writes, role updates
and admin queries all round-trip with no host and no network.

**Every budget row here carries an owning ``tenant_id``, and that is load-bearing.**
The reads under test bind the tenant scope (``_set_tenant_scope``), so the
``tenant_isolation`` policy restricts ``budgets`` to rows whose ``tenant_id`` equals it —
and ``NULL = 1`` is NULL, not true, so an unowned cap is invisible to the tenant it was
meant to cap. On SQLite that policy did nothing, so tests seeding ``Budget(...)`` with no
owner still saw their cap; on PostgreSQL those tests were passing *because the cap had
vanished*, which is the opposite of what they claim to prove. ``tenant_id`` is exactly
what the model documents it to be: ``scope_id`` for a tenant cap, the target user's tenant
for a user cap.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from aegis.gateway.types import BudgetExceededError
from aegis.governance import (
    Budget,
    BudgetScope,
    BudgetWindow,
    CrossTenantBudgetError,
    LastPlatformAdminError,
    Role,
    Tenant,
    UsageLedger,
    User,
    UserCapAboveTenantCapError,
    effective_limits,
    enforce_governance,
    enforcement,
    list_budgets,
    list_tenants,
    list_users,
    record_usage,
    update_user_role,
    upsert_budget,
    usage_rollup,
    user_tenant_id,
)

from .._seed import ensure_tenants, ensure_users, seed

# ── enforcement ──────────────────────────────────────────────────────────────


async def test_over_token_budget_raises(db):
    await seed(
        db,
        Budget(
            tenant_id=1,
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            token_cap=100,
        ),
        UsageLedger(tenant_id=1, prompt_tokens=100, completion_tokens=50, cost_usd=0.1),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.scope == "tenant"
    assert ei.value.limit_type == "token_cap"
    assert ei.value.limit == 100


async def test_user_cap_binds_before_tenant(db):
    # Both caps tripped; the user cap is checked first and attributed to the user.
    await seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
        Budget(tenant_id=1, scope_type=BudgetScope.USER, scope_id=2, token_cap=10),
        UsageLedger(tenant_id=1, user_id=2, prompt_tokens=20, completion_tokens=0),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.scope == "user"


async def test_under_budget_passes(db):
    # The owner stamp matters even here: without it the cap is hidden by the tenant
    # policy and this test would pass by proving nothing at all.
    await seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100_000),
    )
    # No breach → returns cleanly.
    await enforce_governance(tenant_id=1, user_id=2)


async def test_rpm_cap_raises_on_recent_calls(db):
    await seed(
        db,
        Budget(tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, rpm=1),
        UsageLedger(tenant_id=1, prompt_tokens=1, completion_tokens=1),
    )
    with pytest.raises(BudgetExceededError) as ei:
        await enforce_governance(tenant_id=1, user_id=2)
    assert ei.value.limit_type == "rpm"


async def test_ungoverned_call_is_a_noop(db):
    # No tenant bound → enforcement is a full no-op (never touches the DB path).
    await enforce_governance(tenant_id=None, user_id=None)


async def test_record_usage_writes_ledger_row(db):
    # The ledger's tenant/user columns are real foreign keys: an unattributable spend row
    # is exactly what the ledger exists to make impossible.
    await ensure_users(db, u2=1)
    await record_usage(
        tenant_id=1,
        user_id=2,
        model="m",
        prompt_tokens=11,
        completion_tokens=7,
        cost_usd=0.0002,
        trace_id="t-1",
    )
    async with db() as session:
        rows = (
            await session.execute(select(UsageLedger).where(UsageLedger.tenant_id == 1))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == 2
    assert rows[0].prompt_tokens == 11
    assert rows[0].completion_tokens == 7


# ── effective limits (inward clamp) ─────────────────────────────────────────


async def test_effective_limits_clamp_user_inward_to_tenant(db):
    await seed(
        db,
        Budget(
            tenant_id=1, scope_type=BudgetScope.TENANT, scope_id=1, token_cap=1000, rpm=100
        ),
        Budget(tenant_id=1, scope_type=BudgetScope.USER, scope_id=2, token_cap=10),
    )
    limits = await effective_limits(1, 2)
    # User cap (10) binds over the looser tenant cap (1000); rpm only set at tenant.
    assert limits.token_cap == 10
    assert limits.rpm == 100


async def test_effective_limits_unscoped_is_uncapped(db):
    limits = await effective_limits(None, None)
    assert limits.token_cap is None and limits.usd_cap is None


# ── admin rollups ───────────────────────────────────────────────────────────


async def test_upsert_budget_is_idempotent_on_the_natural_key(db):
    await ensure_tenants(db, 1)
    first = await upsert_budget(scope_type="tenant", scope_id=1, token_cap=100, tenant_id=1)
    second = await upsert_budget(scope_type="tenant", scope_id=1, token_cap=250, tenant_id=1)
    assert first.id == second.id  # re-posting the same scope+window updates in place
    assert second.token_cap == 250
    rows = await list_budgets(tenant_id=1)
    assert len(rows) == 1


async def test_list_tenants_and_users_and_usage_rollup(db):
    async with db() as session:
        session.add(Tenant(name="acme"))
        session.add(User(username="alice", role=Role.CLIENT, tenant_id=1))
        session.add(
            UsageLedger(
                tenant_id=1, model="gpt", prompt_tokens=10, completion_tokens=5, cost_usd=0.5
            )
        )
        await session.commit()

    assert [t.name for t in await list_tenants()] == ["acme"]
    users = await list_users(tenant_id=1)
    assert users[0].username == "alice"
    assert await user_tenant_id(1) == 1
    assert await user_tenant_id(999) is None

    pt, ct, cost, by_model, series = await usage_rollup(tenant_id=1)
    assert (pt, ct) == (10, 5)
    assert round(cost, 6) == 0.5
    assert by_model[0].model == "gpt"
    assert len(series) == 1


# ── last-platform-admin lockout ─────────────────────────────────────────────


async def test_last_platform_admin_cannot_be_demoted(db):
    async with db() as session:
        session.add(User(username="root", role=Role.ADMIN, tenant_id=None))
        await session.commit()
    with pytest.raises(LastPlatformAdminError):
        await update_user_role(1, Role.CLIENT)
    # The role is unchanged after the refusal.
    async with db() as session:
        user = await session.get(User, 1)
        assert user.role is Role.ADMIN


async def test_platform_admin_demotable_when_another_remains(db):
    async with db() as session:
        session.add(User(username="root1", role=Role.ADMIN, tenant_id=None))
        session.add(User(username="root2", role=Role.ADMIN, tenant_id=None))
        await session.commit()
    row = await update_user_role(1, Role.CLIENT)
    assert row is not None and row.role is Role.CLIENT


async def test_update_user_role_scoped_to_tenant_rejects_outsider(db):
    await seed(db, User(username="u", role=Role.CLIENT, tenant_id=5))
    # A tenant-admin caller scoped to tenant 9 cannot touch a tenant-5 user.
    assert await update_user_role(1, Role.DEVOPS, tenant_scope=9) is None
    # …but the owning tenant can.
    row = await update_user_role(1, Role.DEVOPS, tenant_scope=5)
    assert row is not None and row.role is Role.DEVOPS


# ── tenant isolation of the governed writes/reads (regression) ───────────────
#
# Both of these ran with no tenant predicate at all: ``upsert_budget`` matched only
# ``(scope_type, scope_id, window)`` and then reassigned ``existing.tenant_id``, so a
# second tenant posting the same triple silently took over the first tenant's cap;
# ``user_tenant_id`` was the one governed read that never bound the RLS scope.


@pytest.fixture
def scope_spy(monkeypatch):
    """Record the tenant scopes the enforcement layer binds, and bind none of them.

    Two effects, both deliberate and both relied on below. It **records** the scope, which
    is what the "…binds_the_tenant_scope" tests assert on. It also **replaces** the real
    binder, so no ``app.tenant_id`` GUC is set and the ``tenant_isolation`` policy takes
    its documented fail-open branch — every row is visible. That is the exact posture of a
    host that injects its own ``set_tenant_scope`` (a supported seam of
    :func:`configure_governance`) or of a deployment whose serving role turns out to be
    ``SUPERUSER``/``BYPASSRLS`` — the condition ``audit_rls_enforcement`` exists to *warn*
    about, not to prevent. It is where the application-level guards below are the only
    thing left, which is why they are tested here rather than assumed.

    Returns:
        The list of tenant scopes the code under test asked to bind.
    """
    seen: list[int | None] = []

    async def _spy(session, tenant_id):  # noqa: ANN001
        seen.append(tenant_id)

    monkeypatch.setattr(enforcement, "_set_tenant_scope", _spy)
    return seen


async def test_upsert_budget_refuses_a_cross_tenant_overwrite(db, scope_spy):
    """The app-level guard refuses the takeover when RLS is not the one stopping it.

    ``scope_spy`` leaves the tenant scope unbound (see its docstring), which is the only
    posture in which ``upsert_budget``'s natural-key lookup can *see* another tenant's
    row — and therefore the only posture in which this guard can fire at all. With a scope
    bound, the row is invisible and PostgreSQL stops the takeover first; that path is
    :func:`test_rls_stops_the_takeover_before_the_app_level_guard_can` below.
    """
    await ensure_tenants(db, 1, 2)
    owned = await upsert_budget(
        scope_type="user", scope_id=42, token_cap=100, tenant_id=1
    )
    with pytest.raises(CrossTenantBudgetError):
        await upsert_budget(scope_type="user", scope_id=42, token_cap=999_999, tenant_id=2)

    # Tenant 1's cap is untouched and still owned by tenant 1 — no partial write.
    rows = await list_budgets(tenant_id=1)
    assert [(r.id, r.token_cap) for r in rows] == [(owned.id, 100)]
    assert await list_budgets(tenant_id=2) == []
    # The refusal really did come from the guard, not from a scope that got bound anyway.
    assert scope_spy == [1, 2, 1, 2]


async def test_rls_stops_the_takeover_before_the_app_level_guard_can(db, pg_owner_engine):
    """With a scope bound, tenant 2 cannot reach tenant 1's cap — and never could.

    This is what the SQLite fixture could not express, and it changes the outcome rather
    than merely adding a layer. Under the live ``tenant_isolation`` policy, tenant 2's
    natural-key lookup is filtered to tenant 2's own rows, so ``existing`` is ``None``,
    ``CrossTenantBudgetError`` is unreachable, and the write lands as a **new row**.

    The isolation the test above cares about holds, and holds harder: tenant 1's cap is
    not merely refused to tenant 2, it is invisible to it. What does not hold is the
    natural key. The two assertions are separated below so the security property and the
    defect are not confused for one another.
    """
    await ensure_tenants(db, 1, 2)
    owned = await upsert_budget(scope_type="user", scope_id=42, token_cap=100, tenant_id=1)
    written = await upsert_budget(
        scope_type="user", scope_id=42, token_cap=999_999, tenant_id=2
    )

    # SECURITY: tenant 1's row is byte-for-byte untouched, read back over the
    # RLS-exempt owner connection so this is not itself filtered into looking clean.
    async with pg_owner_engine.connect() as conn:
        stored = {
            row.id: (row.tenant_id, row.token_cap)
            for row in (
                await conn.execute(
                    select(
                        Budget.__table__.c.id,
                        Budget.__table__.c.tenant_id,
                        Budget.__table__.c.token_cap,
                    )
                )
            ).all()
        }
    assert stored[owned.id] == (1, 100)

    # KNOWN DEFECT, pinned here so it cannot get quietly worse. ``upsert_budget``
    # documents that it keeps the lookup on the *full* natural key precisely so a
    # conflict is refused rather than duplicated — but RLS narrows that lookup to the
    # caller's tenant behind its back, so the duplicate it set out to avoid is exactly
    # what it writes. Each tenant still reads only its own row, but the platform-admin
    # view (unbound scope) sees both and ``_budgets_for(...).first()`` would pick between
    # them arbitrarily; there is no unique constraint on ``(scope_type, scope_id,
    # window)`` to catch it. The fix belongs in ``aegis.governance.enforcement``; when it
    # lands, this becomes ``== 1`` and the assertion above stays as it is.
    assert written.id != owned.id
    assert len(await list_budgets(scope_type="user", scope_id=42)) == 2


async def test_upsert_budget_still_updates_in_place_for_the_owning_tenant(db):
    await ensure_tenants(db, 1)
    first = await upsert_budget(scope_type="user", scope_id=42, token_cap=100, tenant_id=1)
    second = await upsert_budget(scope_type="user", scope_id=42, token_cap=250, tenant_id=1)
    assert first.id == second.id and second.token_cap == 250
    assert len(await list_budgets(tenant_id=1)) == 1


async def test_upsert_budget_may_claim_an_unowned_row(db, scope_spy):
    """An ownerless cap is adopted in place — when the caller can see it at all.

    ``scope_spy`` leaves the scope unbound on purpose. A row with ``tenant_id IS NULL``
    fails the policy predicate (``NULL = 3`` is NULL, not true), so under a bound scope
    tenant 3 does not claim this row, it silently writes a second one alongside it — the
    same natural-key duplication pinned in
    :func:`test_rls_stops_the_takeover_before_the_app_level_guard_can`. Asserting the
    *claim* therefore means asserting it where claiming is possible; ``== [row.id]``
    below is checked against every budget in the database, not just tenant 3's, so a
    shadow row would fail it.
    """
    await ensure_tenants(db, 3)
    await seed(
        db,
        Budget(scope_type=BudgetScope.USER, scope_id=7, window=BudgetWindow.DAY, token_cap=5),
    )
    row = await upsert_budget(scope_type="user", scope_id=7, token_cap=50, tenant_id=3)
    assert row.token_cap == 50
    assert [r.id for r in await list_budgets()] == [row.id]
    assert [r.id for r in await list_budgets(tenant_id=3)] == [row.id]


async def test_platform_admin_may_overwrite_and_does_not_erase_the_owner_stamp(db):
    await ensure_tenants(db, 1)
    owned = await upsert_budget(scope_type="user", scope_id=42, token_cap=100, tenant_id=1)
    updated = await upsert_budget(scope_type="user", scope_id=42, token_cap=300, tenant_id=None)
    assert updated.id == owned.id and updated.token_cap == 300
    # Still listed under tenant 1 — an unscoped write must not orphan the row.
    assert [r.id for r in await list_budgets(tenant_id=1)] == [owned.id]


# ── §7.16 row 2: a stored sub-cap can never be a figure that does not bind ────


async def test_a_user_sub_cap_above_the_tenant_cap_is_refused_rather_than_stored(db):
    """The write-time half of row 2, which is the half that used to be missing.

    ``_clamp_inward`` always made the *effective* limit inward, so this row saved
    happily and the budgets screen read back $500 while $50 was what bound. The refusal
    names the tenant cap that binds, because "invalid" would leave the operator to guess
    which of the four caps and whose figure they collided with.
    """
    await ensure_tenants(db, 1)
    await ensure_users(db, u42=1)
    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=50.0, tenant_id=1)

    with pytest.raises(UserCapAboveTenantCapError) as caught:
        await upsert_budget(scope_type="user", scope_id=42, usd_cap=500.0, tenant_id=1)
    message = str(caught.value)
    assert "$500.00" in message and "$50.00" in message and "tenant 1" in message

    # Refused, not partially written: there is no user row at all to read back.
    assert [r.scope_type for r in await list_budgets(tenant_id=1)] == ["tenant"]


async def test_a_user_sub_cap_at_or_under_the_tenant_cap_still_writes(db):
    """The other side of the boundary — an over-eager guard is its own defect.

    Equality is legal (the row says "always <= the tenant cap"), an absent tenant cap
    constrains nothing, and the comparison is same-window: a *monthly* tenant cap is a
    different quantity from a daily sub-cap and must not refuse one.
    """
    await ensure_tenants(db, 1)
    await ensure_users(db, u42=1, u43=1)
    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=50.0, tenant_id=1)

    at_the_cap = await upsert_budget(
        scope_type="user", scope_id=42, usd_cap=50.0, tenant_id=1
    )
    assert at_the_cap.usd_cap == 50.0
    # A different window is not the same cap, and is not measured against it.
    monthly = await upsert_budget(
        scope_type="user", scope_id=43, window="month", usd_cap=500.0, tenant_id=1
    )
    assert monthly.usd_cap == 500.0


async def test_every_cap_column_is_guarded_not_only_the_usd_one(db):
    """A guard on one column leaves the same lie reachable through the other three."""
    await ensure_tenants(db, 1)
    await ensure_users(db, u42=1)
    await upsert_budget(
        scope_type="tenant", scope_id=1, token_cap=100, usd_cap=5.0, rpm=10, tpm=1_000,
        tenant_id=1,
    )
    for field, value in (
        ("token_cap", 1_000),
        ("usd_cap", 50.0),
        ("rpm", 100),
        ("tpm", 10_000),
    ):
        with pytest.raises(UserCapAboveTenantCapError, match=field):
            await upsert_budget(
                scope_type="user", scope_id=42, tenant_id=1, **{field: value}
            )


async def test_lowering_the_tenant_cap_narrows_the_sub_caps_it_now_overrides(db):
    """The ordering hazard: the same lie, reached from the *tenant* write path.

    $500 under a $1000 tenant cap is legal when it is written. Lowering the tenant to
    $50 afterwards would leave that $500 stored, displayed, and not what binds — the
    identical defect, arrived at in two steps instead of one. So a tenant cap that moves
    down takes its sub-caps with it, in the same transaction as the write that moved it.
    """
    await ensure_tenants(db, 1)
    await ensure_users(db, u42=1, u43=1)
    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=1_000.0, tenant_id=1)
    await upsert_budget(scope_type="user", scope_id=42, usd_cap=500.0, tenant_id=1)
    await upsert_budget(scope_type="user", scope_id=43, usd_cap=20.0, tenant_id=1)

    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=50.0, tenant_id=1)

    stored = {
        (r.scope_type, r.scope_id): r.usd_cap for r in await list_budgets(tenant_id=1)
    }
    assert stored[("user", 42)] == 50.0, "the sub-cap above the new tenant cap survived"
    assert stored[("user", 43)] == 20.0, "a sub-cap already under the cap was moved"
    # And the invariant now holds from the other side too: the narrowed row is a legal
    # write, so re-posting it is accepted rather than refused by the guard above.
    assert (await upsert_budget(
        scope_type="user", scope_id=42, usd_cap=50.0, tenant_id=1
    )).usd_cap == 50.0


async def test_raising_the_tenant_cap_leaves_the_sub_caps_where_their_admin_set_them(db):
    """Narrowing is one-directional; a sub-cap is a decision, not a percentage."""
    await ensure_tenants(db, 1)
    await ensure_users(db, u42=1)
    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=50.0, tenant_id=1)
    await upsert_budget(scope_type="user", scope_id=42, usd_cap=20.0, tenant_id=1)

    await upsert_budget(scope_type="tenant", scope_id=1, usd_cap=1_000.0, tenant_id=1)

    stored = {
        (r.scope_type, r.scope_id): r.usd_cap for r in await list_budgets(tenant_id=1)
    }
    assert stored[("user", 42)] == 20.0


async def test_upsert_budget_binds_the_tenant_scope(db, scope_spy):
    await ensure_tenants(db, 1)
    await upsert_budget(scope_type="tenant", scope_id=1, token_cap=10, tenant_id=1)
    assert scope_spy == [1]


async def test_user_tenant_id_binds_the_tenant_scope(db, scope_spy):
    await seed(db, User(username="alice", role=Role.CLIENT, tenant_id=1))
    assert await user_tenant_id(1, tenant_scope=1) == 1
    assert scope_spy == [1]


async def test_user_tenant_id_hides_a_user_outside_the_caller_tenant(db):
    await seed(db, User(username="alice", role=Role.CLIENT, tenant_id=1))
    # A tenant-2 admin must not be able to resolve (and then cap) a tenant-1 user.
    assert await user_tenant_id(1, tenant_scope=2) is None
    # A platform-admin caller (the back-compatible default) still resolves any user.
    assert await user_tenant_id(1) == 1
    assert await user_tenant_id(1, tenant_scope=1) == 1
    assert await user_tenant_id(999, tenant_scope=1) is None
