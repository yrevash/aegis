"""``@tenant_activity`` — proved by making it refuse, and by making it filter.

The decorator's whole value is that it says **no**, so every assertion here is either a
raised exception or a row that a bound scope could not see. Nothing in this file asserts
that a function exists or is not ``None``.

Two halves:

* the refusals, which need no database — an activity with no argument, an argument with
  no ``tenant_id``, an argument type that declares no ``tenant_id`` (caught at decoration
  time), a body that never accepts the injected session, and ``tenant_id=None`` without an
  explicit opt-in;
* the filtering, which needs a real one. These run over the suite's ``NOSUPERUSER
  NOBYPASSRLS`` role, because the ``tenant_isolation`` policy is skipped entirely for a
  superuser: an isolation assertion made over the owner connection would pass with every
  policy dropped. Both tenants' rows are counted over the **owner** engine first, so
  "the activity saw one document" can never be "the insert quietly failed".

The last live test is the one the design argument turns on. ``set_tenant_scope`` writes a
*transaction-local* GUC, so an activity that commits half-way through would carry on
unscoped — and under the deliberately fail-open predicate an unscoped read returns every
tenant's rows without erroring. The test commits inside the activity and then reads again.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.jobs import Document, JobStatus
from aegis.jobs.scope import (
    ActivityInput,
    MissingTenantScopeError,
    SessionFactoryNotConfiguredError,
    reset_activity_session_factory,
    set_activity_session_factory,
    tenant_activity,
)

from .._seed import ensure_tenants

#: The two tenants every live assertion is written against. Seven is the id the phase
#: file names, and eight is the neighbour it must not be able to see.
_TENANT_A = 7
_TENANT_B = 8


@dataclass(frozen=True, slots=True)
class _Input(ActivityInput):
    """A well-formed activity argument: it carries the tenant, as every one must."""

    note: str = ""


@dataclass(frozen=True, slots=True)
class _TenantlessInput:
    """A malformed activity argument — no ``tenant_id`` anywhere on it."""

    workflow_id: str


@pytest.fixture(autouse=True)
def _clean_session_factory():
    """Leave no session factory bound between tests.

    A factory left pointing at a disposed engine surfaces three files later as an error
    about a closed pool, which is a genuinely miserable thing to debug.
    """
    reset_activity_session_factory()
    yield
    reset_activity_session_factory()


def _document(tenant_id: int, *, filename: str) -> Document:
    """Build one uploaded-but-unparsed document for a tenant."""
    return Document(
        tenant_id=tenant_id,
        filename=filename,
        content_sha256=f"{tenant_id:064d}",
        mime_type="application/pdf",
        size_bytes=1024,
        status=JobStatus.PENDING,
    )


async def _seed_both_tenants(pg_sessionmaker, pg_owner_engine) -> None:
    """Give each tenant exactly one document, and prove both rows landed.

    Seeded over the **owner** engine so the writes are not themselves subject to the
    policy under test, then counted over the same connection: an isolation test whose
    "other tenant" row never existed proves nothing at all.
    """
    await ensure_tenants(pg_sessionmaker, _TENANT_A, _TENANT_B)
    owner_sessions = async_sessionmaker(pg_owner_engine, expire_on_commit=False)
    async with owner_sessions() as session:
        session.add(_document(_TENANT_A, filename="tenant-a.pdf"))
        session.add(_document(_TENANT_B, filename="tenant-b.pdf"))
        await session.commit()
    async with pg_owner_engine.connect() as conn:
        total = await conn.scalar(select(func.count()).select_from(Document.__table__))
    assert total == 2, f"expected both tenants' documents to exist, found {total}"


# ─────────────────────────────────────────────────────────────────────────────
# The refusals
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_activity_called_with_no_argument_refuses_to_run():
    ran = False

    @tenant_activity
    async def activity(inp: _Input, *, session) -> str:
        nonlocal ran
        ran = True
        return "did work"

    with pytest.raises(MissingTenantScopeError) as raised:
        await activity()

    assert "tenant_id" in str(raised.value)
    assert ran is False, "the body ran despite there being no tenant to scope it to"


async def test_an_argument_carrying_no_tenant_field_refuses_to_run():
    @tenant_activity
    async def activity(inp: _Input, *, session) -> str:
        return "did work"

    # Bypasses the decoration-time check on purpose: the annotation is fine, the *value*
    # passed at run time is not. This is the shape a deserialisation bug produces.
    with pytest.raises(MissingTenantScopeError) as raised:
        await activity(_TenantlessInput(workflow_id="wf-1"))

    assert "_TenantlessInput" in str(raised.value)


def test_an_argument_type_declaring_no_tenant_is_refused_at_decoration_time():
    with pytest.raises(MissingTenantScopeError) as raised:

        @tenant_activity
        async def activity(inp: _TenantlessInput, *, session) -> None: ...

    assert "tenant_id" in str(raised.value)


def test_an_activity_with_no_positional_parameter_is_refused_at_decoration_time():
    with pytest.raises(MissingTenantScopeError):

        @tenant_activity
        async def activity(*, session) -> None: ...


def test_an_activity_that_does_not_accept_the_injected_session_is_refused():
    # An activity that took no session would have to open one itself — the unscoped read
    # this decorator exists to make impossible.
    with pytest.raises(TypeError) as raised:

        @tenant_activity
        async def activity(inp: _Input) -> None: ...

    assert "session" in str(raised.value)


def test_a_synchronous_activity_is_refused():
    with pytest.raises(TypeError):

        @tenant_activity
        def activity(inp: _Input, *, session) -> None: ...


async def test_platform_scope_is_never_implicit():
    @tenant_activity
    async def activity(inp: _Input, *, session) -> None: ...

    with pytest.raises(MissingTenantScopeError) as raised:
        await activity(_Input(tenant_id=None, workflow_id="wf-1"))

    assert "allow_platform_scope" in str(raised.value)


async def test_platform_scope_runs_when_it_is_declared(pg_sessionmaker):
    set_activity_session_factory(pg_sessionmaker)
    seen: list[str | None] = []

    @tenant_activity(allow_platform_scope=True)
    async def activity(inp: _Input, *, session) -> None:
        seen.append(await session.scalar(text("SELECT current_setting('app.tenant_id')")))

    await activity(_Input(tenant_id=None, workflow_id="wf-1"))

    # The empty string, not "unset": that is precisely what ``set_tenant_scope`` writes
    # for an unscoped request, and it is the value the policy's substring test reads as
    # "no numeric scope bound".
    assert seen == [""]


async def test_the_billing_scope_is_bound_from_the_same_field_as_the_row_scope(
    pg_sessionmaker,
):
    """An activity's model spend is capped and ledgered, because the tenant is bound.

    The defect this pins (task 9.2): every activity bound the *row* scope and nothing
    bound the *billing* scope, so a stage that embedded two hundred chunks did it with
    no governance context — and the gateway caps and ledgers exactly what a bound
    context names. Ingestion therefore spent a tenant's money against no cap and left no
    ``usage_ledger`` row, and the volume of that grows with every document uploaded.

    Both scopes now come from one field on the activity's own argument, which is what
    makes them impossible to disagree — and what makes them survive a replay in a fresh
    worker, where a contextvar set by the enqueuer would not exist at all.
    """
    from aegis.governance.context import get_governance_context

    set_activity_session_factory(pg_sessionmaker)
    seen = []

    @tenant_activity
    async def activity(inp: _Input, *, session) -> None:
        seen.append(get_governance_context())

    await activity(_Input(tenant_id=_TENANT_A, workflow_id="wf-1"))

    assert seen and seen[0] is not None, "no governance context — spend is uncapped"
    assert seen[0].tenant_id == _TENANT_A
    # And unbound again afterwards, so the next activity on this worker — very possibly
    # another tenant's — is not billed to this one.
    assert get_governance_context() is None


async def test_an_activity_with_no_session_factory_wired_says_so():
    @tenant_activity
    async def activity(inp: _Input, *, session) -> None: ...

    with pytest.raises(SessionFactoryNotConfiguredError) as raised:
        await activity(_Input(tenant_id=_TENANT_A, workflow_id="wf-1"))

    assert "set_activity_session_factory" in str(raised.value)


def test_the_wrapped_activity_hides_the_injected_session_from_introspection():
    import inspect

    @tenant_activity
    async def activity(inp: _Input, *, session) -> str: ...

    signature = inspect.signature(activity)

    # An orchestrator SDK reads this signature to decide how to deserialise the payload,
    # and Temporal rejects keyword-only parameters outright. The session must not appear.
    assert list(signature.parameters) == ["inp"]
    # And the annotations must be resolved types, not the strings ``from __future__
    # import annotations`` leaves behind: the wrapper's globals are the decorator's
    # module, where ``_Input`` does not exist.
    assert activity.__annotations__["inp"] is _Input
    assert "session" not in activity.__annotations__


# ─────────────────────────────────────────────────────────────────────────────
# The filtering, over the unprivileged role
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_activity_for_tenant_7_sees_only_tenant_7s_rows(
    pg_sessionmaker, pg_owner_engine
):
    await _seed_both_tenants(pg_sessionmaker, pg_owner_engine)
    set_activity_session_factory(pg_sessionmaker)

    @tenant_activity
    async def list_documents(inp: _Input, *, session) -> list[tuple[int, str]]:
        rows = await session.execute(select(Document.tenant_id, Document.filename))
        return [(row[0], row[1]) for row in rows]

    visible = await list_documents(_Input(tenant_id=_TENANT_A, workflow_id="wf-a"))

    assert visible == [(_TENANT_A, "tenant-a.pdf")]


async def test_the_other_tenants_activity_sees_the_other_row(
    pg_sessionmaker, pg_owner_engine
):
    # The mirror image, so the previous test cannot be passing because the activity sees
    # nothing at all — a filter that hides everything would also hide tenant B's row.
    await _seed_both_tenants(pg_sessionmaker, pg_owner_engine)
    set_activity_session_factory(pg_sessionmaker)

    @tenant_activity
    async def list_documents(inp: _Input, *, session) -> list[str]:
        return list(await session.scalars(select(Document.filename)))

    assert await list_documents(_Input(tenant_id=_TENANT_B, workflow_id="wf-b")) == [
        "tenant-b.pdf"
    ]


async def test_the_scope_survives_a_commit_inside_the_activity(
    pg_sessionmaker, pg_owner_engine
):
    await _seed_both_tenants(pg_sessionmaker, pg_owner_engine)
    set_activity_session_factory(pg_sessionmaker)

    @tenant_activity
    async def read_commit_read(inp: _Input, *, session) -> list[list[str]]:
        before = list(await session.scalars(select(Document.filename)))
        # ``set_tenant_scope`` binds the GUC with ``is_local=true``, so this commit
        # discards it. Without the per-transaction re-bind the next read runs unscoped —
        # and unscoped reads do not error, they return everybody's rows.
        await session.commit()
        after = list(await session.scalars(select(Document.filename)))
        return [before, after]

    before, after = await read_commit_read(_Input(tenant_id=_TENANT_A, workflow_id="wf-a"))

    assert before == ["tenant-a.pdf"]
    assert after == ["tenant-a.pdf"], (
        "the tenant scope was lost after the activity committed: the second read saw "
        f"{after}"
    )


async def test_a_failing_activity_commits_nothing(pg_sessionmaker, pg_owner_engine):
    await _seed_both_tenants(pg_sessionmaker, pg_owner_engine)
    set_activity_session_factory(pg_sessionmaker)

    class _Boom(RuntimeError):
        pass

    @tenant_activity
    async def half_write(inp: _Input, *, session) -> None:
        document = _document(inp.tenant_id, filename="never-committed.pdf")
        document.content_sha256 = "f" * 64
        session.add(document)
        await session.flush()
        raise _Boom("the stage failed after writing its output")

    with pytest.raises(_Boom):
        await half_write(_Input(tenant_id=_TENANT_A, workflow_id="wf-a"))

    # Read back over the owner engine, which bypasses the policy: if the row had been
    # committed it would be here regardless of scope.
    async with pg_owner_engine.connect() as conn:
        filenames = list(await conn.scalars(select(Document.filename)))
    assert "never-committed.pdf" not in filenames


async def test_a_successful_activity_commits_its_write(pg_sessionmaker, pg_owner_engine):
    # The positive control for the test above: the rollback must be a property of the
    # failure, not of the decorator never committing anything.
    await _seed_both_tenants(pg_sessionmaker, pg_owner_engine)
    set_activity_session_factory(pg_sessionmaker)

    @tenant_activity
    async def write(inp: _Input, *, session) -> None:
        document = _document(inp.tenant_id, filename="committed.pdf")
        document.content_sha256 = "c" * 64
        session.add(document)

    await write(_Input(tenant_id=_TENANT_A, workflow_id="wf-a"))

    async with pg_owner_engine.connect() as conn:
        filenames = sorted(await conn.scalars(select(Document.filename)))
    assert "committed.pdf" in filenames
