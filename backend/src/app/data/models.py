"""SQLAlchemy 2.0 ORM for the relational store (Postgres) + embeddings-of-record.

The **tenancy/governance tables** (``Tenant``, ``User``, ``Budget``, ``UsageLedger``,
``AuditLog`` + their enums) now live in the standalone ``aegis.governance.models`` on the
shared :class:`aegis.data.AegisBase` metadata, and are re-exported here under their
historical names so every ``from app.data.models import User`` call site is unchanged.

The **evals / LLM-Ops tables** (``EvalResult``, ``PromptVersion`` + the ``PromptStatus``
enum) now live in the standalone ``aegis.ops.models`` on the shared
:class:`aegis.data.AegisBase` metadata, and are re-exported here under their historical
names so every ``from app.data.models import EvalResult`` call site is unchanged.

The **retrieval corpus** (``Chunk``) now lives in ``aegis.jobs.models`` beside the
``Document`` it is parsed out of, on the same :class:`aegis.data.AegisBase` metadata, and
is re-exported here under its historical name. It had to move: its ``document_id`` is a
real ``ForeignKey`` with ``ON DELETE CASCADE``, and SQLAlchemy resolves a foreign key by
name within **one** MetaData — a ``chunks`` on this ``Base`` could not reference a
``documents`` on that one, so the relationship would have been a naming convention the
database never checked.

The tables that still belong to the platform stay here on their own :class:`Base`:
``Approval`` (agent HITL) and the console's chat transcript (``ChatSession`` /
``ChatMessage``). Their ``tenant_id`` is a plain indexed column (no cross-package
foreign key to the now-separate ``aegis.governance`` ``tenants`` table — mirroring how
``aegis.memory`` isolates at the query/RLS layer); the belt-and-suspenders tenant scoping
+ Postgres RLS provide the isolation, not a DDL foreign key. Both chat tables are
registered in :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` in the same change that
declares them — a tenant-scoped table that arrives without a policy looks governed from
the outside and leaks silently, which is the gap Phase 4's audit found five times.

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
from aegis.jobs.models import Chunk
from aegis.ops.models import EvalResult, PromptStatus, PromptVersion
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, Text, func
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
    "ChatMessage",
    "ChatSession",
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
    """Declarative base for the platform-owned tables (the approvals inbox).

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


class ChatSession(Base):
    """One console conversation — the transcript's thread record (task 6.2).

    **Its ``id`` is deliberately the same string as** ``memory_session.id``. The console
    mints one id, sends it as ``QueryRequest.session_id``, and both the transcript here
    and :class:`aegis.memory.stores.MemorySession` key off it — so "what did we say" and
    "what do you remember" can never disagree about which conversation is meant. That is
    also why the primary key is a ``String(64)`` rather than a native ``uuid``: a uuid
    column here and a varchar there would be the same identity in two types, and one of
    the two joins would have to cast.

    It is a **separate table** from ``memory_session`` rather than a widening of it. That
    row is the memory subsystem's own working state (rolling summary, turn counter,
    subject) and belongs to :mod:`aegis.memory`; overloading it as the chat store would
    make the console's transcript a hostage of the memory tier's retention policy — the
    forgetting sweep archives memory rows on purpose, and a user's chat history must not
    disappear because a consolidation job decided a fact was stale.
    """

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Plain indexed column, as on ``Approval``: isolation is the app-level predicate
    # plus the ``tenant_isolation`` RLS policy, not a cross-package DDL foreign key.
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    user_id: Mapped[int | None] = mapped_column(default=None, index=True)
    title: Mapped[str] = mapped_column(String(255), default="New chat")
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    last_active_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), index=True
    )


class ChatMessage(Base):
    """One turn of a console conversation, in order (task 6.2).

    ``run_id`` links an assistant turn back to the run that produced it, so the trace,
    the cost ledger and the transcript are one story rather than three. It is ``None``
    on a user turn, which has no run of its own.

    ``tenant_id`` is carried on the row rather than reached through
    :class:`ChatSession`: an RLS predicate that has to join to find the owner makes the
    join the boundary instead of the row, and a parent's policy does not protect what is
    reached another way. Same reasoning as ``chunks`` vs ``documents``.
    """

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True
    )
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    turn_index: Mapped[int] = mapped_column(default=0)
    role: Mapped[str] = mapped_column(String(32), default="user")
    content: Mapped[str] = mapped_column(Text())
    run_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    __table_args__ = (
        Index("ix_chat_messages_session_turn", "session_id", "turn_index"),
    )


# ``Chunk`` / ``EvalResult`` / ``PromptStatus`` / ``PromptVersion`` are imported from
# ``aegis.jobs.models`` / ``aegis.ops.models`` (both on ``aegis.data.AegisBase``) and
# re-exported above under their historical names. Backend ``create_all`` covers them via
# the ``AegisBase`` metadata.
