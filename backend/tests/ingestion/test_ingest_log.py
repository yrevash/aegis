"""The live ingest log (4.12) and the graph made visible (4.12b), on a real ingest.

Every assertion here is against the projection reading rows a real stage really wrote —
a real PDF through the real upload route, the real stage-runner activity under a bound
tenant scope, a real PostgreSQL — because the one thing this surface must never do is
report progress from something held in memory.

The load-bearing test is :func:`test_a_resumed_document_neither_invents_nor_un_commits`.
A worker that dies mid-ingest and is replaced takes every in-process fact with it; what
survives is ``documents.completed_stage`` and the ``run_events`` rows committed with it.
So the projection is driven from a row set here by hand — exactly the state a killed
worker leaves — and is required to name the same stages a running one would.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.ingestion.quality import LOW_CONFIDENCE
from aegis.jobs import Document
from aegis.jobs.stages import UnknownStageError, remaining_stages, stage_names
from aegis.retrieval.graph_extract import Entity, Relation
from aegis.runs.models import RunEvent
from sqlalchemy import select, update

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.progress import (
    COMPLETED,
    FAILED,
    QUEUED,
    RUNNING,
    ingest_progress,
    project_stages,
)
from app.ingestion.stages import IngestDependencies, set_ingest_dependencies
from app.jobs.activities import finish_ingest, run_stage, start_ingest
from app.jobs.flows.contracts import FinishInput, StageInput
from app.jobs.ingest_log import INGEST_FINISHED_EVENT, INGEST_STAGE_EVENT

from .conftest import FIXTURE, fixture_pdf

pytestmark = pytest.mark.asyncio

_TENANT = 1
_USER = 11
_OTHER_TENANT = 2
_OTHER_USER = 22

_STAGES = stage_names()


class _StubEmbed:
    """A deterministic embedder — the vectors are irrelevant, the count is not."""

    async def __call__(self, texts: list[str]) -> list[list[float]]:
        """Return one two-element vector per text."""
        return [[float(len(text)), 1.0] for text in texts]


class _NullPublisher:
    """Stands in for the knowledge backend; the index stage's write is not under test."""

    async def __call__(self, chunks) -> None:  # noqa: ANN001 - Sequence[RetrievalChunk]
        """Accept a publish and do nothing with it."""


class _FixedExtractor:
    """One entity on every chunk, plus an edge where the text mentions attention.

    Fixed rather than real so the numbers the projection reports are numbers this test
    can state: what is under test is that ``chunks.meta`` reaches the screen, not how
    good spaCy is.
    """

    name = "fixed-test-extractor"

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return the entities and relations for one chunk."""
        model = Entity.make("Transformer", "product")
        if "attention" in chunk_text.lower():
            other = Entity.make("Attention", "procedure")
            return ([model, other], [Relation(model.id, other.id, "is built from")])
        return ([model], [])


def _headers(*, tenant_id: int = _TENANT, username: str = "a-admin", user_id: int = _USER):
    """A tenant-admin bearer for one of the two seeded tenants."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants() -> None:
    """Two tenants with admins and budgets, so isolation is assertable."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Tenant A"),
            Tenant(id=_OTHER_TENANT, name="Tenant B"),
            User(id=_USER, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT),
            User(
                id=_OTHER_USER,
                username="b-admin",
                role=Role.ADMIN,
                tenant_id=_OTHER_TENANT,
            ),
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
            Budget(
                tenant_id=_OTHER_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_OTHER_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


def _wire(store) -> None:
    """Install the collaborators the money-spending stages reach outside the DB for."""
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            embed=_StubEmbed(),
            publish=_NullPublisher(),
            extractor=_FixedExtractor(),
        )
    )


async def _upload(client, data: bytes, *, tenant_id: int = _TENANT, **who) -> dict:
    """Upload the fixture through the real route and return the response body."""
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(tenant_id=tenant_id, **who),
        data={"doc_type": "research paper", "doc_date": "2017-06-12"},
    )
    assert res.status_code == 200, res.text
    return res.json()


async def _start(document_id: int, *, tenant_id: int = _TENANT) -> None:
    """Claim the run exactly as the workflow's first activity does.

    Not a formality: ``start_ingest`` is what writes the ``job_runs`` row and moves the
    document to ``RUNNING``, and "which stage is a worker inside right now" is a question
    only a running job has an answer to.
    """
    await start_ingest(
        StageInput(
            tenant_id=tenant_id,
            workflow_id=f"ingest:{tenant_id}:{document_id}",
            document_id=document_id,
            stage="ingest",
        )
    )


async def _run(stage: str, *, document_id: int, tenant_id: int = _TENANT) -> None:
    """Run one stage through the real activity, exactly as the workflow would."""
    await run_stage(
        StageInput(
            tenant_id=tenant_id,
            workflow_id=f"ingest:{tenant_id}:{document_id}",
            document_id=document_id,
            stage=stage,
        )
    )


async def _progress(document_id: int, *, tenant_id: int = _TENANT):
    """Read the projection back over the serving role, under its own tenant scope."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return await ingest_progress(
            session, document_id=document_id, tenant_id=tenant_id
        )


