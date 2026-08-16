"""Seeding helpers that satisfy the foreign keys a real database actually enforces.

The suite used to run on SQLite, which does not enforce foreign keys unless
``PRAGMA foreign_keys=ON`` is issued on every connection — something the old fixture
never did. So test after test wrote a ``usage_ledger`` row for tenant 1, an ``audit_log``
entry for tenant 3, a ``budgets`` row for a tenant that had no ``tenants`` row at all, and
a ``memory_message`` whose ``memory_session`` was never created, and nothing objected.
PostgreSQL objects, correctly: those rows are unattributable, and unattributable spend is
precisely the failure the ledger exists to prevent.

This module lives at the top of ``tests/`` rather than inside one suite because the
foreign-key graph is a property of the **template database** (one ``create_all`` over one
``AegisBase.metadata``, see ``tests/conftest.py``), not of whichever suite happens to be
writing. Governance and memory therefore share one mechanism instead of two.

Two shapes are needed, because the suites write differently:

* :func:`seed`, :func:`ensure_tenants` and :func:`ensure_users` take the ``db`` fixture
  (an ``async_sessionmaker``), open their own session and commit. Rather than scatter a
  hand-written ``Tenant(...)`` in front of forty inserts, :func:`seed` derives the parents
  from the children: it reads the ``tenant_id``/``user_id`` each row carries and
  materialises exactly those. That keeps the tests saying what they are about (budget
  arithmetic, rollups, RBAC) while the database gets a schema-consistent set of rows, and
  it cannot drift — a test that starts referencing tenant 9 gets tenant 9.
* :func:`add_in_fk_order` adds into a session the test already holds open, which is how
  the memory suite writes (it keeps using ``s`` for the call under test afterwards).

Explicit primary keys are used for the derived parents so the ids in the assertions stay
the ids in the database; :func:`_resync_sequences` then re-points the identity sequences
past them, or the next ``Tenant(name=...)`` insert without an explicit id would collide
with a seeded row.
"""

from __future__ import annotations

from itertools import groupby
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from aegis.governance import Tenant, User
from aegis.memory.stores import MemorySession

__all__ = ["add_in_fk_order", "ensure_tenants", "ensure_users", "seed"]

#: Insert generations, parents first. Rows are flushed one generation at a time because
#: the mappers carry no ``relationship()``, so SQLAlchemy's unit of work has nothing to
#: derive an ordering from and falls back to the mapper sort key — which is the *class
#: path*. That puts ``MemoryMessage`` before ``MemorySession`` and ``Budget`` before
#: ``Tenant`` purely by luck of the alphabet, and both orders violate a live foreign key.
#: Anything not listed here references only listed tables, so it goes last.
_FK_GENERATIONS: tuple[tuple[type[Any], ...], ...] = (
    (Tenant, MemorySession),
    (User,),
)


def _generation(row: Any) -> int:  # noqa: ANN401 - any mapped ORM instance
    """Return the insert generation of one ORM row (lower is inserted earlier).

    Args:
        row: The ORM instance about to be persisted.

    Returns:
        Its index in :data:`_FK_GENERATIONS`, or one past the end for a leaf row.
    """
    for index, classes in enumerate(_FK_GENERATIONS):
        if isinstance(row, classes):
            return index
    return len(_FK_GENERATIONS)


async def add_in_fk_order(session: AsyncSession, *rows: Any) -> None:
    """Add ``rows`` to an open session parents-first, flushing between generations.

    The flush is the whole point: without it every row goes out in one INSERT batch
    ordered by mapper sort key, which is alphabetical rather than referential. Ordering
    is a property of this function rather than of a class name.

    Python's sort is stable, so rows within a generation keep the caller's order — which
    matters where the test asserts on ``turn_index`` or on generated ids.

    Args:
        session: An open session; the caller commits.
        rows: ORM instances to add.
    """
    for _, group in groupby(sorted(rows, key=_generation), key=_generation):
        session.add_all(list(group))
        await session.flush()


async def _resync_sequences(session: AsyncSession) -> None:
    """Re-point the ``tenants``/``users`` identity sequences past any explicit ids.

    ``INSERT … (id) VALUES (1)`` does not consume the serial sequence, so a later insert
    that lets the database allocate the id would hand out 1 again and raise a duplicate
    key. ``setval(seq, n, false)`` makes the *next* ``nextval`` return ``n``, so an empty
    table is left handing out 1 and a table seeded up to 4 resumes at 5. (Setting the
    sequence to ``max(id)`` with the default ``is_called=true`` was the earlier attempt
    and is off by one on an empty table: it skips id 1, which silently broke every test
    that seeds a tenant and then expects the user it adds to be user 1.)

    Args:
        session: An open session; the caller commits.
    """
    for table in ("tenants", "users"):
        await session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence(:table, 'id'), "
                f'coalesce((SELECT max(id) FROM "{table}"), 0) + 1, false)'
            ),
            {"table": table},
        )


