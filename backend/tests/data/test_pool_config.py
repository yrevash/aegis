"""The Postgres pools are sized, and exhaustion says so (§9.4).

The defect this closes is not a crash, it is a **silence**. Both engines ran on
SQLAlchemy's defaults, so a request that could not get a connection waited thirty seconds
and then raised ``QueuePool limit of size 5 overflow 10 reached, connection timed out,
timeout 30.00`` — a message that names neither which engine ran out nor which setting
would fix it, arriving long after the operator has decided the process is hung. On a demo
a thirty-second stall is diagnosed by restarting, which destroys the evidence.

Two claims, and the failure mode of each:

* the sizes are **applied**, and not applied to SQLite — a queue pool forced onto the
  in-process SQLite engine is how a suite acquires a mysterious "database is locked";
* exhaustion produces :class:`~app.data.session.PoolExhaustedError`, which names the
  pool, its live status and the variable to raise. Proved by actually exhausting a real
  pool against a real cluster, not by asserting a string.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.data.session import PoolExhaustedError, _pool_kwargs, pool_budget


def test_sqlite_gets_no_queue_pool():
    """A non-Postgres URL keeps SQLAlchemy's own pooling for its dialect.

    Cheap, and load-bearing: ``_pool_kwargs`` is called unconditionally from both engine
    builders, so the guard is the only thing standing between the lite/offline path and a
    pool class its driver cannot use.
    """
    assert _pool_kwargs("sqlite+aiosqlite:///:memory:", label="serving", size=5, overflow=5) == {}


def test_postgres_gets_the_configured_sizes():
    """The numbers in settings reach the engine, rather than being documentation."""
    settings = get_settings()
    kwargs = _pool_kwargs(
        "postgresql+asyncpg://u@h/db",
        label="serving",
        size=settings.db_pool_size,
        overflow=settings.db_max_overflow,
    )
    assert kwargs["pool_size"] == settings.db_pool_size
    assert kwargs["max_overflow"] == settings.db_max_overflow
    assert kwargs["pool_timeout"] == settings.db_pool_timeout_seconds
    assert kwargs["pool_pre_ping"] is True


async def test_exhaustion_names_the_pool_and_the_setting_instead_of_stalling(
    postgres_database,
):
    """Fill a real pool against a real cluster and read what the platform says.

    ``pool_size=1, max_overflow=0`` so exhaustion is reachable in one checkout, and a
    half-second timeout so the test costs half a second rather than the thirty this task
    is about. The assertions are on the three things the old message lacked: **which**
    pool, what it is doing right now, and the name of the knob.
    """
    engine = create_async_engine(
        postgres_database.scratch.app_dsn,
        **{
            **_pool_kwargs(
                postgres_database.scratch.app_dsn, label="serving", size=1, overflow=0
            ),
            "pool_size": 1,
            "max_overflow": 0,
            "pool_timeout": 0.5,
        },
    )
    try:
        async with engine.connect() as held:
            await held.execute(text("SELECT 1"))
            with pytest.raises(PoolExhaustedError) as caught:
                async with engine.connect():
                    pass
        message = str(caught.value)
        assert "serving Postgres pool is exhausted" in message
        assert "DB_POOL_SIZE" in message
        assert "pg_stat_activity" in message
    finally:
        await engine.dispose()


async def test_the_budget_is_checked_against_the_cluster_not_a_comment(db, caplog):
    """``pool_budget`` reads ``max_connections`` and reports what is left.

    The arithmetic is written down in ``app.config``; this is the half that checks it
    against the database actually in front of the process, because that number belongs to
    the cluster and not to this repository. Logged rather than raised — over-subscription
    is a fault only when every pool fills at once, and refusing to boot would turn a
    capacity warning into an outage.
    """
    with caplog.at_level("INFO", logger="app.data.session"):
        result = await pool_budget()
    assert result is not None
    ceiling, requested = result
    settings = get_settings()
    # All THREE engines. This assertion previously named only two, which is how the
    # console's fifteen default connections stayed out of the one figure whose job is to
    # be checked against a cluster — and out of it in the reassuring direction.
    assert requested == (
        settings.db_pool_size
        + settings.db_max_overflow
        + settings.db_admin_pool_size
        + settings.db_admin_max_overflow
        + settings.db_console_pool_size
        + settings.db_console_max_overflow
    )
    assert requested < ceiling, (
        f"the configured pools ({requested}) do not fit inside this cluster's ceiling "
        f"({ceiling}); the sizes in app.config are the thing to change"
    )
    assert "pool budget" in caplog.text.lower()


async def test_the_console_engine_is_sized_like_the_other_two(postgres_database, monkeypatch):
    """The §7.9 console's own engine is not left on SQLAlchemy's defaults.

    **The defect this pins.** ``app.api.routes_db._console_engine`` built its engine with
    a bare ``create_async_engine``, so it took ``pool_size=5, max_overflow=10,
    pool_timeout=30`` while ``app.config``'s arithmetic recorded it as ``3 + 2`` — fifteen
    connections the budget never counted, and the thirty-second undiagnosed stall §9.4
    exists to remove, on the one engine an operator opens *because* the platform is
    already misbehaving.

    Mutation-proven: drop the ``pool_kwargs(...)`` from ``_console_engine`` and this reads
    ``QueuePool``/``30.0`` instead of ``ConsolePool``/the configured timeout.
    """
    from app.api.routes_db import _console_engine, reset_console_engine

    monkeypatch.setattr(
        "app.api.routes_db.console_dsn", lambda: postgres_database.scratch.app_dsn
    )
    reset_console_engine()
    engine = _console_engine()
    try:
        settings = get_settings()
        pool = engine.pool
        assert type(pool).__name__ == "ConsolePool", (
            "the console engine did not get the named pool, so exhaustion on it is a "
            "bare SQLAlchemy TimeoutError again"
        )
        assert pool.size() == settings.db_console_pool_size
        assert pool._max_overflow == settings.db_console_max_overflow
        assert pool._timeout == settings.db_pool_timeout_seconds
    finally:
        await engine.dispose()
        reset_console_engine()
