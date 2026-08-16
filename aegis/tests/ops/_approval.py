"""The ops suite's stand-in for the host-owned ``Approval`` ORM.

``aegis.ops`` never owns the approvals inbox — the host does (``app.data.models`` in the
backend), and the gate reaches it through the ``approval_model``/``approval_status`` seam
injected by :func:`aegis.ops.configure_ops`. The suite therefore needs *an* approvals
model to inject, and it has to live on ``AegisBase.metadata`` so the test template
database materialises it.

**Why the table is not called ``approvals``.** It used to be. That was harmless while
every test ran on its own throwaway SQLite file, and became a real hazard the moment the
suite moved to one shared PostgreSQL template: a fake whose columns differ from the
host's real ``approvals`` table would shadow it on any database that carries both, and
the two ``Enum`` columns would additionally claim the cluster-visible type names
``approval_status`` and ``approval_risk``. Renaming is the honest fix — the gate builds
its queries from the injected class, so the table name is not part of what these tests
assert, and a name that cannot collide is strictly better than one that can.

The consequence, stated rather than hidden: this table is not in
:data:`aegis.governance.rls._TENANT_SCOPED_TABLES`, so it gets no tenant policy here.
Live RLS on the *real* ``approvals`` table is proved by
``backend/tests/integration/test_tenant_isolation_live.py``, which runs against the
host's own schema — the only place that claim can honestly be made.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from aegis.core.types import RiskLevel
from aegis.data import AegisBase, JsonB

__all__ = ["FakeApproval", "FakeApprovalStatus"]


class FakeApprovalStatus(StrEnum):
    """Minimal approvals lifecycle mirroring the host ``ApprovalStatus``."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FakeApproval(AegisBase):
    """A test-local durable approvals row (stands in for the host-owned ``Approval``)."""

    __tablename__ = "ops_test_approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    status: Mapped[FakeApprovalStatus] = mapped_column(
        SAEnum(FakeApprovalStatus, name="ops_test_approval_status"),
        default=FakeApprovalStatus.PENDING,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(255))
    args: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    risk: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, name="ops_test_approval_risk"), default=RiskLevel.LOW
    )
    rationale: Mapped[str | None] = mapped_column(String(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(String(255), default=None)
