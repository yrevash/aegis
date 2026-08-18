"""The re-index handler: rebuilds from the parse artifact, never from the bytes (task 4.13).

Phase 3 shipped the cadence, the per-tenant fold and the record row with **no handler
behind them**, on the grounds that a ``succeeded`` row for work nothing performed is worse
than no row at all. This file is about the handler that fills that hole, and about the two
properties that make it worth having.

**It does not re-parse.** The claim is proved by a spy on ``parse_pdf`` that fails the test
if it is called at all, rather than by a stopwatch: a re-index that happened to be fast
would pass a timing assertion on a warm cache and still be re-deriving a 126-page document
somewhere else. Not-called is a fact; fast is a symptom. The counterpart assertion is that
the rebuild genuinely happened — the embedder is *swapped* between the ingest and the
re-index, so the vectors on the rows afterwards can only be the new one's.

**The fold still holds.** The last test drives ten requests through a real Temporal dev
server, the platform's own worker bootstrap and the real handler registered exactly as
:func:`app.ingestion.reindex.register_corpus_reindex_handler` registers it. Phase 3 proved
this with a handler that recorded a call and returned a dict; a handler that opens a
transaction, walks a corpus and writes to it is a different thing to fold, so the guarantee
is re-proved rather than inherited.
"""

from __future__ import annotations

import asyncio

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Chunk, Document, JobRun, JobStatus
from aegis.retrieval.corpus import corpus_version, reset_corpus_versions
from aegis.retrieval.graph_extract import Entity, Relation
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment
from tests.jobs.conftest import free_port, skip_without_temporal, temporal_cli_path

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion import stages as stages_module
from app.ingestion.reindex import (
    REINDEX_STAGES,
    register_corpus_reindex_handler,
    reindex_corpus,
)
from app.ingestion.stages import IngestDependencies, set_ingest_dependencies
from app.jobs.activities import finish_ingest, run_stage
from app.jobs.client import reset_temporal_client, set_temporal_client
from app.jobs.flows.contracts import (
    FinishInput,
    ReindexInput,
    ReindexResult,
    StageInput,
)
from app.jobs.flows.reindex import reindex_workflow_id
from app.jobs.reindex import (
    REINDEX_JOB_TYPE,
    clear_reindex_handler,
    request_reindex,
)
from app.jobs.worker import start_worker_task

from .conftest import FIXTURE

pytestmark = pytest.mark.asyncio

_TENANT = 1
_USER = 11

#: The debounce window the fold test uses, and the ceiling on folding. Same reasoning as
#: ``tests/jobs/test_debounce.py``: wide enough that ten back-to-back requests land inside
#: it on any machine, narrow enough that the test is seconds rather than a minute.
_WINDOW = 3
_MAX_WAIT = 120


@pytest.fixture(autouse=True)
def _clean_corpus_versions():
    """Keep the process-wide corpus-version counters out of neighbouring tests."""
    reset_corpus_versions()
    yield
    reset_corpus_versions()


@pytest.fixture(autouse=True)
def _clean_reindex_handler():
    """Never leave this file's real handler registered for the next test to inherit."""
    clear_reindex_handler()
    yield
    clear_reindex_handler()


class _StubExtractor:
    """A graph extractor with no spaCy and no model behind it."""

    name = "stub-extractor"

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return one entity per chunk and no relations."""
        return ([Entity.make("Transformer", "product")], [])


class _MarkedEmbed:
    """A deterministic embedder whose vectors say which embedder produced them.

    The marker is the point: "the re-index really re-embedded" is otherwise
    indistinguishable from "the re-index did nothing and the old vectors are still there",
    and those two are exactly what this file has to tell apart.
    """

    def __init__(self, marker: float) -> None:
        self.marker = marker
        self.texts: list[str] = []

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Return one marked vector per text, recording what it was asked for."""
        self.texts.extend(texts)
        return [[self.marker, float(len(text))] for text in texts]


class _RecordingPublisher:
    """Stands in for the knowledge backend, recording what was published."""

    def __init__(self) -> None:
        self.calls: list[list[object]] = []

    async def __call__(self, chunks) -> None:  # noqa: ANN001 - Sequence[RetrievalChunk]
        """Record one publish."""
        self.calls.append(list(chunks))


class _ParseSpy:
    """A ``parse_pdf`` replacement that fails loudly if anything calls it."""

    def __init__(self) -> None:
        self.calls: list[object] = []

    def __call__(self, path, *args: object, **kwargs: object):  # noqa: ANN001, ANN204
        """Record the call and refuse to parse."""
        self.calls.append(path)
        raise AssertionError(
            f"the parser was called for {path}; a re-index must rebuild from the stored "
            "parse artifact, not re-derive a tree that is already on disk"
        )


def _headers() -> dict[str, str]:
    """A tenant-admin bearer for the one tenant these tests use."""
    token = create_access_token(
        user_id=_USER, username="a-admin", role=TENANT_ADMIN, tenant_id=_TENANT
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenant() -> None:
    """One tenant, one admin, one budget generous enough to admit the ingest."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Tenant A"),
            User(id=_USER, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT),
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


async def _ingest(client, artifact, store) -> int:
    """Upload the fixture, seed its parse artifact and run the ingest to a close-out.

    The ``parse`` stage is not run: its *output* is seeded straight into the store, which
    is what a completed parse leaves behind. That keeps the fixture cheap and, more
    usefully, means any later call to the parser in this file is unambiguously the
    re-index's doing.

    Returns:
        The ingested document's id.
    """
    data, payload = artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    document_id = body["document_id"]
    workflow_id = f"ingest:{_TENANT}:{document_id}"
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=payload
    )
    for stage in REINDEX_STAGES:
        await run_stage(
            StageInput(
                tenant_id=_TENANT,
                workflow_id=workflow_id,
                document_id=document_id,
                stage=stage,
            )
        )
    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT,
            workflow_id=workflow_id,
            document_id=document_id,
            status=JobStatus.SUCCEEDED.value,
        )
    )
    return document_id


async def _seed_ingested_document(db, store, artifact) -> int:
    """Insert a fully-ingested ``documents`` row and its parse artifact, with no route.

    The fold test cannot go through ``POST /documents``: by then a *real* Temporal client
    is installed, so the route would start a real ingest workflow and the re-index under
    test would be racing an ingest it did not ask for. Seeding the row is what an ingest
    leaves behind, minus the orchestration.

    Returns:
        The document's id.
    """
    import hashlib

    data, payload = artifact
    sha = hashlib.sha256(data).hexdigest()
    async with db() as session:
        document = Document(
            tenant_id=_TENANT,
            filename=FIXTURE,
            content_sha256=sha,
            mime_type="application/pdf",
            size_bytes=len(data),
            status=JobStatus.SUCCEEDED,
            completed_stage=REINDEX_STAGES[-1],
        )
        session.add(document)
        await session.commit()
        document_id = document.id
    store.put_artifact(tenant_id=_TENANT, sha256=sha, payload=payload)
    return document_id


async def _chunks(document_id: int) -> list[Chunk]:
    """Read a document's chunks back over the serving role, in insertion order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        return list(
            (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.id)
                )
            )
            .scalars()
            .all()
        )


async def _reindex(workflow_id: str = "reindex:1", *, folded: int = 1) -> ReindexResult:
    """Run one re-index through the real activity, as the workflow would."""
    from app.jobs.reindex import run_reindex

    return await run_reindex(
        ReindexInput(
            tenant_id=_TENANT,
            workflow_id=workflow_id,
            folded=folded,
            reasons=tuple(f"reason {index}" for index in range(folded)),
        )
    )


# ── the headline: rebuilt, and not re-parsed ─────────────────────────────────


