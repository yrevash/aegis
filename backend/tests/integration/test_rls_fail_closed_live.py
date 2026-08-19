"""The fail-closed posture, proved on a real cluster as a real unprivileged role (§9.5).

``test_tenant_isolation_live.py`` proves the *shipped* posture: the ``tenant_isolation``
predicate is null-tolerant, so a session that bound no scope reads every tenant's rows.
That is deliberate, documented, and the reason the flag in this file exists — it is also
the hole. This module proves the other half: with ``RLS_FAIL_CLOSED=true`` the same
session reads **nothing**, and the four paths that legitimately span tenants keep working.

Everything here runs against a scratch database provisioned by the sibling module's own
fixtures — one fixture design, not two — and every assertion is made over a
``NOSUPERUSER NOBYPASSRLS`` login role. This project has twice been bitten by a policy
that was enforced against nobody, so a test that reads as the owner proves nothing here.

**The mutation is inside the first test.** The same connection, the same query and the
same rows are read under both predicates in one function: fail-closed → zero rows,
fail-open → both tenants' rows, fail-closed again → zero. A test that only asserted
"zero" would pass just as well against an empty table, a broken grant or a typo in the
seed, and that is the class of vacuous evidence this suite exists to refuse.

**What is deliberately *not* claimed.** These tests do not show that the enumeration of
unscoped readers is complete — nothing can, from inside a test. That is what
:func:`aegis.governance.rls.install_scope_auditor` is for, and why the default posture
stays fail-*open* until its findings are empty over a full suite run.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from aegis.governance.rls import (
    LOGIN_LOOKUP_FUNCTION,
    USERS_PROVISIONED_FUNCTION,
    bootstrap_login_functions,
    bootstrap_rls,
    set_tenant_scope,
)
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_tenant_isolation_live import (
    _TENANT_A,
    _TENANT_B,
    _admin_dsn,
    _app_engine,
    _drop_scratch,
    _provision,
    _registered_tables,
    _Scratch,
    _skip_or_fail,
)

#: The table every assertion below reads. ``audit_log`` because it is the one the phase
#: doc names as never having bound a scope at all, and because it has no foreign-key
#: children to complicate a rollback.
_TABLE = "audit_log"

#: The tenant column, spelled once.
_TENANT_COLUMN = "tenant_id"


def _seed_username(tenant_id: int) -> str:
    """Return the username the sibling module's seeder wrote for ``tenant_id``.

    Derived from that seeder's own rule (``f"{tag}-{column.name}"`` for a string column,
    with ``tag`` being ``seed-t<tenant>``) rather than hard-coded, so a change to the
    seeder fails here loudly instead of turning the login assertions vacuous.

    Args:
        tenant_id: The seeded tenant.

    Returns:
        The ``users.username`` value for that tenant's seeded row.
    """
    return f"seed-t{tenant_id}-username"


async def _reinstall(scratch: _Scratch, *, fail_closed: bool) -> None:
    """Re-run the policy DDL on the scratch database under the given posture.

    Args:
        scratch: The scratch handle.
        fail_closed: Which predicate to install.
    """
    owner = create_async_engine(scratch.owner_dsn)
    try:
        await bootstrap_rls(owner, fail_closed=fail_closed)
    finally:
        await owner.dispose()


async def _provision_closed(admin_dsn: str) -> _Scratch:
    """Provision the sibling module's scratch database, then close the policy on it.

    Args:
        admin_dsn: DSN of a role allowed to ``CREATE ROLE``/``CREATE DATABASE``.

    Returns:
        The provisioned :class:`_Scratch`, carrying the fail-closed policy and the
        login functions.
    """
    scratch = await _provision(admin_dsn)
    try:
        await _reinstall(scratch, fail_closed=True)
        owner = create_async_engine(scratch.owner_dsn)
        try:
            await bootstrap_login_functions(owner, scratch.role)
        finally:
            await owner.dispose()
    except Exception:
        await _drop_scratch(admin_dsn, scratch.database, scratch.role)
        raise
    return scratch


@pytest.fixture(scope="module")
def closed(): # noqa: ANN201 - Iterator[_Scratch]
    """A scratch database whose ``tenant_isolation`` policy is the fail-closed one.

    Module-scoped and synchronous for the same reasons as the sibling module's
    ``scratch``: provisioning per test would dominate the runtime, and driving
    ``asyncio.run`` here keeps every async test on its own function-scoped loop.

    Yields:
        The :class:`_Scratch` handle.
    """
    admin_dsn = _admin_dsn()
    try:
        provisioned = asyncio.run(_provision_closed(admin_dsn))
    except (OSError, SQLAlchemyError) as exc:
        _skip_or_fail(f"{type(exc).__name__}: {exc}")
        raise  # unreachable: _skip_or_fail always raises
    try:
        yield provisioned
    finally:
        asyncio.run(_drop_scratch(admin_dsn, provisioned.database, provisioned.role))


async def _unscoped_count(scratch: _Scratch) -> int:
    """Count the rows an unprivileged connection sees with **no** scope bound.

    Args:
        scratch: The scratch handle.

    Returns:
        The row count in :data:`_TABLE` as the unprivileged role, having bound nothing.
    """
    engine = _app_engine(scratch)
    try:
        async with engine.connect() as conn:
            return int(
                (await conn.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))).scalar_one()
            )
    finally:
        await engine.dispose()


async def test_an_unbound_scope_reads_nothing_and_the_predicate_is_why(closed: _Scratch):
    """THE mutation: the same read answers 0 closed, 2 open, 0 closed again.

    This is the whole task in one assertion. The middle reading is not decoration — it is
    what makes the first and third mean something. Without it "zero rows" is equally
    consistent with an empty table, a missing ``GRANT SELECT``, a mistyped table name, or
    a connection that never reached the database at all. With it, the only variable that
    changed between the readings is which predicate ``CREATE POLICY`` carries.

    The posture is restored before the function returns, so the module's other tests are
    order-independent of this one.
    """
    assert await _unscoped_count(closed) == 0, (
        "an unscoped read returned rows under the fail-closed predicate — the policy "
        "was not installed, or the reading role is exempt from it"
    )

    await _reinstall(closed, fail_closed=False)
    try:
        assert await _unscoped_count(closed) == 2, (
            "the fail-OPEN predicate returned no rows either, so the fail-closed "
            "reading above proves nothing about the predicate"
        )
    finally:
        await _reinstall(closed, fail_closed=True)

    assert await _unscoped_count(closed) == 0


async def test_a_bound_tenant_still_reads_exactly_its_own_row_in_every_table(
    closed: _Scratch,
):
    """Closing the unset branch must not disturb the branch that was already right.

    Swept over the whole registry, and over both tenants, exactly as the fail-open sibling
    test does: the risk of a predicate rewrite is not that isolation weakens, it is that
    isolation becomes *total* — a policy that returns nothing for everyone would pass a
    test that only ever asked "did tenant B's row leak".
    """
    engine = _app_engine(closed)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for table in _registered_tables():
            for tenant_id in (_TENANT_A, _TENANT_B):
                async with maker() as session:
                    await set_tenant_scope(session, tenant_id)
                    visible = (
                        await session.execute(
                            text(f'SELECT {_TENANT_COLUMN} FROM "{table.name}"')
                        )
                    ).scalars().all()
                    await session.rollback()
                assert visible == [tenant_id], (
                    f"{table.name}: scoped to tenant {tenant_id} but saw {visible}"
                )
    finally:
        await engine.dispose()


async def test_the_platform_scope_is_an_assertion_that_dies_with_its_transaction(
    closed: _Scratch,
):
    """``set_tenant_scope(session, None)`` widens — and only for that transaction.

    Three readings on **one** connection, in order, because the danger of a widening GUC
    is not that it fails to widen but that it stays widened: ``app.tenant_all`` lives on a
    pooled connection, and a value that survived the transaction would hand the next
    request every tenant's rows with no call site to blame.

    The third reading is the one that matters. It rebinds a *tenant* scope on the same
    connection and requires the widening to be gone — proving
    :func:`~aegis.governance.rls.scope_binding_params` clears the assertion rather than
    merely not setting it.
    """
    engine = _app_engine(closed)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await set_tenant_scope(session, None)
            wide = (
                await session.execute(text(f'SELECT {_TENANT_COLUMN} FROM "{_TABLE}"'))
            ).scalars().all()
            assert sorted(wide) == [_TENANT_A, _TENANT_B]
            await session.commit()

            after_commit = (
                await session.execute(text(f'SELECT count(*) FROM "{_TABLE}"'))
            ).scalar_one()
            assert after_commit == 0, (
                "the platform assertion outlived its transaction — set_config's "
                "is_local flag is the only thing stopping one request's widening from "
                "becoming the next request's"
            )
            await session.commit()

            await set_tenant_scope(session, _TENANT_A)
            narrowed = (
                await session.execute(text(f'SELECT {_TENANT_COLUMN} FROM "{_TABLE}"'))
            ).scalars().all()
            assert narrowed == [_TENANT_A]
            await session.rollback()
    finally:
        await engine.dispose()


async def test_login_still_works_and_the_definer_function_widens_only_itself(
    closed: _Scratch,
):
    """Login is the one read that precedes a tenant, and it still resolves.

    Four readings on one unprivileged connection:

    1. the plain query the login path used to run sees **nothing** — the failure this
       function exists to avoid, demonstrated rather than described;
    2. :data:`~aegis.governance.rls.LOGIN_LOOKUP_FUNCTION` returns the row for the exact
       username passed, and nothing for a username that does not exist;
    3. :data:`~aegis.governance.rls.USERS_PROVISIONED_FUNCTION` answers the "was this
       deployment ever seeded" question the 503 branch depends on;
    4. the plain query **still** sees nothing afterwards, which is the property that
       makes the function narrow: PostgreSQL restores a ``SET`` clause's prior value when
       the function exits, so the widening cannot escape into the caller's transaction.
    """
    engine = _app_engine(closed)
    try:
        async with engine.connect() as conn:
            before = (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
            assert before == 0

            rows = (
                await conn.execute(
                    text(
                        f"SELECT username, {_TENANT_COLUMN} FROM "
                        f"{LOGIN_LOOKUP_FUNCTION}(:username)"
                    ),
                    {"username": _seed_username(_TENANT_A)},
                )
            ).all()
            assert [r[1] for r in rows] == [_TENANT_A], (
                f"the login lookup returned {rows} — login is broken under the "
                "fail-closed posture, which is the failure this function prevents"
            )

            missing = (
                await conn.execute(
                    text(f"SELECT * FROM {LOGIN_LOOKUP_FUNCTION}(:username)"),
                    {"username": "nobody-by-that-name"},
                )
            ).all()
            assert missing == []

            assert (
                await conn.execute(text(f"SELECT {USERS_PROVISIONED_FUNCTION}()"))
            ).scalar_one() is True

            after = (await conn.execute(text("SELECT count(*) FROM users"))).scalar_one()
            assert after == 0, (
                "the function's widening leaked into the caller's transaction — every "
                "statement after a login would then run platform-wide"
            )
    finally:
        await engine.dispose()


async def test_the_set_clause_and_not_the_definer_identity_is_what_widens(
    closed: _Scratch,
):
    """Isolate the mechanism: hand the function to the unprivileged role and re-run it.

    On a stock local cluster the table owner is a superuser, so a ``SECURITY DEFINER``
    function would see every row whatever its ``SET`` clause said — which means the
    previous test cannot tell the two mechanisms apart, and a deployment whose owner is a
    non-superuser (the correct shape) would behave differently from the one under test.

    Re-owning the function to the ``NOSUPERUSER NOBYPASSRLS`` role removes the definer
    identity as a variable entirely: the function now runs as exactly the role calling
    it, so ``SET app.tenant_all = 'on'`` is the only thing that can still make the lookup
    return a row. The ownership is restored afterwards.
    """
    owner = create_async_engine(closed.owner_dsn)
    try:
        async with owner.begin() as conn:
            owning_role = (
                await conn.execute(text("SELECT current_user"))
            ).scalar_one()
            await conn.execute(
                text(
                    f'ALTER FUNCTION {LOGIN_LOOKUP_FUNCTION}(text) '
                    f'OWNER TO "{closed.role}"'
                )
            )
        app = _app_engine(closed)
        try:
            async with app.connect() as conn:
                rows = (
                    await conn.execute(
                        text(
                            f"SELECT {_TENANT_COLUMN} FROM "
                            f"{LOGIN_LOOKUP_FUNCTION}(:username)"
                        ),
                        {"username": _seed_username(_TENANT_B)},
                    )
                ).scalars().all()
            assert rows == [_TENANT_B], (
                "with the definer identity neutralised the lookup returned nothing, so "
                "the SET clause is not carrying the widening and a deployment with a "
                "non-superuser table owner would have a broken login"
            )
        finally:
            await app.dispose()
        async with owner.begin() as conn:
            await conn.execute(
                text(
                    f'ALTER FUNCTION {LOGIN_LOOKUP_FUNCTION}(text) '
                    f'OWNER TO "{owning_role}"'
                )
            )
    finally:
        await owner.dispose()


async def test_the_platform_readers_this_task_enumerated_still_read(closed: _Scratch):
    """The SLA sweeper, the registry warm and the audit list, under the closed posture.

    These are three of the five unscoped readers the phase doc names, and they are here
    together because they fail the same way and the fix is the same sentence: each is a
    genuinely platform-wide read that used to make that claim by binding *nothing*, which
    is spelled identically to a path that forgot. Under the fail-closed predicate the
    unfixed versions return zero rows, silently — the SLA sweeper stops expiring gates,
    the prompt registry serves the floor prompt to everybody, and the audit trail reads
    empty. None of those raise, which is why they are asserted rather than trusted.
    """
    from aegis.governance import list_recent_audit
    from aegis.ops import registry

    import app.data.governance  # noqa: F401 - its import wires configure_governance
    from app.data import session as session_module
    from app.data.approvals import sweep_expired

    engine = _app_engine(closed)
    owner = create_async_engine(closed.owner_dsn)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    # ``app.data.governance`` wires the governance seam to ``get_sessionmaker()``, which
    # resolves on every call — so pointing the process-wide engine at this scratch
    # database is enough, and nothing has to reach into the seam. The engines are
    # restored below, because a suite that leaves them pointing at a dropped database
    # fails somewhere else entirely.
    saved = (
        session_module._engine,  # noqa: SLF001 - restoring process state the test moves
        session_module._admin_engine,  # noqa: SLF001
        session_module._sessionmaker,  # noqa: SLF001
    )
    try:
        session_module.configure_engine(engine, admin_engine=owner)

        rows = await list_recent_audit(limit=50)
        assert len(rows) == 2, (
            f"list_recent_audit read {len(rows)} of 2 rows under the platform scope — "
            "the reader that never bound a scope at all now reads nothing"
        )
        scoped = await list_recent_audit(limit=50, tenant_id=_TENANT_A)
        assert len(scoped) == 1, (
            f"list_recent_audit read {len(scoped)} of 1 row for one tenant"
        )

        registry.clear_cache()
        async with maker() as session:
            await registry.refresh_cache(session)
        # Two seeded ``prompt_versions`` rows, one per tenant, and both are ACTIVE only
        # if the enum generator happened to pick that label — so the claim asserted here
        # is the one that matters and cannot be an accident: the warm-up saw the table.
        async with maker() as session:
            await set_tenant_scope(session, None)
            total = (
                await session.execute(text("SELECT count(*) FROM prompt_versions"))
            ).scalar_one()
        assert total == 2

        async with owner.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE approvals SET status = 'PENDING', sla_deadline = :deadline"
                ),
                {"deadline": datetime.now(UTC) - timedelta(hours=2)},
            )

        actions = await sweep_expired()
        assert len(actions) == 2, (
            f"the SLA sweeper acted on {len(actions)} of 2 past-deadline gates — under "
            "the fail-closed posture an unscoped sweeper sees none of them and reports "
            "a clean pass"
        )
    finally:
        registry.clear_cache()
        (
            session_module._engine,  # noqa: SLF001
            session_module._admin_engine,  # noqa: SLF001
            session_module._sessionmaker,  # noqa: SLF001
        ) = saved
        await engine.dispose()
        await owner.dispose()
