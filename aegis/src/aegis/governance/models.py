"""SQLAlchemy ORM for the multi-tenant governance core (tenancy / budgets / audit).

These register on the shared :class:`aegis.data.AegisBase` metadata, so a host's
``AegisBase.metadata.create_all`` materialises them — on PostgreSQL with native
``jsonb`` columns, and on the SQLite test database via the cross-dialect ``JsonB``
decorator (JSON fallback). The intra-package foreign keys (``users.tenant_id`` →
``tenants.id``, ``usage_ledger.user_id`` → ``users.id``, …) resolve within this one
package's tables.

Columns are preserved exactly from the pre-extraction platform schema: the nullable
``tenant_id`` on every governed record, the four-valued ``user_role`` SAEnum, the
additive/defaulted auth columns on ``users``, and the naive-UTC ``ts`` on the ledger and
audit rows.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from aegis.data import AegisBase, JsonB
from aegis.governance.types import Role

__all__ = [
    "AuditLog",
    "Budget",
    "BudgetScope",
    "BudgetWindow",
    "Role",
    "Tenant",
    "TenantStatus",
    "UsageLedger",
    "User",
]


# ─────────────────────────────────────────────────────────────────────────────
# Governance / tenancy enums (data-layer contracts)
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


# ─────────────────────────────────────────────────────────────────────────────
# Tenancy / RBAC / budgets / ledger / audit tables
# ─────────────────────────────────────────────────────────────────────────────


class Tenant(AegisBase):
    """An enterprise client — the top of the tenancy hierarchy.

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


class User(AegisBase):
    """An authenticated principal, its tenant and its RBAC role.

    A nullable ``tenant_id`` FK plus password/activation fields for real
    (hashed-password) auth. All additions are nullable / defaulted so existing
    ``User(username=..., role=...)`` construction and the SQLite test schema are
    unaffected.
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


class Budget(AegisBase):
    """A hierarchical spend/rate cap for a tenant or a user.

    Caps are enforced inward at the gateway chokepoint: a request is blocked once any
    level along the tenant→user path is over budget.
    """

    __tablename__ = "budgets"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The owning tenant, for tenant-scoped listing/isolation. Additive and nullable so
    # existing rows/construction are unaffected; for a tenant-scoped cap this equals
    # ``scope_id``, for a user-scoped cap it is the target user's tenant.
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


class UsageLedger(AegisBase):
    """The durable per-call spend record — the persistent form of the in-RAM tally.

    One row per model call at the gateway; the source of truth for per-tenant/user
    cost attribution and the token dashboard.

    Not every model bills per token, so tokens alone cannot describe a call: a
    Whisper transcription is billed per minute of audio and a vision call may be
    billed per image. ``audio_seconds`` / ``images`` record those units, so a
    non-chat call is a real, attributable ledger row instead of
    ``prompt_tokens=0`` → ``$0.00``. Both are additive and defaulted, so existing
    rows, existing construction and the SQLite test schema are unaffected.

    A LIVE Postgres created before these columns existed does **not** grow them from
    ``create_all`` (which is CREATE TABLE IF NOT EXISTS and never alters a table), and
    until it does, every ledger INSERT raises ``UndefinedColumn`` — swallowed, because
    usage recording is best-effort at the gateway, so the row is simply lost and the
    USD caps computed from these rows stop binding. That is why
    :func:`aegis.governance.schema.reconcile_additive_columns` runs at host bootstrap
    and installs any such missing column: additive, idempotent, logged, and fatal if
    the drift cannot be reconciled. There is no manual ALTER TABLE to remember.
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
    #: Seconds of audio billed on this call (per-minute-billed voice models).
    audio_seconds: Mapped[float] = mapped_column(default=0.0, server_default="0")
    #: Images billed or sent as input on this call (vision/multimodal models).
    images: Mapped[int] = mapped_column(default=0, server_default="0")
    cost_usd: Mapped[float] = mapped_column(default=0.0)
    trace_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    #: The agent run this call was made for, or ``NULL`` for a call that belongs to no
    #: run. **This column is the spend↔run attribution, and ``trace_id`` never was.**
    #:
    #: Measured on ``taif_run1`` before it existed: 173 of tenant 1's 1932 ledger rows
    #: (8.95%, $0.104562) carried a trace matching no ``runs`` row, and all 95 runs that
    #: *did* match disagreed with the ledger sum for their trace — so ``runs.cost_usd``
    #: could not sum to total spend by construction, while both figures were labelled
    #: "cost_usd" on two analytics views.
    #:
    #: Three properties this column is required to hold:
    #:
    #: * **NULL means "not attributable to a run", never "zero" and never "unknown".**
    #:   A job, an ingest pass, the chat endpoint and a platform probe all spend real
    #:   money outside any run; ``analytics_spend_daily`` reports that bucket in its own
    #:   named column so it cannot be silently folded into a run's cost.
    #: * **No foreign key to ``runs.run_id``, deliberately.** The ledger row is written
    #:   at the gateway *during* the run; the ``runs`` header is written by a background
    #:   task *after* ``run_finished``. An FK would make every in-run ledger INSERT fail
    #:   its constraint — swallowed, because the ledger write is best-effort — and take
    #:   the USD caps with it. The join is by value, checked by the reconciliation query,
    #:   not enforced by the database.
    #: * **Indexed**, because the one question asked of it — "what did run X spend?" —
    #:   is a lookup by this column over a table that is the largest in the schema.
    run_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)


class AuditLog(AegisBase):
    """First-class audit record for every autonomous / approved action.

    Every autonomous action, the approving human (if any), the model used and the
    trace id are captured here — this is what makes the system defensible.
    """

    __tablename__ = "audit_log"

    # (tenant_id, ts DESC) is the driving predicate of every filtered read: GET /audit
    # always ANDs the caller's sealed tenant scope and orders newest-first, so a
    # standalone index on ``ts`` makes the database sort the whole trail before
    # discarding the other tenants' rows. Declared here, so it is created with the
    # table; ``reconcile_additive_columns`` deliberately does not install indexes on
    # pre-existing columns, so a database that predates this line needs the index
    # created once by hand.
    __table_args__ = (Index("ix_audit_log_tenant_ts", "tenant_id", text("ts DESC")),)

    id: Mapped[int] = mapped_column(primary_key=True)
    # The owning tenant for cross-tenant isolation of the trail. Additive and nullable
    # so existing rows/construction and the SQLite test schema are unaffected; populated
    # from the governance context when one is in force.
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