async def test_a_reindex_rebuilds_from_the_stored_artifact_without_re_parsing(
    client, db, wired, store, temporal, parsed_artifact, monkeypatch
) -> None:
    """The index is rebuilt under the new embedder and the parser is never touched.

    Both halves are needed. "The parser was not called" alone is satisfied by a handler
    that does nothing at all; "the vectors changed" alone is satisfied by one that
    re-parses first. Together they say the only thing worth saying: the expensive stage
    was skipped **because its output was already on disk**, and every stage that depends
    on something mutable really did run again.
    """
    first = _MarkedEmbed(1.0)
    publisher = _RecordingPublisher()
    set_ingest_dependencies(
        IngestDependencies(
            store=store, embed=first, publish=publisher, extractor=_StubExtractor()
        )
    )
    await _seed_tenant()
    register_corpus_reindex_handler()
    document_id = await _ingest(client, parsed_artifact, store)
    before = await _chunks(document_id)
    assert before, "the fixture ingest produced no chunks, so nothing below means anything"
    assert all(chunk.embedding[0] == 1.0 for chunk in before)
    assert len(publisher.calls) == 1

    # The corpus is now the parse artifact plus the rows above. Swap the embedder — the
    # exact thing a re-index exists for — and make any parse an outright failure.
    spy = _ParseSpy()
    monkeypatch.setattr(stages_module, "parse_pdf", spy)
    second = _MarkedEmbed(2.0)
    set_ingest_dependencies(
        IngestDependencies(
            store=store, embed=second, publish=publisher, extractor=_StubExtractor()
        )
    )

    result = await _reindex()

    assert spy.calls == [], (
        "the re-index re-parsed the document; at 0.43–3.20 s/page that is minutes spent "
        "re-deriving a tree the parse stage already wrote beside the bytes"
    )
    after = await _chunks(document_id)
    assert [chunk.id for chunk in after] != [], "the re-index left the document with no chunks"
    assert len(after) == len(before)
    assert all(chunk.embedding[0] == 2.0 for chunk in after), (
        "the chunks still carry the first embedder's vectors, so the re-index did not "
        "actually re-embed anything"
    )
    assert second.texts, "the swapped-in embedder was never called"
    # Every stage that depends on something mutable ran again, not just ``embed``.
    assert len(publisher.calls) == 2
    assert all(chunk.content.startswith(chunk.meta["prefix"]) for chunk in after)
    assert all(chunk.meta["extractor"] == "stub-extractor" for chunk in after), (
        "the graph metadata is gone: ``chunk`` is delete-then-insert, so a re-index that "
        "stops before ``graph`` silently empties one of the three retrieval arms"
    )
    assert result.tenant_id == _TENANT

    async with db() as session:
        row = (
            await session.execute(select(JobRun).where(JobRun.id == result.job_run_id))
        ).scalar_one()
    assert row.job_type == REINDEX_JOB_TYPE
    assert row.status is JobStatus.SUCCEEDED
    assert row.result["documents"] == 1
    assert row.result["chunks"] == len(after)
    assert row.result["stages"] == list(REINDEX_STAGES)


async def test_a_reindex_bumps_the_corpus_version(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """A corpus rebuilt under a new embedder answers differently, so the cache must miss.

    The re-index has exactly the same relationship to a cached answer that an upload does:
    the answer was computed over an index that no longer exists. One bump per run, not one
    per document — the counter is per tenant, so N bumps would be one invalidation done N
    times.
    """
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=_MarkedEmbed(1.0),
            publish=_RecordingPublisher(),
            extractor=_StubExtractor(),
        )
    )
    await _seed_tenant()
    register_corpus_reindex_handler()
    await _ingest(client, parsed_artifact, store)
    assert corpus_version(_TENANT) == 1  # the ingest's own bump

    await _reindex()

    assert corpus_version(_TENANT) == 2
    assert corpus_version(2) == 0, "the re-index moved a tenant it was not asked about"


