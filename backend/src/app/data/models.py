"""SQLAlchemy 2.0 ORM models for the relational + vector store (Postgres/pgvector).

One local PostgreSQL database serves both the relational needs (users/RBAC, the
first-class audit log, offline eval results) and the vector needs (chunk
embeddings for retrieval), per ``docs/backend.md`` §6.

Portability note: the vector and JSON columns are declared with cross-dialect
type decorators so the schema also materialises on SQLite (used by the unit
tests, which must run with no Postgres). On PostgreSQL the columns compile to the
native ``vector`` and ``jsonb`` types; on other dialects they fall back to
``JSON`` so ``metadata.create_all`` still succeeds.

Verified against: SQLAlchemy 2.0.x typed ``Mapped``/``mapped_column`` style and
pgvector-python (``pgvector.sqlalchemy.Vector``), August 2026.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import JSON, ForeignKey, Index, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from app.api.schemas import RiskLevel, Role

# Embedding dimensionality of the fixed embedding model
# (``genailab-maas-text-embedding-3-large`` → 3072 dims).
EMBED_DIM = 3072

# A JSON column that uses native ``jsonb`` on PostgreSQL and portable ``JSON``
# elsewhere (keeps ``create_all`` working on the SQLite test database).
JsonB = JSON().with_variant(JSONB, "postgresql")


# ─────────────────────────────────────────────────────────────────────────────
# Governance / tenancy enums (data-layer contracts; §3.3, §1.3)
# ─────────────────────────────────────────────────────────────────────────────


class TenantStatus(StrEnum):
    """Lifecycle status of a tenant (enterprise client)."""

    ACTIVE = "active"
    SUSPENDED = "suspended"


class BudgetScope(StrEnum):
    """Which level a budget row governs (enforced inward: user cannot exceed tenant)."""

    TENANT = "tenant"
    USER = "user"


class BudgetWindow(StrEnum):
    """The rolling window a budget cap applies over."""

    DAY = "day"
    MONTH = "month"


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


class VectorType(TypeDecorator[list[float]]):
    """A pgvector column that degrades to ``JSON`` on non-Postgres dialects.

    On PostgreSQL this compiles to ``vector(dim)`` and supports the pgvector
    distance operators; on SQLite (unit tests) it stores the vector as a JSON
    array so the table can still be created and rows round-tripped.
    """

    impl = Vector
    cache_ok = True

    def __init__(self, dim: int) -> None:
        """Store the vector dimensionality and initialise the underlying type."""
        self.dim = dim
        super().__init__(dim)

    def load_dialect_impl(self, dialect: Any) -> Any:  # noqa: ANN401 - SQLAlchemy hook
        """Return ``vector`` on PostgreSQL, ``JSON`` on every other dialect."""
        if dialect.name == "postgresql":
            return dialect.type_descriptor(Vector(self.dim))
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    """Declarative base for every table in the relational/vector store."""


class Tenant(Base):
    """An enterprise client — the top of the tenancy hierarchy (§3.3).

    Every governed record (users, budgets, usage, approvals, audit, chunks) hangs
    off a tenant so identity, data and spend can be attributed and isolated.
    """

    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    status: Mapped[TenantStatus] = mapped_column(
        SAEnum(TenantStatus, name="tenant_status"), default=TenantStatus.ACTIVE
    )
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class User(Base):
    """An authenticated principal, its tenant and its RBAC role.

    Extended for production tenancy/auth (§3.3): a nullable ``tenant_id`` FK, plus
    password/activation fields for real (hashed-password) auth. All additions are
    nullable / defaulted so existing ``User(username=..., role=...)`` construction
    and the SQLite test schema are unaffected.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    # The four-valued RBAC role (admin / ai_team / devops / client). Defaults to the
    # least-privileged CLIENT (the successor of the retired coarse "user"). The
    # ``user_role`` Postgres enum is (re)created with all four labels on a fresh
    # ``create_all`` (the lite/SQLite/test path). A LIVE Postgres that already has the
    # old two-label enum needs a one-off migration to widen it, e.g.:
    #     ALTER TYPE user_role ADD VALUE 'ai_team';
    #     ALTER TYPE user_role ADD VALUE 'devops';
    #     ALTER TYPE user_role ADD VALUE 'client';
    # (the old 'user' label is left in place for any historical rows; new rows use
    # 'client'). SQLite/tests recreate the schema, so no migration is needed there.
    role: Mapped[Role] = mapped_column(
        SAEnum(Role, name="user_role"), default=Role.CLIENT
    )
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    email: Mapped[str | None] = mapped_column(String(320), default=None, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255), default=None)
    is_active: Mapped[bool] = mapped_column(default=True)


class Budget(Base):
    """A hierarchical spend/rate cap for a tenant or a user (§3.3).

    Caps are enforced inward at the LiteLLM chokepoint: a request is blocked once
    any level along the tenant→user path is over budget. Enforcement lands in the
    governance phase; this is the frozen storage contract.
    """

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The owning tenant, for tenant-scoped listing/isolation (§3.3). Additive and
    # nullable so existing rows/construction are unaffected; for a tenant-scoped cap
    # this equals ``scope_id``, for a user-scoped cap it is the target user's tenant.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    scope_type: Mapped[BudgetScope] = mapped_column(
        SAEnum(BudgetScope, name="budget_scope"), index=True
    )
    scope_id: Mapped[int] = mapped_column(index=True)
    window: Mapped[BudgetWindow] = mapped_column(
        SAEnum(BudgetWindow, name="budget_window"), default=BudgetWindow.DAY
    )
    token_cap: Mapped[int | None] = mapped_column(default=None)
    usd_cap: Mapped[float | None] = mapped_column(default=None)
    rpm: Mapped[int | None] = mapped_column(default=None)
    tpm: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class UsageLedger(Base):
    """The durable per-call spend record — the persistent form of the in-RAM tally.

    One row per model call at the gateway; the source of truth for per-tenant/user
    cost attribution and the token dashboard (§3.3).
    """

    __tablename__ = "usage_ledger"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), default=None, index=True
    )
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    model: Mapped[str | None] = mapped_column(String(255), default=None)
    prompt_tokens: Mapped[int] = mapped_column(default=0)
    completion_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)


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
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
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
    ml_snapshot: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    assignee_tier: Mapped[str | None] = mapped_column(String(64), default=None)
    sla_deadline: Mapped[datetime | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(String(255), default=None)


class AuditLog(Base):
    """First-class audit record for every autonomous / approved action.

    Every autonomous action, the approving human (if any), the model used and the
    trace id are captured here — this is what makes the system defensible
    (``docs/backend.md`` §6, ``docs/security.md``).
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The owning tenant for cross-tenant isolation of the trail (H2). Additive and
    # nullable so existing rows/construction and the SQLite test schema are
    # unaffected; populated from the governance context when one is in force.
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    action: Mapped[str] = mapped_column(String(255), index=True)
    actor: Mapped[str | None] = mapped_column(String(255), default=None)
    model: Mapped[str | None] = mapped_column(String(255), default=None)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    approved_by: Mapped[str | None] = mapped_column(String(255), default=None)


class Chunk(Base):
    """A retrievable text chunk plus its embedding (reused by the retrieval module).

    The vector column is the shared home for chunk embeddings; the retrieval
    pipeline writes here at ingest time and runs nearest-neighbour search at
    query time.
    """

    __tablename__ = "chunks"

    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[str] = mapped_column(String(255), index=True)
    persona: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    content: Mapped[str] = mapped_column(String())
    embedding: Mapped[list[float]] = mapped_column(VectorType(EMBED_DIM))
    meta: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)


class EvalResult(Base):
    """A single offline-eval measurement (RAGAS metric or LLM-as-judge verdict)."""

    __tablename__ = "eval_results"

    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    run_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    # The prompt_key (persona) the graded run used — the scoping key so Diagnose only
    # clusters a prompt's OWN failures and /ops/evals?prompt_key filters for real.
    prompt_key: Mapped[str | None] = mapped_column(String(128), default=None, index=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    metric: Mapped[str] = mapped_column(String(128), index=True)
    score: Mapped[float] = mapped_column()
    passed: Mapped[bool] = mapped_column(default=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)


class PromptStatus(StrEnum):
    """Lifecycle of a versioned system prompt in the LLM-Ops registry."""

    DRAFT = "draft"        # proposed by the optimizer/human; not live
    STAGED = "staged"      # passed the eval gate; awaiting promotion/approval
    ACTIVE = "active"      # the one live version for its prompt_key (at most one)
    ARCHIVED = "archived"  # a former active version, retained for rollback + audit


class PromptVersion(Base):
    """A versioned system prompt + config — the seam the LLM-Ops loop writes back.

    The harness reads the ACTIVE version for a ``prompt_key`` (via an in-process cache,
    :mod:`app.ops.registry`) and falls back to the adapter default when none exists, so
    the adapter prompt is always the floor. Every version is retained (``archived``),
    making promotion reversible and auditable — the platform never silently mutates its
    own instructions.
    """

    __tablename__ = "prompt_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    tenant_id: Mapped[int | None] = mapped_column(
        ForeignKey("tenants.id"), default=None, index=True
    )
    prompt_key: Mapped[str] = mapped_column(String(128), index=True)
    version: Mapped[int] = mapped_column()
    system_prompt: Mapped[str] = mapped_column(Text())
    config: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    status: Mapped[PromptStatus] = mapped_column(
        SAEnum(PromptStatus, name="prompt_status"), default=PromptStatus.DRAFT, index=True
    )
    parent_version: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[str | None] = mapped_column(String(128), default=None)
    notes: Mapped[str | None] = mapped_column(Text(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now(), index=True)
    activated_at: Mapped[datetime | None] = mapped_column(default=None)

    __table_args__ = (
        Index("ux_prompt_version", "prompt_key", "version", unique=True),
        Index("ix_prompt_key_status", "prompt_key", "status"),
    )
