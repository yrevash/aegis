"""The tenant's documents, read: the corpus listing and one document's live ingest log.

Two endpoints — ``GET /documents`` and ``GET /documents/{id}/ingest`` (tasks 4.12 and
4.12b) — because they are the read side of the same noun and they scope identically.
The listing is here rather than beside ``POST /documents`` for the reason the whole module
exists: :mod:`app.api.routes` is past 3,500 lines, and a reader looking for what a tenant
can *see* about its documents should find both answers in one place.

A **new module rather than a 3,300th line of** :mod:`app.api.routes`, and its own
``APIRouter`` merged into that one at the bottom of it by :func:`mount`, so the served
route table still contains every endpoint while the code for this one lives where a
reader would look for it.

:func:`mount` extends ``target.routes`` rather than calling ``include_router``, and the
difference is not stylistic. FastAPI 0.141 makes ``include_router`` **lazy**: it appends
one ``fastapi.routing._IncludedRouter`` placeholder that resolves its children at request
time. The app serves them either way — but anything that *enumerates* a router's routes
sees a single object with no ``path`` and no ``methods``, so this endpoint would have
been invisible to ``tests/api/test_route_coverage.py``, which reads the served table
straight off ``app.api.routes.router``. A route that escapes the coverage test by
accident is exactly the drift that test exists to catch.

The route is a thin shell on purpose: the projection is
:func:`app.ingestion.progress.ingest_progress`, which reads only rows the ingest already
committed. What is left here is the three things an HTTP boundary owes —

* **scope**, resolved exactly as every other tenant read in this API resolves it: a
  platform admin may read any document, everybody else is pinned to their own tenant and
  the session's ``tenant_isolation`` policy enforces it a second time in the database;
* **a 404 that is not an oracle** — "deleted" and "belongs to someone else" get one
  answer, because answering them differently would let a caller enumerate other tenants'
  document ids;
* **a stable wire shape**, mirrored by ``web/src/lib/api/jobs.ts``.

Polling rather than SSE, and that is a decision rather than a shortcut. The whole record
is durable, so a poll is a *replay* — a browser that reconnects, refreshes, or opens the
document an hour later gets the identical answer, where a stream would have to
reconstruct one anyway. The stream Phase 3 owns is for agent runs, whose events are
per-token; an ingest emits one event per stage, six of them, over minutes.
"""

from __future__ import annotations

from datetime import date

from aegis.retrieval.types import UntenantedPrincipalError, tenant_filter
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.routes import AuthContext, require_auth
from app.data import get_sessionmaker, set_tenant_scope
from app.ingestion.progress import (
    DocumentSummary,
    IngestProgress,
    UnknownDocumentError,
    ingest_progress,
    list_documents,
)

__all__ = [
    "DocumentRow",
    "DocumentsResponse",
    "IngestProgressResponse",
    "ingest_router",
    "mount",
]

#: Upper bound on how many documents one ``GET /documents`` call may return. Clamped at
#: the boundary rather than trusted from the query string: an unbounded ``limit`` on a
#: tenant-scoped scan is a denial-of-service knob handed to whoever holds a token.
_DOCUMENTS_LIMIT_MAX = 200

ingest_router = APIRouter()


class StageProgressModel(BaseModel):
    """One stage of the ingest pipeline and what is known about it."""

    name: str = Field(description="The stage, as `documents.completed_stage` spells it.")
    state: str = Field(description="completed | running | queued.")
    queue: str = Field(description="The task queue whose concurrency policy it obeys.")
    at: str | None = Field(default=None, description="ISO 8601 UTC commit time, or null.")
    duration_ms: int | None = Field(
        default=None, description="Wall clock inside the handler, when recorded."
    )
    detail: dict = Field(
        default_factory=dict,
        description="What the stage found — its own report plus the columns it set.",
    )


class ParseModel(BaseModel):
    """The D-parse quality gate's verdict, which until 4.12 only reached a log file."""

    confidence: float | None = None
    low: bool = False
    threshold: float
    reasons: list[str] = Field(
        default_factory=list,
        description="One line per signal the gate disagreed on, written for a person.",
    )
    heading_histogram: dict[str, int] = Field(default_factory=dict)
    ocr_enabled: bool | None = Field(
        default=None, description="D3's per-document OCR decision."
    )
    ocr_reason: str | None = None
    parser: str | None = None
    parse_seconds: float | None = None


class TableModel(BaseModel):
    """One table the chunk stage lifted out as its own chunk."""

    caption: str | None = None
    rows: int | None = None
    cols: int | None = None
    summarised: bool = False
    reason: str | None = None


class EntityModel(BaseModel):
    """One entity the graph stage extracted, with its mention count."""

    id: str
    label: str
    kind: str
    mentions: int


class RelationModel(BaseModel):
    """One extracted edge, both ends resolved to their human labels."""

    source: str
    phrase: str
    target: str
    mentions: int


class GraphModel(BaseModel):
    """The knowledge graph this ingest built — task 4.12b."""

    extractor: str | None = None
    entity_total: int = 0
    relation_total: int = 0
    entities: list[EntityModel] = Field(default_factory=list)
    relations: list[RelationModel] = Field(default_factory=list)


class CorpusModel(BaseModel):
    """What the document became, counted off `chunks` rather than off the log."""

    chunks: int = 0
    tables: int = 0
    summarised: int = 0
    enriched: int = 0
    embedded: int = 0


class LogEntryModel(BaseModel):
    """One chronological line of the log, every run of the document included."""

    seq: int
    ts: str = Field(description="ISO 8601 UTC.")
    kind: str
    stage: str | None = None
    message: str


class IngestProgressResponse(BaseModel):
    """Body for `GET /documents/{document_id}/ingest`.

    Every field is projected from a committed row — `documents`, `job_runs`, `chunks`
    and `run_events` — so a refresh mid-ingest resumes the view rather than losing it.
    """

    document_id: int
    filename: str
    title: str | None = None
    status: str
    completed_stage: str | None = None
    page_count: int | None = None
    chunk_count: int | None = None
    parse_confidence: float | None = None
    workflow_id: str | None = None
    error: str | None = None
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    stages: list[StageProgressModel]
    parse: ParseModel
    corpus: CorpusModel
    tables: list[TableModel] = Field(default_factory=list)
    graph: GraphModel
    entries: list[LogEntryModel] = Field(default_factory=list)


class DocumentRow(BaseModel):
    """One document in the corpus listing — a row, not a whole ingest log."""

    document_id: int
    filename: str
    title: str | None = None
    status: str = Field(description="pending | running | succeeded | failed | cancelled.")
    completed_stage: str | None = Field(
        default=None, description="The last stage that committed, or null."
    )
    page_count: int | None = None
    chunk_count: int | None = None
    parse_confidence: float | None = Field(
        default=None, description="D-parse's score in [0, 1]; null before the parse runs."
    )
    size_bytes: int
    doc_type: str | None = None
    doc_date: date | None = None
    workflow_id: str | None = None
    error: str | None = Field(
        default=None,
        description="Why it failed, naming the stage that failed and the underlying "
        "cause — not the orchestrator's wrapper.",
    )
    created_at: str | None = Field(default=None, description="ISO 8601 UTC upload time.")


class DocumentsResponse(BaseModel):
    """Body for `GET /documents` — this tenant's corpus, newest first."""

    rows: list[DocumentRow] = Field(default_factory=list)


def _document_row(summary: DocumentSummary) -> DocumentRow:
    """Render one projected summary onto the wire model."""
    return DocumentRow(
        document_id=summary.document_id,
        filename=summary.filename,
        title=summary.title,
        status=summary.status,
        completed_stage=summary.completed_stage,
        page_count=summary.page_count,
        chunk_count=summary.chunk_count,
        parse_confidence=summary.parse_confidence,
        size_bytes=summary.size_bytes,
        doc_type=summary.doc_type,
        doc_date=summary.doc_date,  # type: ignore[arg-type] - a date or None off the row
        workflow_id=summary.workflow_id,
        error=summary.error,
        created_at=_iso(summary.created_at),
    )


