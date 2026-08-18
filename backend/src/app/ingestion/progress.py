"""The live ingest log, as a projection — tasks 4.12 and 4.12b.

**Nothing here is a source of truth, and that is the design.** Every field below is read
back out of a row somebody else already committed:

=========================  ===================================================
What the screen shows      Where it is actually stored
=========================  ===================================================
Which stages completed     ``documents.completed_stage`` (the resume arithmetic
                           in :func:`aegis.jobs.stages.remaining_stages` turns
                           that one value into the whole pipeline's state)
What each stage produced   ``documents.page_count`` / ``chunk_count`` /
                           ``parse_confidence``, plus the per-stage
                           ``run_events`` entry :mod:`app.jobs.ingest_log`
                           wrote in the stage's own transaction
Why the parse scored that  the ``parse`` event's ``facts.quality.reasons`` —
                           task 4.6c computed them and could only log them
Tables, entities,          ``chunks.meta``, aggregated in SQL
relations
When it started / ended    ``job_runs`` and ``documents``
=========================  ===================================================

**Why the completed set is read off the row and never off the events.** They agree —
:mod:`app.jobs.ingest_log` writes each entry in the transaction that bumps
``completed_stage``, so neither can exist without the other — but they agree only because
one of them is derived. Deriving progress from the *log* would mean a log line that
failed to write (a missing monthly partition, say) would make a committed stage vanish
from the screen; deriving it from the row means the worst a lost entry can do is remove a
duration and a detail panel from a stage that still, correctly, reads as done. The
asymmetry is deliberate and it is what makes this survive a resume: a document whose
worker was killed after ``embed`` shows five green stages and a running sixth, because
``completed_stage`` says ``embed`` and nothing in memory is consulted at all.

**What "running" means here.** Exactly one stage can be running — the first one the
resume arithmetic still owes — and only when the row says the job is ``RUNNING``. A
``PENDING`` document has no running stage (it is queued behind the CPU slot), and a
``FAILED`` one has none either: the stage it died in is *not* marked completed, because it
did not commit, and calling it "running" would be the log's one chance to lie.

The graph half (4.12b) reads ``chunks.meta``, which is where ``graph_stage`` writes its
extractions, and aggregates it in PostgreSQL rather than in Python: a 200-page document is
hundreds of chunks each carrying dozens of entities, and pulling that into the API process
to count it would make the ingest log the most expensive read in the platform.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from aegis.ingestion.quality import LOW_CONFIDENCE
from aegis.jobs.models import Document, JobRun, JobStatus
from aegis.jobs.stages import INGEST_STAGES, remaining_stages
from aegis.runs.models import RunEvent
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.jobs.ingest_log import INGEST_FINISHED_EVENT, INGEST_STAGE_EVENT

__all__ = [
    "COMPLETED",
    "QUEUED",
    "RUNNING",
    "CorpusView",
    "EntityView",
    "GraphView",
    "IngestProgress",
    "LogEntry",
    "ParseView",
    "RelationView",
    "StageProgress",
    "TableView",
    "UnknownDocumentError",
    "ingest_progress",
    "project_stages",
]

#: A stage that committed. The only state read from ``documents.completed_stage``.
COMPLETED = "completed"

#: The one stage a worker is inside right now, if the job row says it is running.
RUNNING = "running"

#: A stage that has not run — whether it is waiting on a queue slot or on a failure
#: being re-queued. Deliberately one word rather than two states: the difference is the
#: *job's* status, which the projection reports separately, and splitting it here would
#: put the same fact in two places that could then disagree.
QUEUED = "queued"

#: How many distinct entities / relations / tables the projection carries. The log is a
#: screen, not an export: a document with nine thousand entities must not turn one poll
#: into a nine-thousand-row response, and the totals beside these lists are exact
#: regardless of where the list is cut.
_DETAIL_LIMIT = 40


class UnknownDocumentError(LookupError):
    """No document with that id is visible under the caller's tenant scope.

    "Deleted" and "belongs to another tenant" are one answer on purpose: distinguishing
    them would make this endpoint an oracle for the existence of other tenants' documents.
    """


# ─────────────────────────────────────────────────────────────────────────────
# The projected shapes
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class StageProgress:
    """One stage of the pipeline, and what is known about it.

    Attributes:
        name: The stage name, as ``completed_stage`` spells it.
        state: :data:`COMPLETED`, :data:`RUNNING` or :data:`QUEUED`.
        queue: The task queue it runs on — where the concurrency limit that made it wait
            actually lives.
        at: When it committed, from its ``run_events`` row. ``None`` for a stage that has
            not run, and for one committed before this log existed.
        duration_ms: Wall clock inside the handler, likewise.
        detail: What the stage found — the handler's own report, merged with the
            ``documents`` columns it set. Empty for a stage with no event.
    """

    name: str
    state: str
    queue: str
    at: datetime | None = None
    duration_ms: int | None = None
    detail: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ParseView:
    """The parse's verdict on itself — D-parse (4.6c) made visible.

    ``confidence`` is on the ``documents`` row; everything else is on the parse stage's
    event, because a heading histogram and five sentences of reasoning have nowhere on a
    row to live. Until this projection existed the reasons went to a WARNING in a log
    file, which is to say: to nobody.

    Attributes:
        confidence: The gate's score in [0, 1], or ``None`` before the parse runs.
        low: Whether it is below :data:`aegis.ingestion.quality.LOW_CONFIDENCE`.
        threshold: That constant, carried so the screen does not hard-code it.
        reasons: One line per signal, written for a person.
        heading_histogram: Heading count per level — the panel that makes a multi-column
            PDF's scrambled parse obvious instead of merely wrong.
        ocr_enabled: D3's per-document decision.
        ocr_reason: The evidence behind it, in one line.
        parser: Name and version, because chunks from two parsers are not comparable.
        parse_seconds: Wall clock inside the parser.
    """

    confidence: float | None = None
    low: bool = False
    threshold: float = LOW_CONFIDENCE
    reasons: tuple[str, ...] = ()
    heading_histogram: Mapping[str, int] = field(default_factory=dict)
    ocr_enabled: bool | None = None
    ocr_reason: str | None = None
    parser: str | None = None
    parse_seconds: float | None = None


@dataclass(frozen=True, slots=True)
class TableView:
    """One table the chunk stage lifted out as its own chunk (D8 / 4.10)."""

    caption: str | None
    rows: int | None
    cols: int | None
    summarised: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class EntityView:
    """One entity the graph stage extracted, and how often it was mentioned."""

    id: str
    label: str
    kind: str
    mentions: int


@dataclass(frozen=True, slots=True)
class RelationView:
    """One extracted edge, with both ends resolved to their human labels."""

    source: str
    phrase: str
    target: str
    mentions: int


@dataclass(frozen=True, slots=True)
class GraphView:
    """The knowledge graph this ingest built — task 4.12b.

    Read from ``chunks.meta``, the tenant-scoped rows the ``graph`` stage writes, rather
    than from the graph store: that is what makes "what did this ingest extract"
    answerable with the store unreachable, and it is the only version of the answer that
    is scoped by Row-Level Security.

    Attributes:
        extractor: Which extractor ran. A corpus extracted deterministically must never
            be mistaken for one an LLM extracted.
        entity_total: Every entity mention on every chunk, counted in SQL.
        relation_total: Likewise for edges.
        entities: The most-mentioned distinct entities, capped at :data:`_DETAIL_LIMIT`.
        relations: The most-repeated distinct edges, likewise.
    """

    extractor: str | None = None
    entity_total: int = 0
    relation_total: int = 0
    entities: tuple[EntityView, ...] = ()
    relations: tuple[RelationView, ...] = ()


@dataclass(frozen=True, slots=True)
class CorpusView:
    """What the document became, counted off ``chunks`` rather than off the log."""

    chunks: int = 0
    tables: int = 0
    summarised: int = 0
    enriched: int = 0
    embedded: int = 0


@dataclass(frozen=True, slots=True)
class LogEntry:
    """One line of the log, in the order it was committed.

    The chronological tail, as opposed to the per-stage panels above, which are the same
    events indexed by stage. Both come from ``run_events``; the tail is what makes a
    re-queue legible — the second attempt's lines follow the first's rather than
    replacing them.

    Attributes:
        seq: The event's per-run sequence number.
        ts: When it was recorded, by the database's clock.
        kind: The event type, as stored.
        stage: The stage it is about, for a stage entry.
        message: One line a person can read.
    """

    seq: int
    ts: datetime
    kind: str
    stage: str | None
    message: str


@dataclass(frozen=True, slots=True)
class IngestProgress:
    """One document's ingest, as a tenant watches it.

    Attributes:
        document_id: The row.
        filename: What the tenant uploaded it as.
        title: Derived by the parse from the first heading; ``None`` before it runs.
        status: The document's :class:`aegis.jobs.JobStatus`, as its string.
        completed_stage: The last stage that committed — the single durable fact the
            whole stage list is derived from.
        page_count: From the parse.
        chunk_count: From the chunk stage.
        parse_confidence: From the parse; also carried, with its reasons, on ``parse``.
        workflow_id: The execution behind it.
        error: Why it failed, when it did.
        created_at / started_at / finished_at: From ``documents`` and ``job_runs``.
        stages: Every stage of the pipeline, in order, each with its state.
        parse: The quality gate's verdict.
        corpus: What the document became.
        tables: The tables lifted out, capped.
        graph: The entities and relations extracted.
        entries: The chronological log tail, every run of the document included.
    """

    document_id: int
    filename: str
    title: str | None
    status: str
    completed_stage: str | None
    page_count: int | None
    chunk_count: int | None
    parse_confidence: float | None
    workflow_id: str | None
    error: str | None
    created_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    stages: tuple[StageProgress, ...]
    parse: ParseView
    corpus: CorpusView
    tables: tuple[TableView, ...]
    graph: GraphView
    entries: tuple[LogEntry, ...]


# ─────────────────────────────────────────────────────────────────────────────
# The stage projection — the part that must not lie across a resume
# ─────────────────────────────────────────────────────────────────────────────


def project_stages(
    *,
    completed_stage: str | None,
    status: JobStatus | str,
    events_by_stage: Mapping[str, Mapping[str, Any]],
) -> tuple[StageProgress, ...]:
    """Turn one durable ``completed_stage`` value into the whole pipeline's state.

    A pure function over three durable inputs, so the property that matters — that a
    resumed document neither claims un-run stages nor re-reports committed ones as
    pending — is provable without a database, a worker or an orchestrator.

    Args:
        completed_stage: ``documents.completed_stage``. ``None`` means nothing has
            committed yet.
        status: The document's status, which decides whether the next owed stage is being
            worked on or is merely owed.
        events_by_stage: The per-stage ``run_events`` entries, keyed by stage name. Used
            **only** to decorate stages the row already says are complete; an event can
            never promote a stage.

    Returns:
        Every stage of :data:`aegis.jobs.INGEST_STAGES`, in order.

    Raises:
        UnknownStageError: If ``completed_stage`` names no stage this pipeline declares.
            Guessing would either hide committed work or invent it; see that exception.
    """
    owed = {spec.name for spec in remaining_stages(completed_stage)}
    value = status.value if isinstance(status, JobStatus) else str(status)
    running_now = value == JobStatus.RUNNING.value
    first_owed = next((spec.name for spec in INGEST_STAGES if spec.name in owed), None)
    out: list[StageProgress] = []
    for spec in INGEST_STAGES:
        if spec.name not in owed:
            event = events_by_stage.get(spec.name, {})
            timestamp = event.get("ts")
            out.append(
                StageProgress(
                    name=spec.name,
                    state=COMPLETED,
                    queue=spec.task_queue,
                    at=timestamp if isinstance(timestamp, datetime) else None,
                    duration_ms=event.get("duration_ms"),
                    detail=event.get("detail") or {},
                )
            )
            continue
        state = RUNNING if running_now and spec.name == first_owed else QUEUED
        out.append(StageProgress(name=spec.name, state=state, queue=spec.task_queue))
    return tuple(out)


# ─────────────────────────────────────────────────────────────────────────────
# The reads
# ─────────────────────────────────────────────────────────────────────────────

#: ``chunks.meta`` holds a JSON array under these keys once the ``graph`` stage has run,
#: and holds nothing at all before it. ``jsonb_typeof`` rather than ``coalesce`` because
#: an explicit JSON ``null`` would satisfy the latter and then blow up inside
#: ``jsonb_array_elements``.
def _array(column: str) -> str:
    """Return SQL for ``chunks.meta -> column`` as an array, empty when it is not one."""
    return (
        f"CASE WHEN jsonb_typeof(c.meta->'{column}') = 'array' "
        f"THEN c.meta->'{column}' ELSE '[]'::jsonb END"
    )


_CORPUS_SQL = f"""
SELECT count(*) AS chunks,
       count(*) FILTER (WHERE jsonb_typeof(c.meta->'table') = 'object') AS tables,
       count(*) FILTER (
           WHERE coalesce((c.meta->'table'->>'summarised')::boolean, false)
       ) AS summarised,
       count(*) FILTER (
           WHERE coalesce((c.meta->>'enriched')::boolean, false)
       ) AS enriched,
       count(*) FILTER (WHERE jsonb_array_length(c.embedding) > 0) AS embedded,
       coalesce(sum(jsonb_array_length({_array('entities')})), 0) AS entity_total,
       coalesce(sum(jsonb_array_length({_array('relations')})), 0) AS relation_total,
       max(c.meta->>'extractor') AS extractor
  FROM chunks c
 WHERE c.document_id = :document_id
   AND c.tenant_id = :tenant_id