async def _set(document_id: int, **values) -> None:
    """Write columns onto a document directly — the state a killed worker leaves behind."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        await session.execute(
            update(Document).where(Document.id == document_id).values(**values)
        )
        await session.commit()


async def _events(document_id: int) -> list[RunEvent]:
    """Read this document's durable run events back, in commit order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        return list(
            (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == f"ingest:{_TENANT}:{document_id}")
                    .order_by(RunEvent.id)
                )
            )
            .scalars()
            .all()
        )


def _states(progress) -> dict[str, str]:
    """Map stage name to reported state, for a readable assertion."""
    return {stage.name: stage.state for stage in progress.stages}


# ─────────────────────────────────────────────────────────────────────────────
# The stage projection, as a pure function over the durable row
# ─────────────────────────────────────────────────────────────────────────────


def test_nothing_committed_means_every_stage_is_owed() -> None:
    """A freshly uploaded document has run nothing, and must not claim otherwise."""
    stages = project_stages(
        completed_stage=None, status="pending", events_by_stage={}
    )

    assert [stage.name for stage in stages] == list(_STAGES)
    assert {stage.state for stage in stages} == {QUEUED}


def test_exactly_one_stage_is_running_and_it_is_the_next_one_owed() -> None:
    """A running job is inside the first stage the resume arithmetic still owes."""
    stages = project_stages(
        completed_stage="enrich", status="running", events_by_stage={}
    )

    assert _stage_map(stages) == {
        "parse": COMPLETED,
        "chunk": COMPLETED,
        "enrich": COMPLETED,
        "embed": RUNNING,
        "index": QUEUED,
        "graph": QUEUED,
    }


def test_a_failed_job_has_no_running_stage_but_does_name_the_one_that_broke() -> None:
    """The stage it died in did not commit, so calling it running would be the lie.

    It is not ``queued`` either, and that half was the audit's finding (C2): with
    ``embed`` failing, ``embed``, ``index`` and ``graph`` all rendered ``queued`` —
    identical, though two of the three had never been attempted — so the only stage the
    log named was ``enrich``, the last one that *succeeded*, and every reader concluded
    ``enrich`` was broken.
    """
    stages = project_stages(
        completed_stage="enrich", status="failed", events_by_stage={}
    )

    assert RUNNING not in {stage.state for stage in stages}
    assert _stage_map(stages) == {
        "parse": COMPLETED,
        "chunk": COMPLETED,
        "enrich": COMPLETED,
        "embed": FAILED,
        "index": QUEUED,
        "graph": QUEUED,
    }


def test_a_document_whose_workflow_never_started_blames_no_stage() -> None:
    """A FAILED document with no execution behind it attempted nothing.

    ``POST /documents`` stores the bytes and then starts the workflow; when the start
    fails the row is closed FAILED with the reason on it and **no** ``job_runs`` row is
    ever written. Marking ``parse`` as failed there would invent an attempt nobody made.
    """
    stages = project_stages(
        completed_stage=None, status="failed", events_by_stage={}, started=False
    )

    assert {stage.state for stage in stages} == {QUEUED}


def test_an_event_can_never_promote_a_stage_the_row_has_not_committed() -> None:
    """The row decides what completed; the log only decorates what the row already said.

    The two agree in production because they commit together. This asserts the
    *direction* of the dependency, which is what makes a lost log entry cost a detail
    panel rather than a whole stage's worth of progress.
    """
    stages = project_stages(
        completed_stage="chunk",
        status="running",
        events_by_stage={
            "graph": {"ts": None, "duration_ms": 5, "detail": {"entities": 900}}
        },
    )

    assert _stage_map(stages)["graph"] == QUEUED
    assert _stage_map(stages)["enrich"] == RUNNING
    assert all(stage.detail == {} for stage in stages if stage.name == "graph")