@ingest_router.get(
    "/documents",
    response_model=DocumentsResponse,
    tags=["ingestion"],
)
async def list_tenant_documents(
    limit: int = _DOCUMENTS_LIMIT_MAX,
    auth: AuthContext = Depends(require_auth),
) -> DocumentsResponse:
    """List this tenant's documents, newest first — the corpus, as a list.

    **The endpoint that was missing.** The route table had ``POST /documents`` and
    ``GET /documents/{id}/ingest`` and nothing between them, so "show me what you have
    ingested for this tenant" could only be answered by someone who already knew a
    document id. A corpus you cannot enumerate is a corpus you cannot demonstrate.

    **Scoped through the sealed type, not through ``tenant_id or None``.** The authority
    comes from :meth:`~app.api.routes.AuthContext.tenant_scope` and is turned into a
    filter by :func:`aegis.retrieval.types.tenant_filter`, so the platform-wide ``None``
    is reachable *only* from the explicit ``ALL_TENANTS`` authority. The expression this
    replaces — ``None if admin else auth.tenant_id`` — produced that same ``None`` down
    the unprivileged branch for any principal whose ``users.tenant_id`` is NULL, which is
    the conflation behind the five cross-tenant leaks commit ``907b7f2`` closed. A
    principal bound to no tenant gets an **empty list** rather than everyone's corpus.

    The session's ``tenant_isolation`` policy enforces the same scope a second time in the
    database, so a mistake in the predicate above runs into a policy rather than into a
    tenant's documents.

    Args:
        limit: How many rows at most, clamped to ``[1, 200]``.
        auth: The authenticated principal. Platform staff see every tenant's documents;
            everybody else sees their own.

    Returns:
        The rows, newest first. Empty is an honest answer and never an error: a tenant
        that has uploaded nothing has nothing here.
    """
    capped = max(1, min(limit, _DOCUMENTS_LIMIT_MAX))
    try:
        tenant_id = tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError:
        # Not a 403: this is a *listing*, and "you are bound to no tenant, so no corpus is
        # yours" is exactly an empty list. Raising here would make an un-tenanted staff
        # account's console error rather than show it the truth.
        return DocumentsResponse(rows=[])
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        rows = await list_documents(session, tenant_id=tenant_id, limit=capped)
    return DocumentsResponse(rows=[_document_row(row) for row in rows])


def _iso(value: object) -> str | None:
    """Return an ISO 8601 string for a datetime, or ``None``.

    Args:
        value: A ``datetime`` or ``None``.

    Returns:
        The ISO rendering, or ``None`` when there is no instant.
    """
    return None if value is None else value.isoformat()  # type: ignore[attr-defined]


def _response(progress: IngestProgress) -> IngestProgressResponse:
    """Render the projection onto the wire model.

    Args:
        progress: What :func:`app.ingestion.progress.ingest_progress` returned.

    Returns:
        The response body.
    """
    return IngestProgressResponse(
        document_id=progress.document_id,
        filename=progress.filename,
        title=progress.title,
        status=progress.status,
        completed_stage=progress.completed_stage,
        page_count=progress.page_count,
        chunk_count=progress.chunk_count,
        parse_confidence=progress.parse_confidence,
        workflow_id=progress.workflow_id,
        error=progress.error,
        created_at=_iso(progress.created_at),
        started_at=_iso(progress.started_at),
        finished_at=_iso(progress.finished_at),
        stages=[
            StageProgressModel(
                name=stage.name,
                state=stage.state,
                queue=stage.queue,
                at=_iso(stage.at),
                duration_ms=stage.duration_ms,
                detail=dict(stage.detail),
            )
            for stage in progress.stages
        ],
        parse=ParseModel(
            confidence=progress.parse.confidence,
            low=progress.parse.low,
            threshold=progress.parse.threshold,
            reasons=list(progress.parse.reasons),
            heading_histogram=dict(progress.parse.heading_histogram),
            ocr_enabled=progress.parse.ocr_enabled,
            ocr_reason=progress.parse.ocr_reason,
            parser=progress.parse.parser,
            parse_seconds=progress.parse.parse_seconds,
        ),
        corpus=CorpusModel(
            chunks=progress.corpus.chunks,
            tables=progress.corpus.tables,
            summarised=progress.corpus.summarised,
            enriched=progress.corpus.enriched,
            embedded=progress.corpus.embedded,
        ),
        tables=[
            TableModel(
                caption=table.caption,
                rows=table.rows,
                cols=table.cols,
                summarised=table.summarised,
                reason=table.reason,
            )
            for table in progress.tables
        ],
        graph=GraphModel(
            extractor=progress.graph.extractor,
            entity_total=progress.graph.entity_total,
            relation_total=progress.graph.relation_total,
            entities=[
                EntityModel(
                    id=entity.id,
                    label=entity.label,
                    kind=entity.kind,
                    mentions=entity.mentions,
                )
                for entity in progress.graph.entities
            ],
            relations=[
                RelationModel(
                    source=relation.source,
                    phrase=relation.phrase,
                    target=relation.target,
                    mentions=relation.mentions,
                )
                for relation in progress.graph.relations
            ],
        ),
        entries=[
            LogEntryModel(
                seq=entry.seq,
                ts=entry.ts.isoformat(),
                kind=entry.kind,
                stage=entry.stage,
                message=entry.message,
            )
            for entry in progress.entries
        ],
    )


