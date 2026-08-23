"""``GET /agent/checkpoints/{run_id}`` — tenant scope, and what it refuses to send.

Two properties carry this endpoint, and each has a failure mode worth pinning:

1. **A caller may only read its own tenant's run.** The endpoint reads LangGraph's
   checkpoint tables, which have no ``tenant_id`` column and therefore no
   ``tenant_isolation`` policy to fall back on — and this deployment's RLS posture is
   fail-**open** for an unbound scope anyway. The app-level filter on the ``runs``
   header is the whole of the isolation, so a run belonging to another tenant must
   answer 404 (the same answer as a run that does not exist, so an id cannot be probed).

2. **It never sends the checkpointed state.** A checkpoint holds the query, the
   retrieved passages, the tool arguments and the draft answer. The response model is
   the redaction, so the fields it declares are asserted by name — a future field that
   carried a state payload would have to break this test to ship.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.runs.models import Run

from app.api.routes_checkpoints import CheckpointHistoryResponse, CheckpointRow
from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio


def _headers(role: str, *, tenant_id: int | None = None) -> dict[str, str]:
    token = create_access_token(
        user_id=None, username="ckpt", role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants_one_run_each() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Tenant A"),
            Tenant(id=2, name="Tenant B"),
            User(id=11, username="a-user", role=Role.CLIENT, tenant_id=1),
            User(id=22, username="b-user", role=Role.CLIENT, tenant_id=2),
            Run(run_id="run-a", tenant_id=1),
            Run(run_id="run-b", tenant_id=2),
        )
        await session.commit()


async def test_another_tenants_run_is_indistinguishable_from_one_that_does_not_exist(
    client, db
):
    await _seed_two_tenants_one_run_each()
    a_admin = _headers(TENANT_ADMIN, tenant_id=1)

    other = await client.get("/agent/checkpoints/run-b", headers=a_admin)
    missing = await client.get("/agent/checkpoints/run-nope", headers=a_admin)

    assert other.status_code == 404
    assert missing.status_code == 404
    # Same body, so the 404 cannot be read as "this id exists but is not yours".
    assert other.json() == missing.json()


async def test_own_run_is_served_and_platform_staff_may_read_any(client, db):
    await _seed_two_tenants_one_run_each()

    own = await client.get(
        "/agent/checkpoints/run-a", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert own.status_code == 200
    body = own.json()
    assert body["run_id"] == "run-a"
    # The tests run on the default in-memory saver and this run never executed, so the
    # honest answer is an empty chain — never a fabricated one.
    assert body["checkpoints"] == []
    assert body["durable"] is False
    assert body["interrupted_at"] is None

    for run_id in ("run-a", "run-b"):
        served = await client.get(
            f"/agent/checkpoints/{run_id}", headers=_headers(PLATFORM_ADMIN)
        )
        assert served.status_code == 200


async def test_the_projection_carries_no_checkpointed_state():
    """The redaction is the response model; a state-bearing field must break this."""
    assert set(CheckpointRow.model_fields) == {
        "checkpoint_id",
        "parent_checkpoint_id",
        "step",
        "source",
        "created_at",
        "produced_by",
        "next_nodes",
        "interrupted",
    }
    assert set(CheckpointHistoryResponse.model_fields) == {
        "run_id",
        "store",
        "durable",
        "checkpoints",
        "entries",
        "interrupted_at",
        "resumed_from",
        "truncated",
    }