def test_a_completed_stage_this_build_does_not_declare_is_refused() -> None:
    """A stage set that changed under a live row is stated, never guessed at."""
    with pytest.raises(UnknownStageError):
        project_stages(
            completed_stage="vectorise", status="running", events_by_stage={}
        )


def _stage_map(stages) -> dict[str, str]:
    """Map stage name to state for a tuple of :class:`StageProgress`."""
    return {stage.name: stage.state for stage in stages}


# ─────────────────────────────────────────────────────────────────────────────
# The projection over a real ingest
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_projection_reports_the_stages_that_really_committed(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Run half the pipeline; the log names exactly the half that ran.

    Driven off the real activity, so what is asserted is the state the substrate left in
    PostgreSQL — the same rows a browser polling the endpoint reads a second later.
    """
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)

    for stage in ("chunk", "enrich", "embed"):
        await _run(stage, document_id=document_id)

    progress = await _progress(document_id)

    assert progress.completed_stage == "embed"
    # ``parse`` is completed here because ``completed_stage`` is a high-water mark over an
    # ordered pipeline: the artifact was seeded and the chunk stage committed on top of it.
    assert _states(progress) == {
        "parse": COMPLETED,
        "chunk": COMPLETED,
        "enrich": COMPLETED,
        "embed": COMPLETED,
        "index": RUNNING,
        "graph": QUEUED,
    }
    ran = {stage.name: stage for stage in progress.stages if stage.detail}
    assert set(ran) == {"chunk", "enrich", "embed"}, (
        "a stage reported detail it never ran, or ran and reported none"
    )
    assert ran["chunk"].detail["chunks"] == progress.chunk_count
    assert ran["enrich"].detail["enriched"] == progress.chunk_count
    assert ran["embed"].detail["embedded"] == progress.chunk_count
    assert all(stage.duration_ms is not None for stage in ran.values())
    assert progress.corpus.chunks == progress.chunk_count
    assert progress.corpus.enriched == progress.chunk_count
    assert progress.corpus.embedded == progress.chunk_count


async def test_a_resumed_document_neither_invents_nor_un_commits_a_stage(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The projection is driven by the row, so a restart cannot make it lie.

    Two halves, and the second is the one that matters:

    1. A document whose ``completed_stage`` sits mid-pipeline — precisely what a
       hard-killed worker leaves — reports those stages done and no others, with **no**
       events consulted at all.
    2. The replacement worker runs :func:`aegis.jobs.stages.remaining_stages` and
       nothing else, so its entries are *appended* to the earlier attempt's rather than
       replacing them — and the stages the first worker committed keep both their
       completed state and the detail it recorded for them.
    """
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)
    for stage in ("chunk", "enrich", "embed"):
        await _run(stage, document_id=document_id)

    # (1) The worker is gone. Nothing is in memory; the row says where it got to.
    await _set(document_id, completed_stage="enrich")
    resumed = await _progress(document_id)

    assert _states(resumed) == {
        "parse": COMPLETED,
        "chunk": COMPLETED,
        "enrich": COMPLETED,
        "embed": RUNNING,
        "index": QUEUED,
        "graph": QUEUED,
    }, "the log claimed a stage the row does not say committed, or re-pended one it does"

    # ``parse`` never ran in this process at all — the artifact was seeded — so it has
    # no event, and it still reads completed. That is the row driving the projection.
    by_name = {stage.name: stage for stage in resumed.stages}
    assert by_name["parse"].detail == {} and by_name["parse"].at is None
    assert by_name["chunk"].detail, "a stage that did run lost the detail it recorded"

    # (2) The replacement worker picks up exactly what the row still owes.
    before = await _events(document_id)
    for spec in remaining_stages("enrich"):
        await _run(spec.name, document_id=document_id)
    after = await _events(document_id)

    assert [(event.event_type, event.seq) for event in after[: len(before)]] == [
        (event.event_type, event.seq) for event in before
    ], "the resume rewrote the earlier attempt's entries instead of appending to them"
    assert [event.payload["stage"] for event in after[len(before) :]] == [
        "embed",
        "index",
        "graph",
    ]
    replayed = await _progress(document_id)
    assert set(_states(replayed).values()) == {COMPLETED}
    assert replayed.completed_stage == "graph"