"""

_TABLES_SQL = """
SELECT c.meta->'table'->>'caption'                          AS caption,
       (c.meta->'table'->>'rows')::int                      AS rows,
       (c.meta->'table'->>'cols')::int                      AS cols,
       coalesce((c.meta->'table'->>'summarised')::boolean, false) AS summarised,
       c.meta->'table'->>'reason'                           AS reason
  FROM chunks c
 WHERE c.document_id = :document_id
   AND c.tenant_id = :tenant_id
   AND jsonb_typeof(c.meta->'table') = 'object'
 ORDER BY c.id
 LIMIT :limit
"""

_ENTITIES_CTE = f"""
WITH ent AS (
    SELECT e->>'id'        AS id,
           min(e->>'label') AS label,
           min(e->>'kind')  AS kind,
           count(*)         AS mentions
      FROM chunks c,
           LATERAL jsonb_array_elements({_array('entities')}) AS e
     WHERE c.document_id = :document_id
       AND c.tenant_id = :tenant_id
     GROUP BY 1
)
"""

_ENTITIES_SQL = (
    _ENTITIES_CTE
    + """
SELECT id, label, kind, mentions
  FROM ent
 ORDER BY mentions DESC, label
 LIMIT :limit
"""
)

_RELATIONS_SQL = (
    _ENTITIES_CTE
    + f"""
