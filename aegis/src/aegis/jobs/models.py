"""SQLAlchemy ORM for the durable job substrate — ``job_runs`` and ``documents``.

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

Both tables carry ``tenant_id`` and are therefore registered in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`, which is what earns them a
``tenant_isolation`` Row-Level Security policy at boot. Registration is not optional
bookkeeping: an unregistered table with a ``tenant_id`` column looks governed from the
outside and is not, and the boot-time catalog read-back exists precisely to report that.

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

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

# Registration side-effect, and deliberately not a lazy import: the foreign keys below
# reference ``tenants.id`` / ``users.id``, which SQLAlchemy resolves by name against the
# shared metadata at mapper-configuration time. See the module docstring.
import aegis.governance.models  # noqa: F401
from aegis.data import AegisBase, JsonB

__all__ = [
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