async def test_re_running_a_reindex_leaves_exactly_one_set_of_chunks(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Idempotent: the second run rebuilds the same corpus rather than doubling it.

    The re-index is retried by the orchestrator (``maximum_attempts=3``) and re-fired by
    the cadence every night, so "runs twice" is the normal case and not the edge one.
    """
    embed = _MarkedEmbed(1.0)
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=embed,
            publish=_RecordingPublisher(),
            extractor=_StubExtractor(),
        )
    )
    await _seed_tenant()
    register_corpus_reindex_handler()
    document_id = await _ingest(client, parsed_artifact, store)
    baseline = await _chunks(document_id)

    await _reindex("reindex:1")
    once = await _chunks(document_id)
    await _reindex("reindex:1")
    twice = await _chunks(document_id)

    assert len(once) == len(baseline)
    assert len(twice) == len(baseline)
    assert [chunk.content for chunk in twice] == [chunk.content for chunk in once], (
        "the second re-index changed the chunk text, so the enrich guard is prefixing "
        "already-prefixed content"
    )
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
    assert document.chunk_count == len(twice)


async def test_a_reindex_of_a_tenant_with_nothing_ingested_rebuilds_nothing(
    db, wired, store
) -> None:
    """No documents means no bump: an empty corpus cannot have gone stale.

    The cadence's own visibility check already declines to request a run in this case, so
    reaching here means something else asked. Recording "0 documents" is the honest
    outcome; bumping would throw the tenant's cache away for a rebuild that did not occur.
    """
    await _seed_tenant()
    async with db() as session:
        await set_tenant_scope(session, _TENANT)
        result = await reindex_corpus(
            session, tenant_id=_TENANT, folded=1, reasons=("cadence",)
        )
        await session.commit()

    assert result["documents"] == 0
    assert result["corpus_version"] is None
    assert corpus_version(_TENANT) == 0


# ── Phase 3's fold, re-proved against the real handler ───────────────────────


@pytest.fixture
async def reindex_env(wired):
    """A Temporal dev server with the platform's own workers running against it.

    Goes through :func:`app.jobs.worker.start_worker_task` — the same call the API's
    lifespan makes — so the fold is proved on the shipped bootstrap rather than on a
    test-local imitation of one.
    """
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that ten re-index requests inside the debounce window still fold into a "
            "single run now that the real corpus re-index handler performs the work."
        )
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary, port=free_port(), ui=False
    )
    set_temporal_client(env.client)
    stop = asyncio.Event()
    worker = start_worker_task(stop)
    try:
        yield env
    finally:
        stop.set()
        await asyncio.wait_for(worker, timeout=30)
        reset_temporal_client()
        await env.shutdown()


async def test_ten_requests_fold_into_one_run_of_the_real_handler(
    db, wired, store, parsed_artifact, reindex_env
) -> None:
    """Phase 3's guarantee, asserted against work that actually touches the database.

    A broken debounce would still leave one ``job_runs`` row — the write is an upsert on a
    workflow id that is reused for every window — so the row count proves nothing on its
    own. What proves it is the corpus version: the handler bumps once per run, so a
    version that moved by exactly one is ten requests having produced one rebuild.
    """
    embed = _MarkedEmbed(1.0)
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=embed,
            publish=_RecordingPublisher(),
            extractor=_StubExtractor(),
        )
    )
    await _seed_tenant()
    register_corpus_reindex_handler()
    document_id = await _seed_ingested_document(db, store, parsed_artifact)
    assert corpus_version(_TENANT) == 0

    handles = []
    run_ids = set()
    for index in range(10):
        handle = await request_reindex(
            reindex_env.client,
            tenant_id=_TENANT,
            reason=f"document {index} ingested",
            debounce_seconds=_WINDOW,
            max_wait_seconds=_MAX_WAIT,
        )
        handles.append(handle)
        run_ids.add((await handle.describe()).run_id)
    result: ReindexResult = await asyncio.wait_for(
        handles[-1].result(), timeout=_WINDOW + 60
    )

    assert {handle.id for handle in handles} == {reindex_workflow_id(_TENANT)}
    assert len(run_ids) == 1, f"the ten requests started more than one execution: {run_ids}"
    assert result.folded == 10
    assert corpus_version(_TENANT) == 1, (
        "the corpus version moved by something other than one, so the ten requests did "
        "not fold into a single rebuild"
    )
    chunks = await _chunks(document_id)
    assert chunks, "the folded run recorded a success without rebuilding anything"
    async with db() as session:
        rows = list(
            (
                await session.execute(
                    select(JobRun).where(JobRun.job_type == REINDEX_JOB_TYPE)
                )
            ).scalars()
        )
    assert len(rows) == 1
    assert rows[0].result["documents"] == 1
    assert rows[0].result["chunks"] == len(chunks)
    assert rows[0].payload["folded"] == 10
