"""The resolver against a real PostgreSQL: the merge rules, the source, and the refusals.

Run against a live database rather than a fake store because two of the claims are
*database* claims. The platform baseline is a NULL-tenant row, and it has to stay
readable under a bound tenant scope or every ``tighten_only`` key would silently resolve
against the compiled-in default instead of the platform's own choice — the exact
weakening the rule exists to prevent. And the check constraints that stop a user-scoped
row from having no tenant only exist in PostgreSQL.

Platform-scoped writes are made on a session with **no** tenant scope bound, which is
what a platform admin's request actually is (that tier carries no tenant pin). Tenant and
user writes bind their tenant, like every governed request.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.governance.rls import set_tenant_scope
from aegis.settings import (
    SettingNotReadableError,
    SettingNotWritableError,
    SettingScope,
    SettingValueError,
    SettingWeakerThanFloorError,
    UnknownSettingError,
    resolve,
    resolve_all,
    write_setting,
)
from aegis.settings.models import Setting

from .._seed import ensure_tenants, ensure_users

_TENANT = 601
_OTHER_TENANT = 602
_USER = 6011
_OTHER_USER = 6012


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenants and users the FKs need."""
    await ensure_tenants(pg_sessionmaker, _TENANT, _OTHER_TENANT)
    await ensure_users(pg_sessionmaker, **{f"u{_USER}": _TENANT, f"u{_OTHER_USER}": _TENANT})
    return pg_sessionmaker


async def _write(db, key, value, *, scope, role, tenant_id=None, user_id=None, actor=None):  # noqa: ANN001, ANN202, PLR0913
    """Write a setting the way a request would: scope bound, then committed."""
    async with db() as session:
        # A platform admin has no tenant pin; everyone else binds theirs.
        await set_tenant_scope(session, None if scope is SettingScope.PLATFORM else tenant_id)
        row = await write_setting(
            session,
            key,
            value,
            scope=scope,
            actor_role=role,
            tenant_id=tenant_id,
            user_id=user_id,
            actor_user_id=actor,
        )
        await session.commit()
        return row