@ingest_router.get(
    "/documents/{document_id}/ingest",
    response_model=IngestProgressResponse,
    tags=["ingestion"],
)
async def get_ingest_progress(
    document_id: int,
    auth: AuthContext = Depends(require_auth),
) -> IngestProgressResponse:
    """Return the live ingest log for one document — stage by stage, as it happened.

    **A projection, not a second log.** Which stages completed comes off
    ``documents.completed_stage``; what each produced comes off the ``run_events`` entry
    the stage wrote in the transaction that bumped it, plus the columns on the row; the
    tables, entities and relations come off ``chunks.meta``. Nothing is held in memory,
    so a worker killed mid-ingest and restarted cannot make this answer disagree with
    what actually committed — the stage it died in is not marked done, and the five
    before it do not go back to pending.

    **The parse's confidence and its reasons are here** because task 4.6c computed them
    and could only write them to a WARNING no tenant can read. A document that parsed at
    0.57 is indexed and searchable and *flagged*, and this is where a human finds out.

    **The graph is shown as it is built** (4.12b): the entities and relations the
    ``graph`` stage wrote onto the chunks, with mention counts, rather than only a final
    node total.

    Args:
        document_id: The document to report on.
        auth: The authenticated principal. A platform admin may read any document;
            everyone else is pinned to their own tenant, and a principal pinned to no
            tenant is refused rather than unpinned.

    Returns:
        The whole log in one body — safe to poll.

    Raises:
        HTTPException: 404 when the document is not visible under the caller's scope.
            "Deleted", "another tenant's" and "your account is bound to no tenant, so
            nothing here is yours" are deliberately one answer — a principal with no
            tenant authority can see no document, and saying which of the three it was
            would tell an unauthorised caller that document ``document_id`` exists.

            The scope itself used to be
            ``None if auth.fine_role == PLATFORM_ADMIN else auth.tenant_id``, which
            reaches the platform admin's unrestricted ``None`` down the *other* branch
            for any non-admin whose ``users.tenant_id`` is NULL — the shape ``app.seed``
            mints for the "client" platform principal. ``_load_document`` then added no
            predicate and ``set_tenant_scope(None)`` bound the empty RLS scope, which
            ``tenant_isolation`` deliberately does not restrict, so both layers were
            open at once. :meth:`~app.api.routes.AuthContext.tenant_scope` now separates
            the two states in the type, and the tenant-less one lands here.
    """
    try:
        tenant_id = tenant_filter(auth.tenant_scope())
    except UntenantedPrincipalError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No document {document_id} is visible to this caller.",
        ) from exc
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        try:
            progress = await ingest_progress(
                session, document_id=document_id, tenant_id=tenant_id
            )
        except UnknownDocumentError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No document {document_id} is visible to this caller.",
            ) from exc
    return _response(progress)


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Args:
        target: The application's main router. Its ``routes`` list is extended in place;
            see the module docstring for why this is not ``include_router``.
    """
    target.routes.extend(ingest_router.routes)