, rel AS (
    SELECT r->>'src_id' AS src,
           r->>'phrase' AS phrase,
           r->>'tgt_id' AS tgt,
           count(*)     AS mentions
      FROM chunks c,
           LATERAL jsonb_array_elements({_array('relations')}) AS r
     WHERE c.document_id = :document_id
       AND c.tenant_id = :tenant_id
     GROUP BY 1, 2, 3
)
SELECT coalesce(s.label, rel.src) AS source,
       rel.phrase                 AS phrase,
       coalesce(t.label, rel.tgt) AS target,
       rel.mentions               AS mentions
  FROM rel
  LEFT JOIN ent s ON s.id = rel.src
  LEFT JOIN ent t ON t.id = rel.tgt
 ORDER BY rel.mentions DESC, source, phrase
 LIMIT :limit
"""
)


async def _load_document(
    session: AsyncSession, *, document_id: int, tenant_id: int | None
) -> Document:
    """Read the document through the caller's scope, or refuse to say why not.

    Args:
        session: The scoped session.
        document_id: The row to read.
        tenant_id: The app-level filter over the database's RLS policy — the same
            belt-and-suspenders every governed read in this codebase carries.

    Returns:
        The row.

    Raises:
        UnknownDocumentError: When nothing is visible under this scope.
    """
    statement = select(Document).where(Document.id == document_id)
    if tenant_id is not None:
        statement = statement.where(Document.tenant_id == tenant_id)
    document = (await session.execute(statement)).scalar_one_or_none()
    if document is None:
        raise UnknownDocumentError(
            f"document {document_id} is not visible under this tenant scope"
        )
    return document


async def _load_events(
    session: AsyncSession, *, document: Document, tenant_id: int | None
) -> tuple[RunEvent, ...]:
    """Return every ingest event for this document, across every run of it.

    A re-queue starts a **new** execution with a new ``workflow_id`` (it must: the id is
    ``job_runs``' unique idempotency key), so reading only ``documents.workflow_id`` would
    show the stages the latest attempt happened to re-run and drop the ones an earlier
    attempt committed. Collecting the ids off ``job_runs`` instead makes the log the
    document's, not the attempt's.

    Args:
        session: The scoped session.
        document: The row, whose own ``workflow_id`` is included in case its job row is
            gone.
        tenant_id: The app-level tenant filter.

    Returns:
        The events in commit order.
    """
    runs = select(JobRun.workflow_id).where(
        JobRun.payload["document_id"].as_string() == str(document.id)
    )
    if tenant_id is not None:
        runs = runs.where(JobRun.tenant_id == tenant_id)
    workflow_ids = {row for row in (await session.execute(runs)).scalars().all() if row}
    if document.workflow_id:
        workflow_ids.add(document.workflow_id)
    if not workflow_ids:
        return ()
    statement = select(RunEvent).where(RunEvent.run_id.in_(sorted(workflow_ids)))
    if tenant_id is not None:
        statement = statement.where(RunEvent.tenant_id == tenant_id)
    result = await session.execute(statement.order_by(RunEvent.ts, RunEvent.seq))
    return tuple(result.scalars().all())


def _stage_events(events: Sequence[RunEvent]) -> dict[str, dict[str, Any]]:
    """Index the per-stage entries by stage name, latest attempt winning.

    Args:
        events: Every event of every run of the document, in commit order.

    Returns:
        ``{stage: {"ts", "duration_ms", "detail"}}``. A stage re-run by a later attempt
        overwrites the earlier entry, so the panel describes the run that produced the
        rows currently in the database.
    """
    out: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.event_type != INGEST_STAGE_EVENT:
            continue
        payload = event.payload or {}
        stage = payload.get("stage")
        if not isinstance(stage, str):
            continue
        detail = dict(payload.get("facts") or {})
        detail.update(
            {
                key: value
                for key, value in (payload.get("columns") or {}).items()
                if value is not None
            }
        )
        out[stage] = {
            "ts": event.ts,
            "duration_ms": payload.get("duration_ms"),
            "detail": detail,
        }
    return out


def _log_entries(events: Sequence[RunEvent]) -> tuple[LogEntry, ...]:
    """Render every recorded event as one readable line, oldest first.

    Args:
        events: The document's events across every run, in commit order.

    Returns:
        The tail. An event type this build does not recognise is still rendered — the
        record layer stores unknown types rather than rejecting them, and an entry
        nobody can name is exactly the one an operator needs to see.
    """
    out: list[LogEntry] = []
    for event in events:
        payload = event.payload or {}
        stage = payload.get("stage") if isinstance(payload.get("stage"), str) else None
        if event.event_type == INGEST_STAGE_EVENT:
            message = f"{stage} committed{_took(payload.get('duration_ms'))}"
            summary = _summarise(dict(payload.get("facts") or {}))
            if summary:
                message = f"{message} — {summary}"
        elif event.event_type == INGEST_FINISHED_EVENT:
            reached = payload.get("completed_stage") or "no stage"
            message = f"ingest {payload.get('status')} at {reached}"
            if payload.get("error"):
                message = f"{message}: {payload['error']}"
        else:
            message = event.event_type
        out.append(
            LogEntry(
                seq=event.seq,
                ts=event.ts,
                kind=event.event_type,
                stage=stage,
                message=message,
            )
        )
    return tuple(out)


def _took(duration_ms: Any) -> str:  # noqa: ANN401 - a wire value of unknown shape
    """Return ``" in 1.4s"`` for a duration, or ``""`` when there is none."""
    if not isinstance(duration_ms, int | float):
        return ""
    return f" in {duration_ms / 1000:.2f}s"


def _summarise(facts: Mapping[str, Any]) -> str:
    """Return a one-line summary of a stage's reported facts.

    Args:
        facts: What the handler reported. Scalars are rendered ``key=value``; nested
            structures are left to the per-stage detail panel, which has room for them.

    Returns:
        The summary, possibly empty.
    """
    parts = [
        f"{key}={value}"
        for key, value in facts.items()
        if isinstance(value, bool | int | float | str) and value != ""
    ]
    return ", ".join(parts[:6])


def _parse_view(document: Document, detail: Mapping[str, Any]) -> ParseView:
    """Build the parse panel from the row's score and the parse event's evidence.

    Args:
        document: The row, which carries ``parse_confidence``.
        detail: The ``parse`` stage's recorded facts, or an empty mapping.

    Returns:
        The panel. Absent evidence renders as absent, never as a confident default: a
        document parsed before this log existed shows its score with no reasons rather
        than a fabricated "no problems found".
    """
    quality = detail.get("quality") or {}
    ocr = detail.get("ocr") or {}
    reasons = quality.get("reasons") or ()
    histogram = detail.get("heading_histogram") or {}
    return ParseView(
        confidence=document.parse_confidence,
        low=bool(quality.get("low"))
        or (
            document.parse_confidence is not None
            and document.parse_confidence < LOW_CONFIDENCE
        ),
        threshold=float(quality.get("threshold") or LOW_CONFIDENCE),
        reasons=tuple(str(reason) for reason in reasons),
        heading_histogram={str(key): int(value) for key, value in histogram.items()},
        ocr_enabled=ocr.get("enabled"),
        ocr_reason=ocr.get("reason"),
        parser=detail.get("parser"),
        parse_seconds=detail.get("parse_seconds"),
    )


async def ingest_progress(
    session: AsyncSession, *, document_id: int, tenant_id: int | None
) -> IngestProgress:
    """Project one document's ingest out of the rows that already record it.

    Args:
        session: A session with the caller's tenant scope already bound.
        document_id: The document to report on.
        tenant_id: The caller's tenant, applied as the app-level filter over RLS.

    Returns:
        The whole log, in one round of reads.

    Raises:
        UnknownDocumentError: When the document is not visible under this scope.
        UnknownStageError: When the row's ``completed_stage`` names a stage this build
            does not declare — the stage set changed under a live row, and neither
            reading of it is safe to guess at.
    """
    document = await _load_document(
        session, document_id=document_id, tenant_id=tenant_id
    )
    owner = document.tenant_id
    events = await _load_events(session, document=document, tenant_id=tenant_id)
    by_stage = _stage_events(events)

    binds = {"document_id": document_id, "tenant_id": owner}
    totals = (await session.execute(text(_CORPUS_SQL), binds)).mappings().one()
    tables = (
        (await session.execute(text(_TABLES_SQL), {**binds, "limit": _DETAIL_LIMIT}))
        .mappings()
        .all()
    )
    entities = (
        (await session.execute(text(_ENTITIES_SQL), {**binds, "limit": _DETAIL_LIMIT}))
        .mappings()
        .all()
    )
    relations = (
        (await session.execute(text(_RELATIONS_SQL), {**binds, "limit": _DETAIL_LIMIT}))
        .mappings()
        .all()
    )

    job = None
    if document.workflow_id:
        job = (
            await session.execute(
                select(JobRun).where(JobRun.workflow_id == document.workflow_id).limit(1)
            )
        ).scalar_one_or_none()

    return IngestProgress(
        document_id=document.id,
        filename=document.filename,
        title=document.title,
        status=document.status.value,
        completed_stage=document.completed_stage,
        page_count=document.page_count,
        chunk_count=document.chunk_count,
        parse_confidence=document.parse_confidence,
        workflow_id=document.workflow_id,
        error=document.error,
        created_at=document.created_at,
        started_at=job.started_at if job is not None else None,
        finished_at=job.finished_at if job is not None else None,
        stages=project_stages(
            completed_stage=document.completed_stage,
            status=document.status,
            events_by_stage=by_stage,
        ),
        parse=_parse_view(document, by_stage.get("parse", {}).get("detail", {})),
        corpus=CorpusView(
            chunks=int(totals["chunks"]),
            tables=int(totals["tables"]),
            summarised=int(totals["summarised"]),
            enriched=int(totals["enriched"]),
            embedded=int(totals["embedded"]),
        ),
        tables=tuple(
            TableView(
                caption=row["caption"],
                rows=row["rows"],
                cols=row["cols"],
                summarised=bool(row["summarised"]),
                reason=row["reason"],
            )
            for row in tables
        ),
        graph=GraphView(
            extractor=totals["extractor"],
            entity_total=int(totals["entity_total"]),
            relation_total=int(totals["relation_total"]),
            entities=tuple(
                EntityView(
                    id=row["id"],
                    label=row["label"] or row["id"],
                    kind=row["kind"] or "",
                    mentions=int(row["mentions"]),
                )
                for row in entities
            ),
            relations=tuple(
                RelationView(
                    source=row["source"],
                    phrase=row["phrase"] or "",
                    target=row["target"],
                    mentions=int(row["mentions"]),
                )
                for row in relations
            ),
        ),
        entries=_log_entries(events),
    )


