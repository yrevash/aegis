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
from sqlalchemy import ForeignKey, Index, String, Text, func, text
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
    "Notification",
    "NotificationKind",
    "NotificationSeverity",
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
    #: Every call this ONE gate authorises, not only the representative in ``action``.
    #: A fan-out can propose three consequential writes in a single turn; the live
    #: ``approval_required`` event has enumerated all of them since commit ``7285bd6``,
    #: and the durable row did not — so the inbox showed one write while approving ran
    #: three. Nullable (not a defaulted NOT NULL) because that is what
    #: ``reconcile_additive_columns`` can install without a migration; a row written
    #: before this column existed reads as ``None`` and falls back to the representative.
    actions: Mapped[list[dict[str, Any]] | None] = mapped_column(JsonB, default=None)
    #: The ``users.id`` whose run raised this gate, when the run was made by a real
    #: user. This is what lets a tenant's own user see the fate of the gates *they*
    #: raised without being able to see anybody else's — the client half of the inbox.
    #: Plain indexed column, as ``tenant_id`` above: no cross-package DDL foreign key.
    requested_by: Mapped[int | None] = mapped_column(default=None, index=True)
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

    #: ``(session_id, turn_index)`` is **not** a unique index, and that is a decision
    #: rather than an oversight. ``turn_index`` is a ``SELECT count(*)``, so two
    #: concurrent appenders on one conversation could in principle both count *n* and
    #: both write *n*. What stops them is ordering, in
    #: :func:`app.data.chat.append_chat_turn`: the ``UPDATE`` that bumps
    #: ``last_active_at`` runs **first** and takes this session row's write lock, so the
    #: second appender blocks there instead of counting stale. The lock is the
    #: serialisation point and the index is only a read path.
    #:
    #: Why not both — a unique index as the backstop? Because this schema is created by
    #: ``create_all``, which is ``CREATE TABLE IF NOT EXISTS`` and **never alters a table
    #: that already exists** (see :mod:`app.data.session`). Declaring ``unique=True``
    #: here would give a fresh database the constraint and leave every existing one
    #: without it, while every reader of this file believed the invariant was enforced.
    #: A protection that half the deployments do not have, asserted in the model as
    #: though they all did, is the failure mode this codebase keeps paying for. Add the
    #: unique index the day a real migration tool lands, in the same change that
    #: backfills the duplicates it would reject.
    __table_args__ = (
        Index("ix_chat_messages_session_turn", "session_id", "turn_index"),
    )