async def _resolve(db, key, *, tenant_id=None, user_id=None, role=None):  # noqa: ANN001, ANN202
    """Resolve a setting under a bound tenant scope, as a request would."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        result = await resolve(
            session, key, tenant_id=tenant_id, user_id=user_id, actor_role=role
        )
        await session.rollback()
        return result


# ── tighten_only: the load-bearing rule ──────────────────────────────────────


async def test_a_tenant_cannot_write_a_weaker_value_and_is_told_why(db):
    """Refused with a reason — silently ignoring it is the failure mode.

    The resolver already could not compute a weaker value, so a stored weakening would be
    displayed back to the tenant admin as their setting and have no effect whatsoever. A
    setting that lies about being in force is worse than one that was refused.
    """
    await _write(
        db, "agent.gate_min_risk", "medium", scope=SettingScope.PLATFORM, role="platform_admin"
    )

    with pytest.raises(SettingWeakerThanFloorError) as caught:
        await _write(
            db,
            "agent.gate_min_risk",
            "high",
            scope=SettingScope.TENANT,
            role="tenant_admin",
            tenant_id=_TENANT,
        )
    assert caught.value.floor == "medium"
    assert "may only be tightened" in caught.value.reason
    assert "no effect" in caught.value.reason

    assert await _resolve(db, "agent.gate_min_risk", tenant_id=_TENANT) == ("medium", "platform")


async def test_a_weaker_row_that_predates_a_platform_tightening_simply_loses(db):
    """The structural half: the resolver cannot return it even though it is stored.

    This is not a hypothetical row — it is what a legitimate tenant value becomes the day
    the platform tightens its own default. The write was legal when it was made, the
    platform then moved the floor, and the resolver must fold the platform layer in
    rather than trust the newest write.

    Note the first write: the platform may move its **own** layer in either direction,
    because the platform is what the floor *is*. Only the scopes beneath it are held to
    tightening.
    """
    await _write(
        db,
        "agent.max_plan_iterations",
        6,
        scope=SettingScope.PLATFORM,
        role="platform_admin",
    )
    await _write(
        db,
        "agent.max_plan_iterations",
        4,
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    assert await _resolve(db, "agent.max_plan_iterations", tenant_id=_TENANT) == (4, "tenant")

    await _write(
        db,
        "agent.max_plan_iterations",
        2,
        scope=SettingScope.PLATFORM,
        role="platform_admin",
    )

    value, source = await _resolve(db, "agent.max_plan_iterations", tenant_id=_TENANT)
    assert (value, source) == (2, "platform")
    # …and the tenant's row is still there. Nothing was deleted behind their back; it is
    # simply not what resolves.
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        stored = (
            await session.execute(
                Setting.__table__.select().where(
                    Setting.key == "agent.max_plan_iterations",
                    Setting.scope == SettingScope.TENANT,
                )
            )
        ).all()
    assert [row.value for row in stored] == [4]


async def test_a_tenant_may_tighten_and_the_source_moves_with_it(db):
    assert await _resolve(db, "agent.gate_min_risk", tenant_id=_TENANT) == ("high", "platform")
    await _write(
        db,
        "agent.gate_min_risk",
        "low",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    assert await _resolve(db, "agent.gate_min_risk", tenant_id=_TENANT) == ("low", "tenant")


async def test_a_user_may_not_loosen_what_their_tenant_tightened(db):
    """The floor for a user write is platform **and** tenant, not platform alone."""
    await _write(
        db,
        "agent.gate_min_risk",
        "low",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    with pytest.raises(SettingWeakerThanFloorError) as caught:
        await _write(
            db,
            "agent.gate_min_risk",
            "high",
            scope=SettingScope.USER,
            # An operator setting their **own** gate: ``agent.gate_min_risk`` is not a
            # business user's to touch at any scope, which is a different refusal.
            role="ai_team",
            tenant_id=_TENANT,
            user_id=_USER,
            actor=_USER,
        )
    assert caught.value.floor == "low"


async def test_a_bound_tenant_scope_can_still_see_the_platform_baseline(db):
    """If it could not, every tighten_only key would resolve against the wrong floor.

    ``settings`` is registered in ``_PLATFORM_BASELINE_TABLES`` precisely for this, and
    the assertion is written through ``resolve`` rather than against the catalog so it
    fails if either half — the policy or the resolver's query — stops holding.
    """
    await _write(
        db, "agent.mode", "fast", scope=SettingScope.PLATFORM, role="platform_admin"
    )
    assert await _resolve(db, "agent.mode", tenant_id=_TENANT) == ("fast", "platform")


# ── union: additive, and removal is not expressible ──────────────────────────


async def test_a_union_key_appends_the_tenants_members_to_the_platforms(db):
    value, source = await _resolve(db, "guardrails.pii.entities", tenant_id=_TENANT)
    assert value == ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"]
    assert source == "platform"

    await _write(
        db,
        "guardrails.pii.entities",
        ["IBAN_CODE"],
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    value, source = await _resolve(db, "guardrails.pii.entities", tenant_id=_TENANT)
    assert value == ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD", "IBAN_CODE"]
    assert source == "tenant"


async def test_a_union_key_cannot_be_used_to_remove_a_platform_member(db):
    """Removal is not a thing a tenant can express — the fold is a union, full stop."""
    await _write(
        db,
        "guardrails.pii.entities",
        ["CREDIT_CARD"],
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    value, source = await _resolve(db, "guardrails.pii.entities", tenant_id=_TENANT)
    assert value == ["EMAIL_ADDRESS", "PHONE_NUMBER", "CREDIT_CARD"]
    # Nothing was added either, so the platform is still the source of the effective set.
    assert source == "platform"


async def test_a_union_key_accumulates_across_all_three_scopes(db):
    await _write(
        db,
        "guardrails.denylist.terms",
        ["acme-internal"],
        scope=SettingScope.PLATFORM,
        role="platform_admin",
    )
    await _write(
        db,
        "guardrails.denylist.terms",
        ["project-zephyr"],
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    value, source = await _resolve(db, "guardrails.denylist.terms", tenant_id=_TENANT)
    assert value == ["acme-internal", "project-zephyr"]
    assert source == "tenant"


# ── the source, at each scope ────────────────────────────────────────────────


async def test_resolve_reports_the_scope_that_actually_decided_the_value(db):
    """A badge saying "tenant default" over a platform value is worse than no badge."""
    assert await _resolve(db, "agent.model", tenant_id=_TENANT, user_id=_USER) == (
        "default",
        "platform",
    )

    await _write(
        db, "agent.model", "tenant-choice", scope=SettingScope.PLATFORM, role="platform_admin"
    )
    assert await _resolve(db, "agent.model", tenant_id=_TENANT, user_id=_USER) == (
        "tenant-choice",
        "platform",
    )

    await _write(
        db,
        "agent.model",
        "the-tenants",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    assert await _resolve(db, "agent.model", tenant_id=_TENANT, user_id=_USER) == (
        "the-tenants",
        "tenant",
    )

    await _write(
        db,
        "agent.model",
        "mine",
        scope=SettingScope.USER,
        role="client",
        tenant_id=_TENANT,
        user_id=_USER,
        actor=_USER,
    )
    assert await _resolve(db, "agent.model", tenant_id=_TENANT, user_id=_USER) == (
        "mine",
        "user",
    )
    # …and another user in the same tenant still sees the tenant's choice.
    assert await _resolve(db, "agent.model", tenant_id=_TENANT, user_id=_OTHER_USER) == (
        "the-tenants",
        "tenant",
    )


async def test_one_tenants_setting_is_invisible_to_another(db):
    await _write(
        db,
        "agent.model",
        "acme-only",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    assert await _resolve(db, "agent.model", tenant_id=_OTHER_TENANT) == ("default", "platform")


# ── the refusals ─────────────────────────────────────────────────────────────


async def test_a_role_outside_writable_by_is_refused(db):
    """Phase 7 row 2: a tenant raising its own budget cap. Enforced in the resolver.

    Not in the form: a disabled control is a hint, and this is the check a ``curl`` hits.
    """
    with pytest.raises(SettingNotWritableError, match="may not write"):
        await _write(
            db,
            "budget.usd_cap",
            50000.0,
            scope=SettingScope.TENANT,
            role="tenant_admin",
            tenant_id=_TENANT,
        )


async def test_a_business_user_may_set_their_own_preference_but_not_the_tenants(db):
    """``writable_by`` says whether; the scope rule says how far."""
    with pytest.raises(SettingNotWritableError, match="not the tenant default"):
        await _write(
            db,
            "agent.model",
            "everyones",
            scope=SettingScope.TENANT,
            role="client",
            tenant_id=_TENANT,
            user_id=None,
            actor=_USER,
        )
    row = await _write(
        db,
        "agent.model",
        "mine",
        scope=SettingScope.USER,
        role="client",
        tenant_id=_TENANT,
        user_id=_USER,
        actor=_USER,
    )
    assert row.value == "mine"


async def test_a_user_cannot_write_another_users_setting(db):
    with pytest.raises(SettingNotWritableError, match="another user's"):
        await _write(
            db,
            "agent.model",
            "not-yours",
            scope=SettingScope.USER,
            role="client",
            tenant_id=_TENANT,
            user_id=_OTHER_USER,
            actor=_USER,
        )


async def test_only_a_platform_admin_writes_the_platform_layer(db):
    with pytest.raises(SettingNotWritableError, match="platform-scoped"):
        await _write(
            db, "agent.mode", "fast", scope=SettingScope.PLATFORM, role="tenant_admin"
        )


async def test_a_user_scoped_row_without_a_tenant_is_refused(db):
    """Such a row would carry a NULL tenant, which is what marks a platform baseline.

    It would therefore be readable by *every* tenant — the widened read on this table is
    exactly why the shape has to be refused rather than merely discouraged.
    """
    with pytest.raises(SettingNotWritableError, match="readable by every tenant"):
        await _write(
            db,
            "agent.model",
            "orphan",
            scope=SettingScope.USER,
            role="client",
            tenant_id=None,
            user_id=_USER,
            actor=_USER,
        )


async def test_an_illegal_value_is_refused_with_the_reason(db):
    with pytest.raises(SettingValueError, match="outside"):
        await _write(
            db,
            "agent.max_plan_iterations",
            99,
            scope=SettingScope.TENANT,
            role="tenant_admin",
            tenant_id=_TENANT,
        )
    with pytest.raises(SettingValueError, match="expected an integer"):
        await _write(
            db,
            "agent.max_plan_iterations",
            "two",
            scope=SettingScope.TENANT,
            role="tenant_admin",
            tenant_id=_TENANT,
        )


async def test_an_unknown_key_is_refused_at_both_ends(db):
    async with db() as session:
        with pytest.raises(UnknownSettingError):
            await resolve(session, "agent.nonexistent", tenant_id=_TENANT)
        with pytest.raises(UnknownSettingError):
            await write_setting(
                session,
                "agent.nonexistent",
                1,
                scope=SettingScope.PLATFORM,
                actor_role="platform_admin",
            )


async def test_a_reader_outside_readable_by_is_refused(db):
    with pytest.raises(SettingNotReadableError, match="may not read"):
        await _resolve(db, "jobs.max_inflight.ingest", tenant_id=_TENANT, role="client")


# ── the screen's read ────────────────────────────────────────────────────────


async def test_resolve_all_returns_every_readable_key_with_its_source(db):
    """One query for a settings screen, and unreadable keys are omitted, not refused."""
    await _write(
        db,
        "agent.model",
        "the-tenants",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        admin_view = await resolve_all(
            session, tenant_id=_TENANT, user_id=_USER, actor_role="tenant_admin"
        )
        client_view = await resolve_all(
            session, tenant_id=_TENANT, user_id=_USER, actor_role="client"
        )
        await session.rollback()

    assert admin_view["agent.model"] == ("the-tenants", "tenant")
    assert admin_view["agent.gate_min_risk"] == ("high", "platform")
    assert "jobs.max_inflight.ingest" in admin_view
    # A client sees the same values for the keys it may read, and simply does not get
    # the operator-only one — a screen with one unreadable key is not a blank screen.
    assert "jobs.max_inflight.ingest" not in client_view
    assert client_view["agent.model"] == ("the-tenants", "tenant")


async def test_writing_the_same_key_twice_updates_the_one_row(db):
    """One row per key per scope; the partial unique indexes are what make that true."""
    await _write(
        db,
        "agent.model",
        "first",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    await _write(
        db,
        "agent.model",
        "second",
        scope=SettingScope.TENANT,
        role="tenant_admin",
        tenant_id=_TENANT,
    )
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        rows = (
            await session.execute(
                Setting.__table__.select().where(Setting.key == "agent.model")
            )
        ).all()
    assert [row.value for row in rows] == ["second"]
