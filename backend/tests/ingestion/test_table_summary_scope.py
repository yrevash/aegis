"""Two layers over the table-summary cache, each proved to be doing work on its own.

``keyword_recall`` is the reference standard in this repository: an application
``WHERE tenant_id = :ctx`` **and** a Postgres RLS policy, each mutation-proven separately.
``_cached`` — the read that decides whether one tenant's paid-for summary is handed to
another — carried only one of the two proofs. Deleting ``TableSummary.tenant_id ==
tenant_id`` from its ``select`` left 58 tests passing, because the ``db`` fixture's
``NOSUPERUSER NOBYPASSRLS`` role meant RLS silently held the boundary up on its own. A
predicate nothing can fail is a predicate that can be deleted in a refactor without a
single red test, on the exact read whose docstring says it is there "because the scope is
a property of the connection and this is a property of the query".

So this file asserts the two halves **separately**:

* the app predicate, with **no scope bound** — the RLS predicate's
  ``substring(<guc> from '^[0-9]+$')`` then yields NULL, which is the deliberate fail-open
  branch documented on ``_TENANT_ISOLATION_PREDICATE``, so nothing but the ``WHERE``
  clause is left to hold the line;
* the policy, with the *wrong* scope bound and the query asking for the other tenant's
  digest by name.

``test_table_summaries.py`` already proves the cache is per tenant end to end through the
chunk stage. This proves *why* it is, twice over.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.jobs import TableSummary
from sqlalchemy import select

from app.data import Tenant, get_sessionmaker, set_tenant_scope
from app.ingestion.tables import _cached

pytestmark = pytest.mark.asyncio

_TENANT_A = 9101
_TENANT_B = 9102
_DIGEST = "d" * 64

#: A string that exists nowhere else in this repository.
_SECRET = "AUDITA-QUAGGA-3317"


async def _seed() -> None:
    """One identical table digest, summarised independently by each tenant."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Summary tenant A"),
            Tenant(id=_TENANT_B, name="Summary tenant B"),
            TableSummary(
                tenant_id=_TENANT_A,
                digest=_DIGEST,
                summary="Tenant A's own reading of the rate card.",
                row_count=5,
                col_count=3,
                model_role="small",
            ),
            TableSummary(
                tenant_id=_TENANT_B,
                digest=_DIGEST,
                summary=f"Tenant B: {_SECRET} settlement schedule.",
                row_count=5,
                col_count=3,
                model_role="small",
            ),
        )
        await session.commit()


async def test_both_tenants_summaries_are_really_in_the_table(client, db) -> None:
    """Non-vacuity. Without this, every assertion below could pass on an empty table."""
    await _seed()
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(
                select(TableSummary.tenant_id).where(TableSummary.digest == _DIGEST)
            )
        ).scalars().all()
        await session.rollback()
    assert sorted(rows) == [_TENANT_A, _TENANT_B]


async def test_the_app_predicate_alone_holds_the_boundary_with_no_scope_bound(
    client, db
) -> None:
    """RLS is deliberately fail-open with no scope bound, so this is the ``WHERE`` clause.

    Binding nothing is not a contrived state: it is what an unscoped/platform request
    leaves on the connection, and what a pooled connection carries when a caller forgets
    to bind. On that connection the only thing between tenant A and tenant B's cached
    sentence is ``TableSummary.tenant_id == tenant_id``.
    """
    await _seed()
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, None)  # the fail-open branch, on purpose
        hits = await _cached(session, tenant_id=_TENANT_A, digests=[_DIGEST])
        await session.rollback()

    assert list(hits) == [_DIGEST]
    assert _SECRET not in hits[_DIGEST], (
        "with no RLS scope bound, the cache read returned another tenant's summary — "
        "the query carries no tenant predicate of its own"
    )
    assert hits[_DIGEST] == "Tenant A's own reading of the rate card."


async def test_the_policy_alone_holds_the_boundary_when_the_query_asks_for_the_other(
    client, db
) -> None:
    """The second layer: tenant A bound, a query that explicitly names tenant B's row.

    This is the shape the app predicate cannot catch — the predicate *is* the thing being
    subverted — so it is the policy that must return nothing.
    """
    await _seed()
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        rows = (
            await session.execute(
                select(TableSummary.summary).where(
                    TableSummary.tenant_id == _TENANT_B,
                    TableSummary.digest == _DIGEST,
                )
            )
        ).scalars().all()
        await session.rollback()

    assert rows == [], (
        f"tenant {_TENANT_A}'s bound scope read tenant {_TENANT_B}'s summary by naming "
        f"it: {rows}"
    )


async def test_each_tenant_still_reads_its_own_cached_summary(client, db) -> None:
    """Both directions, so a predicate hard-coded to one tenant would still fail."""
    await _seed()
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        mine = await _cached(session, tenant_id=_TENANT_A, digests=[_DIGEST])
        await session.rollback()
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_B)
        theirs = await _cached(session, tenant_id=_TENANT_B, digests=[_DIGEST])
        await session.rollback()

    assert mine[_DIGEST] != theirs[_DIGEST]
    assert _SECRET in theirs[_DIGEST]
    assert _SECRET not in mine[_DIGEST]
