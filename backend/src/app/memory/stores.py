"""SQLAlchemy models for the long-term memory tiers (episodic / semantic / audit).

These register on the shared :class:`app.data.models.Base` metadata, so
``metadata.create_all`` materialises them alongside the governance tables — on
PostgreSQL with native ``vector``/``jsonb`` columns, and on the SQLite test database
via the cross-dialect ``VectorType``/``JsonB`` decorators (vectors stored as JSON).

**Isolation is app-level first.** Every recall/persist query MUST filter by
``subject_id`` (and ``tenant_id`` when present) in the ``WHERE`` clause — this is the
primary, NULL-safe, dialect-independent isolator. Postgres RLS is only an additive belt
and, as bootstrapped, fails closed on a NULL ``tenant_id`` (see ``docs/MEMORY_SPEC.md``
hardening notes), so these tables are deliberately **not** auto-added to ``_RLS_TABLES``;
a NULL-tolerant policy is a separate, opt-in step.

**Bitemporal facts (Zep).** A fact is never hard-deleted: ``valid_at``/``invalid_at`` is
world-time (when it became / ceased to be true) and ``created_at``/``expired_at`` is
transaction-time (system ingest / supersession). Contradiction sets ``invalid_at`` and
adds a new row; refinement sets ``expired_at`` + ``supersedes_id``. History is auditable.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.data.models import EMBED_DIM, Base, JsonB, VectorType


class MemoryOrigin(StrEnum):
    """Provenance of a stored memory item — governs read-time trust (spotlighting)."""

    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"
    REFLECTION = "reflection"


class WriteOp(StrEnum):
    """The bitemporal fact-write operations recorded in :class:`MemoryWriteLog`."""

    ADD = "add"
    UPDATE = "update"
    INVALIDATE = "invalidate"
    NOOP = "noop"
    PRUNE = "prune"  # soft-archived out of hot recall by the forgetting sweep (never deleted)


class ConsolidationStatus(StrEnum):
    """Lifecycle of a durable consolidation job (queue-backed, not fire-and-forget)."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    ERROR = "error"


class MemorySession(Base):
    """A conversation thread; holds the running summary and the turn counter."""

    __tablename__ = "memory_session"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    persona: Mapped[str | None] = mapped_column(String(128), default=None)
    turn_count: Mapped[int] = mapped_column(default=0)
    summary: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True
    )


class MemoryMessage(Base):
    """Episodic memory: one row per turn (user/assistant/tool), optionally embedded.

    The user turn may reuse the query embedding already computed by retrieval — but
    only when ``embedding_dim == EMBED_DIM`` and it is a real gateway vector; otherwise
    ``embedding`` stays ``None`` until the batched consolidation embed. ``embedding_dim``
    records the stored vector's dimensionality so a lite-mode 256-dim vector is never
    mistaken for a recall-comparable one.
    """

    __tablename__ = "memory_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("memory_session.id"), index=True
    )
    turn_index: Mapped[int] = mapped_column(default=0)
    role: Mapped[str] = mapped_column(String(32))
    origin: Mapped[MemoryOrigin] = mapped_column(
        SAEnum(MemoryOrigin, name="memory_origin"), default=MemoryOrigin.USER
    )
    content: Mapped[str] = mapped_column(Text())
    embedding: Mapped[list[float] | None] = mapped_column(
        VectorType(EMBED_DIM), default=None
    )
    embedding_dim: Mapped[int | None] = mapped_column(default=None)
    importance: Mapped[int] = mapped_column(default=5)
    access_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    last_access_at: Mapped[datetime | None] = mapped_column(default=None)
    run_id: Mapped[str | None] = mapped_column(String(64), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)

    __table_args__ = (
        Index("ix_memory_message_subject_created", "subject_id", "created_at"),
        Index("ix_memory_message_session_turn", "session_id", "turn_index"),
    )


class MemoryFact(Base):
    """Semantic memory: a durable, typed, bitemporal fact about the subject.

    Currently-valid facts are those with ``invalid_at IS NULL AND expired_at IS NULL``
    — that is the hot-recall predicate. Superseded/contradicted rows are retained for
    audit and the belief timeline.
    """

    __tablename__ = "memory_fact"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    fact_type: Mapped[str] = mapped_column(String(64), default="fact")
    subject: Mapped[str] = mapped_column(String(255), default="")
    predicate: Mapped[str] = mapped_column(String(255), default="")
    object: Mapped[str] = mapped_column(String(1024), default="")
    text: Mapped[str] = mapped_column(Text())
    embedding: Mapped[list[float] | None] = mapped_column(
        VectorType(EMBED_DIM), default=None
    )
    confidence: Mapped[float] = mapped_column(default=1.0)
    importance: Mapped[int] = mapped_column(default=5)
    access_count: Mapped[int] = mapped_column(default=0)
    valid_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    invalid_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    expired_at: Mapped[datetime | None] = mapped_column(default=None, index=True)
    last_access_at: Mapped[datetime | None] = mapped_column(default=None)
    source_turn_ids: Mapped[list[int]] = mapped_column(JsonB, default=list)
    supersedes_id: Mapped[int | None] = mapped_column(default=None)

    __table_args__ = (
        Index("ix_memory_fact_subject_pred", "subject_id", "predicate"),
    )


class MemoryProfile(Base):
    """Semantic memory: the structured, always-injected profile ("human block")."""

    __tablename__ = "memory_profile"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    data: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("ux_memory_profile_subject", "subject_id", "tenant_id", unique=True),
    )


class MemoryWriteLog(Base):
    """Append-only audit of every fact write (ADD/UPDATE/INVALIDATE/NOOP/PRUNE).

    The "why does the agent believe X" trail: each row links the operation to the
    before/after fact state, the model that decided it, and the trace it came from —
    the feed for the later Memory-changelog frontend view.
    """

    __tablename__ = "memory_write_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    op: Mapped[WriteOp] = mapped_column(SAEnum(WriteOp, name="memory_write_op"))
    fact_id: Mapped[int | None] = mapped_column(default=None)
    before: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    reason: Mapped[str | None] = mapped_column(Text(), default=None)
    model: Mapped[str | None] = mapped_column(String(255), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None)
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)


class MemoryConsolidationJob(Base):
    """Durable consolidation queue — written synchronously in ``persist_memory``.

    Fire-and-forget ``asyncio.create_task`` loses work on redeploy/error; this row is
    the honest-durability seam: enqueue ``PENDING`` in the request, flip to ``DONE`` in
    the background task, and let a periodic sweep re-run stragglers.
    """

    __tablename__ = "memory_consolidation_job"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[ConsolidationStatus] = mapped_column(
        SAEnum(ConsolidationStatus, name="memory_consolidation_status"),
        default=ConsolidationStatus.PENDING,
        index=True,
    )
    attempts: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now()
    )


__all__ = [
    "ConsolidationStatus",
    "MemoryConsolidationJob",
    "MemoryFact",
    "MemoryMessage",
    "MemoryOrigin",
    "MemoryProfile",
    "MemorySession",
    "MemoryWriteLog",
    "WriteOp",
]