class McpServer(Base):
    """A declared external MCP server — the durable half of an MCP connection (§10.6).

    What an operator adds in the console and expects to still be there tomorrow: the
    namespace id, a label, the endpoint, and which header the peer wants its credential
    in. Platform-scoped by construction — there is deliberately **no** ``tenant_id``:
    which third party's code an agent may reach is a decision about the whole fleet, so
    there is nothing here for a per-tenant RLS policy to isolate.

    **The credential is not in this table, and there is no column for it.** A secret set
    through the console lives in the serving process and nowhere else, so there is
    nothing to read back out of the API and nothing to read out of a database dump
    either. What persists is :attr:`credential_fingerprint` — twelve hex characters of
    SHA-256, enough for an operator to tell "the key I pasted" from "the key that was
    already there" and useless for recovering either — plus who set it and when. A
    deployment that wants a restart to re-arm the connection without a human supplies
    the secret the way it supplies ``GENAILAB_API_KEY``: an environment variable,
    ``AEGIS_MCP_CRED_<SERVER_ID>``.

    ``enabled`` is not a soft delete. A disabled server's tools leave the planner's
    payload entirely (``ExternalToolRegistry._visible``), because a tool the model can
    still see is a tool it will still try.
    """

    __tablename__ = "mcp_servers"

    #: The Aegis-side id, which **is** the tool namespace (``mcp__<id>__<tool>``). The
    #: primary key rather than a surrogate, because two rows with the same namespace is
    #: not a state the tool table can represent.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    label: Mapped[str] = mapped_column(String(128), default="")
    url: Mapped[str] = mapped_column(String(1024), default="")
    #: The header this peer wants its credential in — ``Authorization`` for a bearer,
    #: ``X-API-Key`` for plenty of others. Per server because there is no one answer.
    auth_header: Mapped[str] = mapped_column(String(64), default="Authorization")
    enabled: Mapped[bool] = mapped_column(default=True)
    #: A non-reversing fingerprint of the credential last set, or ``""``. Never the
    #: credential.
    credential_fingerprint: Mapped[str] = mapped_column(String(32), default="")
    credential_set_by: Mapped[str | None] = mapped_column(String(255), default=None)
    credential_set_at: Mapped[datetime | None] = mapped_column(default=None)
    created_by: Mapped[str | None] = mapped_column(String(255), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(default=None)


class NotificationKind(StrEnum):
    """The vocabulary of things worth telling somebody about, unprefixed by any UI.

    A ``<subject>.<event>`` name rather than a screen name, because the alert outlives
    the panel that first rendered it: ``job.succeeded`` still means the same thing when
    the jobs page is redesigned, and ``ingest_toast`` would not.

    Stored as a plain ``String`` column, **not** a native Postgres enum, and that is the
    one schema decision in this table that is worth arguing. A native enum would need
    :func:`aegis.governance.schema.reconcile_enum_values` to run before any row carrying
    a new label could be written — the exact ``invalid input value for enum`` failure
    that ``run_status.REJECTED`` produced — and a notification is the last thing that
    should be able to fail a boot or a write. The Python enum is therefore the
    *vocabulary* (what emitters spell, what the frontend switches on) and the column is
    the *storage*: adding a kind is one line here and no DDL anywhere.
    """

    JOB_SUCCEEDED = "job.succeeded"
    JOB_FAILED = "job.failed"
    APPROVAL_AWAITING = "approval.awaiting"
    BUDGET_EXCEEDED = "budget.exceeded"
    SLA_AUTO_DECIDED = "sla.auto_decided"


class NotificationSeverity(StrEnum):
    """How loudly a notification should read. Three values, deliberately.

    ``info`` is "this finished", ``warning`` is "this needs you", ``critical`` is "this
    stopped work". A fourth level would have to be explained on every screen that
    renders a colour for it, and the emitters here have never needed one.
    """

    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Notification(Base):
    """One durable alert for a tenant, and optionally for one user inside it.

    **Durable first, pushed second.** This row is the notification; the SSE frame that
    :mod:`app.notifications.bus` publishes is a *copy in flight*. A user who was
    disconnected when their ingest finished sees it on their next load because the row
    is here — an alert that only ever existed in a process's memory is not an alert, it
    is a toast. That ordering is enforced in :func:`app.data.notifications.emit`, which
    commits before it publishes and publishes nothing when the commit wrote no row.

    Scoping, and the two columns that carry it
    ------------------------------------------

    ``tenant_id`` is who owns it. ``user_id`` is optional and narrows it further:
    ``NULL`` means *everyone in that tenant* (an ingest finished, a gate is waiting),
    and a value means *this person only*. The read predicate is therefore
    ``tenant_id = :scope AND (user_id IS NULL OR user_id = :me)`` — see
    :func:`app.data.notifications.scope_predicate`, which is the single place it is
    written and is used by the list, the count, both mark-read writes **and** the SSE
    filter, so a stream cannot disagree with the list about what a principal may see.

    Like ``Approval`` and the chat tables, ``tenant_id`` is a plain indexed column with
    no cross-package DDL foreign key to ``aegis.governance``'s ``tenants``; the
    ``tenant_isolation`` RLS policy plus the app-level predicate provide the isolation.
    ``notifications`` is registered in
    :data:`aegis.governance.rls._TENANT_SCOPED_TABLES` in the same change that declares
    it — this deployment runs the **fail-open** predicate, so the app-level filter is
    load-bearing and the policy is the second lock, not the only one.

    Why ``dedupe_key`` is a UNIQUE column and not a Python ``if``
    ------------------------------------------------------------

    Every emitter in this platform can run twice. A Temporal activity commits and dies
    before the orchestrator records its completion, and replays in a fresh worker; the
    SLA sweeper runs every cycle; a re-uploaded document takes the same code path as a
    new one. Each call site *does* carry its own guard (``finish_ingest`` emits only
    when its ``WHERE status IS DISTINCT FROM`` matched a row), but a guard in the caller
    protects only that caller, and "two rows claiming one event" is not a state this
    table should be able to hold at all. So the second alert is refused by a unique
    index and an ``ON CONFLICT DO NOTHING``: the insert reports no row, and
    :func:`app.data.notifications.emit` publishes nothing, so a replay produces neither
    a duplicate row nor a duplicate frame.

    ``NULL`` is allowed and is not constrained (Postgres permits many NULLs in a unique
    index), which is the honest default for an event that genuinely happens repeatedly
    and should be reported every time.
    """

    __tablename__ = "notifications"

    #: A uuid4 hex, minted by the emitter. A string rather than a serial because it is
    #: the SSE frame's identity too, and the frame is built before the transaction that
    #: would allocate a sequence value has committed.
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    #: ``NULL`` == everyone in the tenant. See the class docstring.
    user_id: Mapped[int | None] = mapped_column(default=None, index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    severity: Mapped[str] = mapped_column(String(16), default=NotificationSeverity.INFO)
    title: Mapped[str] = mapped_column(String(255), default="")
    body: Mapped[str] = mapped_column(Text(), default="")
    #: ``job:21`` / ``document:23`` / ``approval:seed-gate-northwind`` — what the alert
    #: is *about*, in a form the frontend can key on without parsing prose out of
    #: ``body``. Indexed because "is there already an alert about this document" is the
    #: one question anything other than the inbox asks of this table.
    entity_ref: Mapped[str | None] = mapped_column(String(255), default=None, index=True)
    #: The in-app path to open, e.g. ``/app/tenant_admin/jobs``. Server-chosen rather
    #: than derived in the browser: the emitter is the only thing that knows which
    #: surface actually shows this entity, and a client-side mapping table would be a
    #: second copy of the routing that silently rots.
    href: Mapped[str | None] = mapped_column(String(512), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    #: ``NULL`` until read. A timestamp rather than a boolean because "when did they see
    #: it" is a question the boolean cannot answer and costs nothing to keep.
    read_at: Mapped[datetime | None] = mapped_column(default=None)
    #: The idempotency key described in the class docstring, or ``NULL`` for an event
    #: that should be reported every time it happens.
    dedupe_key: Mapped[str | None] = mapped_column(String(255), default=None)

    __table_args__ = (
        # The inbox query, exactly: "this tenant's notifications, newest first". The
        # composite index is what keeps that a range scan rather than a sort over the
        # tenant's whole history once a busy tenant has months of rows.
        Index("ix_notifications_tenant_created", "tenant_id", "created_at"),
        # The unread badge's query, and the ``read-all`` write's WHERE clause. Partial
        # on PostgreSQL — read rows are the overwhelming majority and are never in the
        # answer, so indexing them would be paying for the rows the query excludes.
        Index(
            "ix_notifications_unread",
            "tenant_id",
            "user_id",
            postgresql_where=text("read_at IS NULL"),
        ),
        # The idempotency guarantee. UNIQUE rather than merely indexed: see the class
        # docstring — the emitter's ``ON CONFLICT DO NOTHING`` is what turns a replay
        # into a no-op, and it has nothing to conflict *on* without this.
        Index("ux_notifications_dedupe", "dedupe_key", unique=True),
    )


# ``Chunk`` / ``EvalResult`` / ``PromptStatus`` / ``PromptVersion`` are imported from
# ``aegis.jobs.models`` / ``aegis.ops.models`` (both on ``aegis.data.AegisBase``) and
# re-exported above under their historical names. Backend ``create_all`` covers them via
# the ``AegisBase`` metadata.