async def _ensure(session: AsyncSession, tenants: set[int], users: dict[int, int | None]) -> None:
    """Insert the tenants and users that are referenced but absent.

    Args:
        session: An open session; the caller commits.
        tenants: Tenant ids that must exist.
        users: ``{user id: owning tenant id or None}`` that must exist.
    """
    missing: list[Any] = []
    if tenants:
        present = set(
            (await session.execute(select(Tenant.id).where(Tenant.id.in_(tenants))))
            .scalars()
            .all()
        )
        missing += [
            Tenant(id=tenant_id, name=f"seeded-tenant-{tenant_id}")
            for tenant_id in sorted(tenants - present)
        ]
    if users:
        present = set(
            (await session.execute(select(User.id).where(User.id.in_(users))))
            .scalars()
            .all()
        )
        missing += [
            User(
                id=user_id,
                username=f"seeded-user-{user_id}",
                tenant_id=users[user_id],
            )
            for user_id in sorted(set(users) - present)
        ]
    await add_in_fk_order(session, *missing)
    await _resync_sequences(session)


def _references(row: Any, column: str) -> bool:  # noqa: ANN401 - any mapped ORM instance
    """Report whether ``row``'s ``column`` is a real foreign key on its own table.

    Read off the mapped table rather than from a list of class names, so it cannot drift
    from the schema. It matters because ``tenant_id`` does *not* mean the same thing
    everywhere: on ``usage_ledger`` it references ``tenants.id``, while on the memory
    tables it is a bare integer (subjects are opaque host identifiers). Materialising a
    ``tenants`` row for the latter would invent a parent the schema never asked for — and
    a test that then counts tenants would see it.

    Args:
        row: The ORM instance about to be inserted.
        column: The column name to inspect.

    Returns:
        ``True`` when the column exists on the row's table and carries a foreign key.
    """
    mapped = type(row).__table__.columns.get(column)
    return mapped is not None and bool(mapped.foreign_keys)


def _referenced(rows: tuple[Any, ...]) -> tuple[set[int], dict[int, int | None]]:
    """Collect the tenant and user ids the given ORM rows point at.

    Rows that *are* a ``Tenant`` or a ``User`` are skipped as referents: they are the
    parents, and asking for them to be pre-created would fight the insert the test is
    making. Their own ``tenant_id`` (a user's owning tenant) is still collected.

    Args:
        rows: The ORM instances about to be inserted.

    Returns:
        The tenant ids, and a ``{user id: owning tenant id or None}`` mapping.
    """
    tenants: set[int] = set()
    users: dict[int, int | None] = {}
    for row in rows:
        tenant_id = getattr(row, "tenant_id", None)
        if tenant_id is not None and _references(row, "tenant_id"):
            tenants.add(int(tenant_id))
        if isinstance(row, User | Tenant):
            continue
        user_id = getattr(row, "user_id", None)
        if user_id is not None and _references(row, "user_id"):
            users[int(user_id)] = None if tenant_id is None else int(tenant_id)
    return tenants, users


async def seed(db, *rows: Any) -> None:  # noqa: ANN001 - async_sessionmaker
    """Insert ``rows`` after materialising every tenant and user they reference.

    Args:
        db: The suite's ``db`` fixture (an ``async_sessionmaker``).
        rows: ORM instances to persist.
    """
    tenants, users = _referenced(rows)
    async with db() as session:
        await _ensure(session, tenants, users)
        await add_in_fk_order(session, *rows)
        await session.commit()


async def ensure_tenants(db, *tenant_ids: int) -> None:  # noqa: ANN001 - async_sessionmaker
    """Create the named tenants, for tests whose writes go through production code.

    :func:`seed` can read the parents off the rows it is handed; a test that calls
    ``record_audit(tenant_id=3)`` hands nothing over, so it names the tenant here instead.

    Args:
        db: The suite's ``db`` fixture (an ``async_sessionmaker``).
        tenant_ids: The tenants that must exist before the call under test runs.
    """
    async with db() as session:
        await _ensure(session, set(tenant_ids), {})
        await session.commit()


async def ensure_users(db, **users: int | None) -> None:  # noqa: ANN001 - async_sessionmaker
    """Create the named users (keyed ``u<id>``), with their owning tenant.

    Keyword arguments are used because a mapping literal at every call site reads worse
    than ``ensure_users(db, u2=1)`` — "user 2 belongs to tenant 1".

    Args:
        db: The suite's ``db`` fixture (an ``async_sessionmaker``).
        users: ``u<id>=<tenant id or None>`` pairs.

    Raises:
        ValueError: If a key is not of the form ``u<integer>``.
    """
    parsed: dict[int, int | None] = {}
    tenants: set[int] = set()
    for key, tenant_id in users.items():
        if not key.startswith("u") or not key[1:].isdigit():
            raise ValueError(f"ensure_users keys must look like 'u7', got {key!r}")
        parsed[int(key[1:])] = tenant_id
        if tenant_id is not None:
            tenants.add(tenant_id)
    async with db() as session:
        await _ensure(session, tenants, parsed)
        await session.commit()
