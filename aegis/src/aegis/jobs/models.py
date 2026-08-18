"""SQLAlchemy ORM for the durable job substrate — ``job_runs``, ``documents``, ``chunks``.

**These tables are the system of record.** The orchestrator that actually executes the
work (Temporal, in this platform's host) owns *execution* state — retries, timers,
cancellation, replay. It is not a database and its own documentation says not to use it
as one. So the split is deliberate and one-directional:

    documents / job_runs   (tenant_id, status, completed_stage, workflow_id)
          │                 ← ours · RLS-governed · joinable to budgets and users
          └── workflow_id ─▶ the orchestrator: retries · timers · resumability

Everything a tenant is ever shown about its background work — the queue position, the
stage the ingest reached, what it cost, why it failed — is read off these rows, and can
be read with the orchestrator unreachable, uninstalled, or replaced.

Like every other module's models these register on the shared
:class:`aegis.data.AegisBase` metadata (not a second metadata), so a host's
``AegisBase.metadata.create_all`` materialises them alongside the governance, memory and
ops tables — on PostgreSQL with native ``jsonb`` and ``timestamptz`` columns via
:data:`aegis.data.JsonB` and the base's ``UtcDateTime`` annotation map.

All three tables carry ``tenant_id`` and are therefore registered in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`, which is what earns them a
``tenant_isolation`` Row-Level Security policy at boot. Registration is not optional
bookkeeping: an unregistered table with a ``tenant_id`` column looks governed from the
outside and is not, and the boot-time catalog read-back exists precisely to report that.

``chunks`` lives here — rather than beside the host's own tables, where it used to — for
one structural reason: it needs a real ``ForeignKey`` to ``documents.id``, and
SQLAlchemy resolves a foreign key by name **within one MetaData**. The host keeps a
second declarative base for its platform-owned tables, so a ``chunks`` declared there
could not reference a ``documents`` declared here at all; the two would be joined by
convention and by nothing the database checks. The retrieval corpus and the document it
was parsed out of are one lifecycle — the chunk exists because the document was ingested
and must stop existing when it is deleted — so they belong on the same metadata.

**This module imports :mod:`aegis.governance.models`, and that import is load-bearing**
rather than incidental: ``job_runs`` declares real ``ForeignKey`` references to
``tenants.id`` and ``users.id``, which SQLAlchemy can only resolve if those tables are
present on the shared metadata. Importing the jobs models must therefore be sufficient to
make ``create_all`` work, instead of leaving a host to discover the ordering by hitting a
``NoReferencedTableError``. (:mod:`aegis.ops.models` made the opposite choice — a plain
indexed ``tenant_id`` with no FK — because its rows are written by an eval loop that may
run against a database with no governance tables at all. A job row is different: it is
always a *tenant's* job, charged to that tenant's budget, so the referential integrity is
worth the coupling.)

**This module must not import the orchestrator SDK**, directly or transitively. The core
declares the record; the host runs the work. That keeps ``aegis.jobs`` importable by a
consumer who orchestrates differently, and keeps the fallback substrate a drop-in.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Computed,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

# Registration side-effect, and deliberately not a lazy import: the foreign keys below
# reference ``tenants.id`` / ``users.id``, which SQLAlchemy resolves by name against the
# shared metadata at mapper-configuration time. See the module docstring.
import aegis.governance.models  # noqa: F401
from aegis.data import EMBED_DIM, AegisBase, JsonB, VectorColumn

__all__ = [
    "Chunk",
    "Document",
    "JobRun",
    "JobStatus",
]


class JobStatus(StrEnum):
    """Lifecycle of a durable job, from the record layer's point of view.

    Deliberately *not* a mirror of the orchestrator's workflow status: this is what a
    tenant sees and what the console renders, and it must stay meaningful when the
    orchestrator is unreachable or is swapped for another one.

    :data:`RECONCILING` is the state a row enters when the reconciler finds a workflow it
    cannot account for — the row says ``RUNNING`` but the orchestrator has no live
    execution behind it. Making that a visible, sweepable state is the whole point: the
    failure this substrate exists to end is a row silently stuck in ``RUNNING`` forever,
    matched by no sweeper and retried by nothing, which is exactly what the pre-existing
    consolidation job does today. ``RECONCILING`` is a transient state — the reconciler
    resumes the row or fails it with a reason — never a resting place.
    """

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    RECONCILING = "reconciling"


#: The one Postgres ``job_status`` enum type, shared by both tables below.
#:
#: Declared once rather than constructed per column: two ``SAEnum(JobStatus,
#: name="job_status")`` instances are two type objects carrying one type name, and whether
#: ``create_all`` then emits ``CREATE TYPE`` once or twice depends on its memo-based
#: de-duplication rather than on anything this module says. One instance says it. The type
#: is named explicitly for the same reason every enum in :mod:`aegis.governance.models`
#: is — ``jobstatus``, SQLAlchemy's derived default, is not a name a migration can be
#: written against with any confidence.
_JOB_STATUS = SAEnum(JobStatus, name="job_status")


class JobRun(AegisBase):
    """One durable unit of background work, owned by a tenant.

    The ``workflow_id`` is a plain string, not a foreign key: the orchestrator is a system
    we do not own and must not constrain our schema. Nothing in our database may depend on
    a row existing in someone else's, and no DDL of ours may fail because an external
    system pruned its own history. It is the only link between the two, and it is
    deliberately one-way — this row stays readable, joinable and auditable with the
    orchestrator switched off entirely. It is ``unique`` because it is the idempotency key
    the reconciler and every activity look a job up by: two rows claiming the same
    execution is the dual-write skew this substrate is built to make impossible.

    ``run_id`` is the orchestrator's *attempt* identifier under that workflow and is
    nullable because it is only known once the execution has actually started; the pair
    ``(workflow_id, run_id)`` is what a support engineer takes to the orchestrator's UI.

    Where the specification was silent, the choices made here and why:

    * ``status`` carries **no default**, so every writer names the state it is creating a
      row in. A job row that defaults itself into existence is how "pending" and "nobody
      set this" become indistinguishable.
    * The status column reuses the one named :data:`_JOB_STATUS` type rather than
      declaring its own; see that attribute for why the name is not left to SQLAlchemy.
    * ``cost_usd`` is a plain float rather than a numeric: it is an *attribution* figure
      reconciled against ``usage_ledger``, which is float-valued for the same reason, and
      not a billed amount of money.
    """

    __tablename__ = "job_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable, matching every other governed record: a platform-level job (a re-index of
    # a shared corpus, an operator-triggered sweep) has no owning tenant. Note that under
    # the tenant_isolation policy ``NULL = <scope>`` is NULL rather than true, so such a
    # row is invisible to every tenant — which is the intended reading.
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    job_type: Mapped[str] = mapped_column(String(64), index=True)
    workflow_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    run_id: Mapped[str | None] = mapped_column(String(255), default=None)
    status: Mapped[JobStatus] = mapped_column(_JOB_STATUS, index=True)
    # The last stage that finished and committed. A resumed job restarts *after* this,
    # which is what stops a failure in the graph stage re-parsing two hundred pages.
    completed_stage: Mapped[str | None] = mapped_column(String(64), default=None)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    result: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    # Who cancelled it, not merely that it was cancelled: a cancelled tenant job is an
    # audit question ("who stopped the ingest?") before it is an operational one.
    cancelled_by: Mapped[str | None] = mapped_column(String(255), default=None)


class Document(AegisBase):
    """A tenant's source document and where its ingestion got to.

    ``content_sha256`` is the idempotency anchor for the whole ingestion pipeline:
    re-uploading identical bytes must not re-parse them, and the
    ``uq_documents_tenant_sha`` constraint is what makes that **structural rather than a
    check somebody remembers to write**. Parsing is the expensive stage (CPU-bound, roughly a
    second a page) and embedding the billed one, so a duplicate upload that slips past an
    application-level guard costs real money and real minutes; the database refusing the
    second row is the only version of this guarantee that cannot be forgotten.

    The constraint is per **tenant**, not global. Two tenants uploading the same public
    filing are two independent documents with independent lifecycles, ACLs and deletions —
    deduplicating across the tenant boundary would leak the existence of one tenant's data
    into another's, which is the failure the whole isolation layer exists to prevent. One
    consequence is worth stating rather than discovering: SQL says ``NULL`` is not equal to
    ``NULL``, so the constraint does **not** bind rows with no owning tenant. Platform-level
    documents are deduplicated by whatever schedules them, not by this index.

    ``workflow_id`` is again a plain nullable string and not a foreign key, for the reason
    given on :class:`JobRun`: it is nullable here because a document exists from the moment
    its bytes land, before any ingestion has been scheduled for it.

    Where the specification was silent: ``page_count`` and ``chunk_count`` are nullable
    because they are *discovered* by the parse and chunk stages rather than known at
    upload, so ``NULL`` honestly means "not parsed yet" where ``0`` would claim an empty
    document.

    The three D7 prefix fields — ``title``, ``doc_type``, ``doc_date`` — are the correction
    recorded under that decision. D7 assumed all four of its fields were already on this
    row; two thirds of that was false, and this is where the missing three live. Each is
    nullable, and each is nullable for its own reason rather than for tidiness:

    * ``title`` is *derived*, by the parse stage, from the document's first heading (which
      on every fixture is the real printed title), falling back to the filename stem. It
      is ``NULL`` between the upload and the parse, which is the honest reading of "we
      have not opened the file yet".
    * ``doc_type`` and ``doc_date`` can only be **supplied by the tenant at upload**. A
      MIME type is ``application/pdf`` for the whole corpus and so discriminates nothing,
      and there is nothing in the bytes that reliably states either. Left ``NULL`` they
      degrade to :func:`aegis.retrieval.chunker.chunk_prefix`'s ``untyped`` / ``undated``
      placeholders — a stated absence, which keeps the prefix's *shape* constant across a
      corpus, rather than a confident wrong value.

    ``doc_date`` is a ``date`` and it is **never** derived from ``created_at``. That column
    is when somebody uploaded the file; using it would stamp every chunk of a 2019 contract
    with 2026 and would do so invisibly, because a plausible date looks exactly like a
    correct one.
    """

    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(ForeignKey("tenants.id"), index=True)
    filename: Mapped[str] = mapped_column(String(512))
    # Indexed as well as uniquely constrained: the unique index is composite and leads on
    # ``tenant_id``, so it cannot serve a "have we seen these bytes anywhere" lookup.
    content_sha256: Mapped[str] = mapped_column(String(64), index=True)
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int]
    # D7's three prefix fields; see the class docstring for why each is nullable and why
    # ``doc_date`` may never be back-filled from ``created_at``. ``title`` is sized like
    # ``filename`` because its fallback *is* the filename stem.
    title: Mapped[str | None] = mapped_column(String(512), default=None)
    doc_type: Mapped[str | None] = mapped_column(String(128), default=None)
    doc_date: Mapped[date | None] = mapped_column(default=None)
    status: Mapped[JobStatus] = mapped_column(_JOB_STATUS, index=True)
    completed_stage: Mapped[str | None] = mapped_column(String(64), default=None)
    workflow_id: Mapped[str | None] = mapped_column(String(255), index=True, default=None)
    page_count: Mapped[int | None] = mapped_column(default=None)
    chunk_count: Mapped[int | None] = mapped_column(default=None)
    error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        UniqueConstraint("tenant_id", "content_sha256", name="uq_documents_tenant_sha"),
    )


class Chunk(AegisBase):
    """One retrievable passage of an ingested document, plus its embedding-of-record.

    This table is the **lexical** arm of retrieval. Dense search runs on the vector store
    (:class:`aegis.retrieval.vector_store.ChromaVectorStore`); ``embedding`` here is the
    durable JSON source-of-record that index rebuilds replay from, not a search index —
    this cluster has no ``pgvector`` and nothing in the pipeline assumes one.

    Two columns are new, and both are load-bearing:

    ``document_id`` replaces a ``doc_id VARCHAR(255)`` that referenced nothing. It could
    not be joined to ``documents.id`` at all (a string against an integer), so there was
    no way to get from a chunk back to the document that produced it — which is what
    citation provenance, re-index and cascade deletion each need. ``ON DELETE CASCADE``
    because re-ingesting a document otherwise leaves its old chunks behind, still
    answering queries from text the tenant believes they replaced.

    ``tenant_id`` is **denormalised onto this row on purpose**, even though it is
    reachable through ``documents``. An RLS policy that has to join to find the owner
    makes the *join* the boundary rather than the row, and a parent's policy does not
    protect what is reached another way — the exact failure this platform measured on
    ``run_events``' partitions, where a scoped connection saw one tenant through the
    parent and both through the partition. The predicate has to sit on the row it
    protects, so the owner does.

    It is ``NOT NULL`` where ``documents.tenant_id`` is nullable, and the asymmetry is
    deliberate rather than an oversight. Under the ``tenant_isolation`` predicate
    ``NULL = <scope>`` is NULL — not true — so a null-tenant chunk would be invisible to
    every tenant while still being counted, indexed and paid for. Making it
    unrepresentable is better than making it useless: a platform-level document simply
    does not own rows in this table.

    The composite index leads on ``tenant_id`` because every read is tenant-scoped first
    and document-scoped second — the RLS predicate is on ``tenant_id``, so an index that
    led with ``document_id`` could not serve it.

    ``search_vector`` is what makes the lexical arm corpus-wide. It is a **generated**
    ``tsvector``, not a column a writer fills: derived by the database from ``content``
    on every insert and update, it cannot drift out of step with the text it indexes the
    way a second search system — a separate BM25 index, kept in sync by application code
    — can and eventually does. That, and not ranking quality, is why the phase chose
    PostgreSQL full-text search: the keyword predicate and the tenant predicate end up on
    **the same row**, so the tenant filter is a ``WHERE`` clause rather than a second
    store to remember to scope. Ranking is read honestly at the query site — see
    :meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.keyword_recall`, which says
    plainly that ``ts_rank_cd`` is not Okapi BM25.
    """

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    # NOT NULL and a real FK: see the class docstring for why the owner is duplicated
    # here rather than resolved through ``documents``. Deliberately *not* ``index=True``:
    # the composite below already leads on ``tenant_id``, so a second single-column index
    # would serve no query the composite cannot and would cost a write on every chunk of
    # every ingest — the hottest insert path in the system.
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id"))
    # Typed to match ``documents.id`` exactly. That column is a plain ``integer``
    # (``Mapped[int]``), not the ``BIGINT`` the phase document assumed; declaring
    # ``BIGINT`` here would still work — PostgreSQL has an ``int8 = int4`` operator — but
    # it would leave the referencing side a width the referenced index is not, which is a
    # trap to inherit rather than a guarantee to keep.
    #
    # Indexed on its own as well, and this index is *not* redundant with the composite:
    # that one leads on ``tenant_id``, so it cannot serve the document-only lookup the
    # ``ON DELETE CASCADE`` performs, and an unindexed FK child turns every document
    # delete into a table scan.
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), index=True
    )
    persona: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    content: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(VectorColumn(EMBED_DIM))
    meta: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    # PostgreSQL computes this from ``content``; no writer may set it, and a rewrite of
    # ``content`` rewrites it in the same statement. The text search configuration is
    # named explicitly (``'english'`` rather than the one-argument form) for two reasons:
    # ``to_tsvector(regconfig, text)`` is IMMUTABLE, which a generated column requires,
    # and the one-argument form would make every stored vector depend on the *server's*
    # ``default_text_search_config`` — a setting outside this schema, whose change would
    # silently leave the index stemming differently from the queries run against it.
    # :data:`aegis.retrieval.lightrag_backend._FTS_CONFIG` is the query side of this pair
    # and must name the same configuration.
    search_vector: Mapped[str | None] = mapped_column(
        TSVECTOR, Computed("to_tsvector('english', content)", persisted=True)
    )

    __table_args__ = (
        Index("ix_chunks_tenant_document", "tenant_id", "document_id"),
        # GIN rather than GiST: this index is read on every query and written only at
        # ingest, which is exactly the trade GIN makes (slower to build, substantially
        # faster to search, and lossless — a GiST text-search index returns candidates
        # that must be rechecked against the row).
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )
