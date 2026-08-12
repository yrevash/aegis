"""Shared fixtures for the ops suite — an offline SQLite store + injected fakes, no host.

Every DB-touching test gets a fresh in-memory-file SQLite database with the
``aegis.data.AegisBase`` schema created (the ops ORM registers on it), a sessionmaker, and
the ops loop configured (via :func:`aegis.ops.configure_ops`) to draw sessions from it —
plus a fake ``Approval`` ORM (also on ``AegisBase``) so the gate's inbox read/decide path
round-trips without any host application.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

import pytest_asyncio
from sqlalchemy import Enum as SAEnum
from sqlalchemy import String, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Mapped, mapped_column

# Import the ops ORM so its tables register on AegisBase.metadata before create_all.
import aegis.ops.models  # noqa: F401
from aegis.core.types import RiskLevel
from aegis.data import AegisBase, JsonB
from aegis.ops import config, registry

#: The floor prompt the fake host injects (stands in for the adapter/persona baseline).
FAKE_FLOOR = "FLOOR PROMPT"
DEFAULT_PERSONA_ID = "default"


def fake_render_floor_prompt(prompt_key: str) -> str:  # noqa: ARG001 - stable fake floor
    """A deterministic fake floor renderer for tests (no adapter)."""
    return FAKE_FLOOR


class FakeApprovalStatus(StrEnum):
    """Minimal approvals lifecycle mirroring the host ``ApprovalStatus``."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class FakeApproval(AegisBase):
    """A test-local durable approvals row (stands in for the host-owned ``Approval``)."""

    __tablename__ = "approvals"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    tenant_id: Mapped[int | None] = mapped_column(default=None, index=True)
    status: Mapped[FakeApprovalStatus] = mapped_column(
        SAEnum(FakeApprovalStatus, name="approval_status"),
        default=FakeApprovalStatus.PENDING,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(255))
    args: Mapped[dict[str, Any]] = mapped_column(JsonB, default=dict)
    risk: Mapped[RiskLevel] = mapped_column(
        SAEnum(RiskLevel, name="approval_risk"), default=RiskLevel.LOW
    )
    rationale: Mapped[str | None] = mapped_column(String(), default=None)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(default=None)
    decided_by: Mapped[str | None] = mapped_column(String(255), default=None)


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    """A dedicated SQLite ops DB with the aegis schema materialised + the ops loop wired.

    Yields the sessionmaker and injects it (plus the fake floor renderer, the fake
    durable-approval writer, and the fake ``Approval`` ORM) into :mod:`aegis.ops` via
    :func:`configure_ops`, so ``evaluate_run`` / ``diagnose`` / ``release`` / the gate all
    round-trip against it.
    """
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(AegisBase.metadata.create_all)
    sessionmaker = async_sessionmaker(engine, expire_on_commit=False)

    async def fake_enqueue_approval(
        *, approval_id, run_id, thread_id, action, args, risk, rationale, tenant_id=None
    ):  # noqa: ANN001, ANN003
        async with sessionmaker() as session:
            session.add(
                FakeApproval(
                    id=approval_id,
                    run_id=run_id,
                    thread_id=thread_id,
                    action=action,
                    args=args,
                    risk=risk,
                    rationale=rationale,
                    tenant_id=tenant_id,
                )
            )
            await session.commit()

    config.configure_ops(
        render_floor_prompt=fake_render_floor_prompt,
        session_factory=lambda: sessionmaker(),
        enqueue_approval=fake_enqueue_approval,
        approval_model=FakeApproval,
        approval_status=FakeApprovalStatus,
    )
    config.reset_loop_params()  # historical defaults unless a test opts in
    registry.clear_cache()
    yield sessionmaker
    config.reset_loop_params()
    registry.clear_cache()
    await engine.dispose()
