"""Data layer: async SQLAlchemy over Postgres (relational + JSON embeddings-of-record).

Public surface (per the shared contract in ``docs/module/MODULE_REFERENCE.md``):

- :func:`get_session` — FastAPI-style async session dependency.
- :func:`record_audit` — write one row to the first-class audit log.
- :func:`bootstrap` — create all tables (vector ANN search lives in Qdrant, not pgvector).

Also exported: the ORM models (:class:`User`, :class:`AuditLog`, :class:`Chunk`,
:class:`EvalResult`) and engine helpers for wiring/tests.
"""

from __future__ import annotations

from .approvals import (
    ApprovalResolution,
    SweepAction,
    count_approved,
    enqueue_approval,
    finalize_resumed,
    get_approval,
    list_pending,
    resolve_approval,
    run_sla_sweeper,
    sweep_expired,
)
from .audit import list_recent_audit, record_audit
from .governance import (
    DuplicateTenantError,
    DuplicateUserError,
    LastPlatformAdminError,
    create_tenant,
    create_user,
    effective_limits,
    enforce_governance,
    list_budgets,
    list_tenants,
    list_users,
    record_usage,
    update_user_role,
    upsert_budget,
    usage_rollup,
    user_tenant_id,
)
from .models import (
    EMBED_DIM,
    Approval,
    ApprovalStatus,
    AuditLog,
    Base,
    Budget,
    BudgetScope,
    BudgetWindow,
    Chunk,
    EvalResult,
    Tenant,
    TenantStatus,
    UsageLedger,
    User,
    VectorColumn,
)
from .session import (
    bootstrap,
    bootstrap_rls,
    configure_engine,
    get_engine,
    get_session,
    get_sessionmaker,
    set_tenant_scope,
    to_asyncpg_dsn,
)

__all__ = [
    "EMBED_DIM",
    "Approval",
    "ApprovalResolution",
    "ApprovalStatus",
    "AuditLog",
    "Base",
    "Budget",
    "BudgetScope",
    "BudgetWindow",
    "Chunk",
    "EvalResult",
    "LastPlatformAdminError",
    "SweepAction",
    "Tenant",
    "TenantStatus",
    "UsageLedger",
    "User",
    "VectorColumn",
    "bootstrap",
    "bootstrap_rls",
    "configure_engine",
    "count_approved",
    "effective_limits",
    "enforce_governance",
    "enqueue_approval",
    "finalize_resumed",
    "get_approval",
    "get_engine",
    "get_session",
    "get_sessionmaker",
    "list_budgets",
    "list_pending",
    "list_recent_audit",
    "DuplicateTenantError",
    "DuplicateUserError",
    "create_tenant",
    "create_user",
    "list_tenants",
    "list_users",
    "record_audit",
    "record_usage",
    "resolve_approval",
    "run_sla_sweeper",
    "set_tenant_scope",
    "sweep_expired",
    "to_asyncpg_dsn",
    "update_user_role",
    "upsert_budget",
    "usage_rollup",
    "user_tenant_id",
]