async def test_parse_confidence_and_its_reasons_reach_the_projection(
    client, db, wired, store, temporal
) -> None:
    """Task 4.6c computed the score and could only WARN; this is where a human sees it.

    The real ``parse`` stage runs, against the real fixture, so the reasons asserted here
    are the reasons the quality gate actually produced — not a hand-written sample.
    """
    _wire(store)
    await _seed_tenants()
    body = await _upload(client, fixture_pdf().read_bytes())
    document_id = body["document_id"]
    await _start(document_id)

    await _run("parse", document_id=document_id)
    progress = await _progress(document_id)

    assert progress.parse.confidence is not None
    assert progress.parse.confidence == progress.parse_confidence
    assert progress.parse.threshold == LOW_CONFIDENCE
    assert progress.parse.low is False, progress.parse.reasons
    assert progress.parse.reasons, (
        "the score reached the screen with no reasons behind it — 0.57 tells a person "
        "nothing they can act on"
    )
    assert any("reading order" in reason for reason in progress.parse.reasons)
    # D3's decision, named on screen rather than left silent.
    assert progress.parse.ocr_enabled is not None
    assert progress.parse.ocr_reason
    # D2's structure check: the histogram is what makes a scrambled multi-column parse
    # obvious instead of merely wrong.
    assert progress.parse.heading_histogram
    assert progress.parse.parser
    assert progress.parse.parse_seconds and progress.parse.parse_seconds > 0
    assert progress.page_count == 15


async def test_the_graph_this_ingest_built_reaches_the_projection(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Task 4.12b: entities and relations as extracted, not just a finished node count."""
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)
    for stage in _STAGES[1:]:
        await _run(stage, document_id=document_id)

    progress = await _progress(document_id)

    assert progress.graph.extractor == "fixed-test-extractor"
    assert progress.graph.entity_total > 0
    assert progress.graph.relation_total > 0
    labels = {entity.label for entity in progress.graph.entities}
    assert {"Transformer", "Attention"} <= labels
    transformer = next(e for e in progress.graph.entities if e.label == "Transformer")
    assert transformer.kind == "product"
    assert transformer.mentions == progress.chunk_count, (
        "the extractor put one Transformer on every chunk; the projection lost some"
    )
    # Both ends resolved to their human labels rather than left as `kind:normalised` ids.
    edge = next(
        relation
        for relation in progress.graph.relations
        if relation.phrase == "is built from"
    )
    assert (edge.source, edge.target) == ("Transformer", "Attention")
    assert edge.mentions > 0
    # The stage's own event carries the totals it counted, which must agree with the rows.
    graph_stage_detail = {
        stage.name: stage.detail for stage in progress.stages
    }["graph"]
    assert graph_stage_detail["entities"] == progress.graph.entity_total
    assert graph_stage_detail["relations"] == progress.graph.relation_total


async def test_the_log_tail_records_every_stage_and_the_close_out(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The chronological half of the log, and the terminal entry that closes it."""
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)
    for stage in _STAGES[1:]:
        await _run(stage, document_id=document_id)
    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT,
            workflow_id=f"ingest:{_TENANT}:{document_id}",
            document_id=document_id,
            status="succeeded",
        )
    )

    progress = await _progress(document_id)

    kinds = [entry.kind for entry in progress.entries]
    assert kinds == [INGEST_STAGE_EVENT] * 5 + [INGEST_FINISHED_EVENT]
    assert [entry.stage for entry in progress.entries[:5]] == list(_STAGES[1:])
    assert "succeeded" in progress.entries[-1].message
    assert progress.status == "succeeded"

    # A replayed close-out must not record a second ending.
    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT,
            workflow_id=f"ingest:{_TENANT}:{document_id}",
            document_id=document_id,
            status="succeeded",
        )
    )
    again = await _progress(document_id)
    assert len(again.entries) == len(progress.entries)


# ─────────────────────────────────────────────────────────────────────────────
# The route
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_endpoint_serves_the_projection_and_is_tenant_scoped(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """`GET /documents/{id}/ingest` answers for its owner and 404s for anyone else.

    "Deleted" and "another tenant's" are one answer on purpose: telling them apart would
    make this endpoint an oracle for other tenants' document ids.
    """
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)
    for stage in _STAGES[1:]:
        await _run(stage, document_id=document_id)

    res = await client.get(f"/documents/{document_id}/ingest", headers=_headers())

    assert res.status_code == 200, res.text
    payload = res.json()
    assert payload["document_id"] == document_id
    assert [stage["name"] for stage in payload["stages"]] == list(_STAGES)
    assert payload["completed_stage"] == "graph"
    assert payload["graph"]["entity_total"] > 0
    assert payload["parse"]["threshold"] == LOW_CONFIDENCE
    assert payload["entries"], "the endpoint served an empty log for a real ingest"

    other = await client.get(
        f"/documents/{document_id}/ingest",
        headers=_headers(
            tenant_id=_OTHER_TENANT, username="b-admin", user_id=_OTHER_USER
        ),
    )

    assert other.status_code == 404, other.text


