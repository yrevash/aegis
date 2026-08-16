"""Shared fixtures for the ops suite — a real PostgreSQL store + injected fakes.

Every DB-touching test gets its own clone of the session template database (see
``tests/conftest.py``), reached over the ``NOSUPERUSER NOBYPASSRLS`` role, plus the ops
loop configured (via :func:`aegis.ops.configure_ops`) to draw sessions from it and the
:class:`~tests.ops._approval.FakeApproval` ORM so the gate's inbox read/decide path
round-trips without any host application.

A real cluster is not a formality here: ``test_registry_durability`` opens four
*concurrent* sessions and depends on a genuine unique-constraint collision between them,
which is exactly the kind of behaviour the old single-file test database could not
express.
"""

from __future__ import annotations

import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

# Import the ops ORM so its tables register on AegisBase.metadata before create_all.
import aegis.ops.models  # noqa: F401
from aegis.ops import config, registry

from ._approval import FakeApproval, FakeApprovalStatus

#: The floor prompt the fake host injects (stands in for the adapter/persona baseline).
FAKE_FLOOR = "FLOOR PROMPT"
DEFAULT_PERSONA_ID = "default"

__all__ = [
    "DEFAULT_PERSONA_ID",
    "FAKE_FLOOR",
    "FakeApproval",
    "FakeApprovalStatus",
    "fake_render_floor_prompt",
]


def fake_render_floor_prompt(prompt_key: str) -> str:  # noqa: ARG001 - stable fake floor
    """A deterministic fake floor renderer for tests (no adapter)."""
    return FAKE_FLOOR


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """A private PostgreSQL ops database with the ops loop wired to it.

    Yields the sessionmaker and injects it (plus the fake floor renderer, the fake
    durable-approval writer, and the :class:`FakeApproval` ORM) into :mod:`aegis.ops` via
    :func:`configure_ops`, so ``evaluate_run`` / ``diagnose`` / ``release`` / the gate all
    round-trip against it.

    Args:
        pg_sessionmaker: The unprivileged sessionmaker over this test's scratch database.
    """

    async def fake_enqueue_approval(
        *, approval_id, run_id, thread_id, action, args, risk, rationale, tenant_id=None
    ):  # noqa: ANN001, ANN003
        async with pg_sessionmaker() as session:
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
        session_factory=lambda: pg_sessionmaker(),
        enqueue_approval=fake_enqueue_approval,
        approval_model=FakeApproval,
        approval_status=FakeApprovalStatus,
    )
    config.reset_loop_params()  # historical defaults unless a test opts in
    registry.clear_cache()
    yield pg_sessionmaker
    config.reset_loop_params()
    registry.clear_cache()
