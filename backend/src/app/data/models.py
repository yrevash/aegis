"""SQLAlchemy 2.0 ORM for the relational store (Postgres) + embeddings-of-record.

The **tenancy/governance tables** (``Tenant``, ``User``, ``Budget``, ``UsageLedger``,
``AuditLog`` + their enums) now live in the standalone ``aegis.governance.models`` on the
shared :class:`aegis.data.AegisBase` metadata, and are re-exported here under their
historical names so every ``from app.data.models import User`` call site is unchanged.

The **evals / LLM-Ops tables** (``EvalResult``, ``PromptVersion`` + the ``PromptStatus``
enum) now live in the standalone ``aegis.ops.models`` on the shared
:class:`aegis.data.AegisBase` metadata, and are re-exported here under their historical
names so every ``from app.data.models import EvalResult`` call site is unchanged.

The tables that still belong to the platform stay here on its own :class:`Base`:
``Approval`` (agent HITL) and ``Chunk`` (retrieval). Their ``tenant_id`` is a plain indexed
column (no cross-package foreign key to the now-separate ``aegis.governance`` ``tenants``
table — mirroring how ``aegis.memory`` isolates at the query/RLS layer); the
belt-and-suspenders tenant scoping + Postgres RLS provide the isolation, not a DDL foreign
key.

Both metadatas (this ``Base`` and ``AegisBase``) are created by ``app.data.session.bootstrap``.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from aegis.core.types import RiskLevel
from aegis.data import EMBED_DIM, JsonB, UtcDateTime, VectorColumn
from aegis.governance.models import (
    AuditLog,
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    TenantStatus,
    UsageLedger,
    User,
)
from aegis.ops.models import EvalResult, PromptStatus, PromptVersion
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

__all__ = [
    "EMBED_DIM",
    "Approval",
    "ApprovalStatus",
    "AuditLog",
    "Base",
    "Budget",
    "BudgetScope",
    "BudgetWindow",
    "Chunk",
    "EvalResult",
    "JsonB",
    "PromptStatus",
    "PromptVersion",
    "Tenant",
    "TenantStatus",
    "UsageLedger",
    "User",
    "VectorColumn",
]


class ApprovalStatus(StrEnum):
    """Lifecycle of a durable approvals-inbox row (§1.3).

    ``PENDING`` → (``APPROVED`` | ``REJECTED`` | ``ESCALATED`` | ``EXPIRED``);
    a winning resumer flips ``APPROVED`` → ``RESUMING`` under an optimistic lock.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    RESUMING = "resuming"
    ESCALATED = "escalated"
    EXPIRED = "expired"


class Base(DeclarativeBase):
    """Declarative base for the platform-owned tables (approvals/chunks/evals/prompts).

    The tenancy/governance tables live on :class:`aegis.data.AegisBase` (via
    ``aegis.governance.models``); the host creates both metadatas at bootstrap.

    Every ``Mapped[datetime]`` here materialises as :class:`aegis.data.UtcDateTime`
    (``timestamptz`` on PostgreSQL, naive-UTC ``DATETIME`` on SQLite) — matching
    ``AegisBase`` so both metadatas share one timestamp contract. The application layer
    is aware-UTC throughout; a naive column made asyncpg reject every aware bind, which
    is what silently killed the SLA sweeper (it threw once per cycle).
    """

    type_annotation_map = {datetime: UtcDateTime}


class Approval(Base):
    """A durable approvals-inbox row — the source of truth for a paused run (§1.3).

    Written when a run interrupts at the human gate. It survives a restart (the
    LangGraph checkpoint is keyed by ``thread_id`` == ``run_id``); an out-of-band
    decision flips ``status`` and a resumer continues the run. Carries the SLA
    deadline and escalation tier for the async sweeper.
    """

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    # Plain indexed column (no cross-package FK to aegis.governance ``tenants``); the
    # per-tenant Postgres RLS policy on ``approvals`` + app-scoping provide isolation.
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    status: Mapped[ApprovalStatus] = mapped_column(
        SAEnum(ApprovalStatus, name="approval_status"),
        default=ApprovalStatus.PENDING,
        index=True,
    )
    persona: Mapped[str | None] = mapped_column(String(128), default=None)
    action: Mapped[str] = mapped_column(String(255))
    args: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    risk: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, name="approval_risk"), default=RiskLevel.LOW
    )
    rationale: Mapped[str | None] = mapped_column(String(), default=None)
    #: Model evidence frozen at gate time. No longer written by the agent (the ML
    #: step was removed from the graph), so new rows carry ``{}``. The column is kept
    #: deliberately — dropping it needs a migration and those are deferred — and the
    #: console guards on the value being absent, so an empty dict renders cleanly.
    ml_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    assignee_tier: Mapped[str | None] = mapped_column(String(64), default=None)
    sla_deadline: Mapped[datetime | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(String(255), default=None)


class Chunk(Base):
    """A retrievable text chunk plus its embedding-of-record (reused by retrieval).

    The embedding column is the durable JSON source-of-record for chunk vectors; the
    retrieval pipeline writes here at ingest time. Nearest-neighbour (ANN) search runs
    in the embedded vector store — this column is the mirror source, not a search index.
    """

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(255), index=True)
    persona: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    content: Mapped[str] = mapped_column(String())
    embedding: Mapped[list[float]] = mapped_column(VectorColumn(EMBED_DIM))
    meta: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)


# ``EvalResult`` / ``PromptStatus`` / ``PromptVersion`` are imported from
# ``aegis.ops.models`` (on ``aegis.data.AegisBase``) and re-exported above under their
# historical names. Backend ``create_all`` covers them via the ``AegisBase`` metadata.