# ─────────────────────────────────────────────────────────────────────────────
# A failed ingest, read the way a tenant reads it (audit C, C2)
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_failed_ingest_names_the_stage_that_broke_and_why(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The tenant-visible body must blame ``embed``, not ``enrich``, and say why.

    Measured on the cold demo path with ``embed`` failing:

    * the terminal entry read ``"ingest failed at enrich"`` — and enrich **succeeded**;
    * ``embed`` rendered ``queued``, identical to ``index`` and ``graph``, which never ran;
    * the real cause (``litellm.APIError: RBAC: access denied``) appeared nowhere, because
      what reached the row was Temporal's wrapper, ``"Activity task failed"``.

    The same response body already renders per-table failures well
    (``"reason": "the summary call failed: …"``). This asserts the stage failure now reads
    the same way.
    """
    _wire(store)
    await _seed_tenants()
    data, artifact = parsed_artifact
    body = await _upload(client, data)
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT, sha256=body["content_sha256"], payload=artifact
    )
    await _start(document_id)
    for stage in ("chunk", "enrich"):
        await _run(stage, document_id=document_id)

    # The workflow's own close-out, carrying the string its ``except`` now builds.
    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT,
            workflow_id=f"ingest:{_TENANT}:{document_id}",
            document_id=document_id,
            status="failed",
            error="the embed stage failed: litellm.APIError: RBAC: access denied",
        )
    )

    progress = await _progress(document_id)

    # 1. The failing stage is distinguishable from the stages that never ran.
    assert _states(progress) == {
        "parse": COMPLETED,
        "chunk": COMPLETED,
        "enrich": COMPLETED,
        "embed": FAILED,
        "index": QUEUED,
        "graph": QUEUED,
    }

    # 2. The terminal log line names ``embed``, and does not read as "enrich broke".
    terminal = [entry for entry in progress.entries if entry.kind == INGEST_FINISHED_EVENT]
    assert len(terminal) == 1
    message = terminal[0].message
    assert "in the embed stage" in message, message
    assert "failed at enrich" not in message, message

    # 3. The underlying error is there, not Temporal's wrapper.
    assert "RBAC: access denied" in message
    assert "RBAC: access denied" in (progress.error or "")

    # 4. The seq gap is the pipeline's shape, not lost rows. ``seq`` is the stage's
    #    1-based index in INGEST_STAGES and the close-out's is one past the last, so
    #    chunk=2, enrich=3, close-out=7 — and 4/5/6 are embed/index/graph never having
    #    committed one. (parse=1 is absent here only because this test seeds the parse
    #    artifact instead of running the stage.) Contiguity was never the contract;
    #    replay stability is. See the module docstring.
    seqs = [entry.seq for entry in progress.entries]
    assert seqs == [2, 3, 7], (
        "seq is the stage's index in INGEST_STAGES, so the gaps are stages that never "
        "committed — see the module docstring"
    )


def test_the_workflow_reports_the_root_cause_not_temporals_wrapper() -> None:
    """``str(exc)`` on a failed activity is ``"Activity task failed"`` and nothing else.

    That wrapper is what reached ``documents.error``, ``job_runs.error`` and the ingest
    log, so a tenant whose embed stage died of ``litellm.APIError: RBAC: access denied``
    was told only that an activity failed. The cause lives one link down the chain.
    """
    from temporalio.exceptions import ActivityError, ApplicationError

    from app.jobs.flows.ingest import _root_reason

    cause = ApplicationError("litellm.APIError: RBAC: access denied", type="APIError")
    wrapper = ActivityError(
        "Activity task failed",
        scheduled_event_id=1,
        started_event_id=2,
        identity="worker",
        activity_type="aegis_run_stage",
        activity_id="1",
        retry_state=None,
    )
    wrapper.__cause__ = cause

    unwrapped = _root_reason(wrapper)
    assert "RBAC: access denied" in unwrapped
    assert "Activity task failed" not in unwrapped
    # An exception with no chain still yields something readable rather than "".
    assert _root_reason(RuntimeError("plain")) == "plain"
