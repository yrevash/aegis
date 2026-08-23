"""The ``/ops/*`` reads resolve their tenant from the token, and refuse another's.

Two measured defects, one root: these routes never resolved a scope from the principal.

**They read the platform scope for everybody.** ``app.ops.registry`` defaults its tenant
from the sealed *governance* context, which ``POST /query`` and the chat surfaces bind
and no plain GET does — so ``list_versions`` resolved ``None``, the platform scope, and
matched ``tenant_id IS NULL``. On ``taif_run1``,
``GET /ops/prompts?prompt_key=operations_lead`` returned ``{"rows": []}`` for an analyst,
a tenant admin *and* platform staff, while ``prompt_versions`` held two rows for tenant 1
and ``GET /llmops/prompts`` — which resolves its scope from the principal — reported
``activeVersion: 2`` for the same key. One screen reads both endpoints, so it rendered
"No version of this prompt has been recorded" directly above the list of them.

**They silently substituted a named tenant.** ``/ops/evals`` declared no ``tenant_id``
parameter at all, so FastAPI dropped it: ``?tenant_id=2`` as a tenant-1 analyst returned
200 with tenant 1's rows. Not a leak — but a caller who names a scope and is served a
different one has no way to find out, which is how a screen comes to caption one tenant's
number with another tenant's name. Compare ``_scope_tenant``, which 403s.
"""

from __future__ import annotations

import pgsupport
import pytest

from app.api.schemas import Role
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.data.models import EvalResult
from app.ops import registry

pytestmark = pytest.mark.asyncio

PK = "ops-scope-key"
_MINE, _THEIRS = 9711, 9712
_MY_ADMIN, _STAFF = 97111, 97113


async def _seed() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_MINE, name="Mine"),
            Tenant(id=_THEIRS, name="Theirs"),
            User(id=_MY_ADMIN, username="mine-admin", role=Role.ADMIN, tenant_id=_MINE),
            User(id=_STAFF, username="staff", role=Role.ADMIN, tenant_id=None),
            EvalResult(
                run_id="run-mine",
                prompt_key=PK,
                tenant_id=_MINE,
                metric="answer",
                score=0.9,
                passed=True,
                detail={},
            ),
            EvalResult(
                run_id="run-theirs",
                prompt_key=PK,
                tenant_id=_THEIRS,
                metric="answer",
                score=0.9,
                passed=True,
                detail={},
            ),
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        mine = await registry.create_draft(
            session, prompt_key=PK, system_prompt="TENANT PROMPT", tenant_id=_MINE
        )
        await registry.promote(session, mine.id)
        await session.commit()


def _headers(user_id: int, username: str, role: str, tenant_id: int | None):
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=user_id, username=username, role=role, tenant_id=tenant_id
        )
    }


def _mine_admin():
    return _headers(_MY_ADMIN, "mine-admin", TENANT_ADMIN, _MINE)


def _staff():
    return _headers(_STAFF, "staff", PLATFORM_ADMIN, None)


async def test_ops_prompts_lists_the_callers_own_tenants_versions(db, client):
    """The defect: this returned ``[]`` while the tenant had an active version."""
    registry.clear_cache()
    await _seed()

    resp = await client.get("/ops/prompts", params={"prompt_key": PK}, headers=_mine_admin())
    assert resp.status_code == 200, resp.text
    rows = resp.json()["rows"]
    assert [r["status"] for r in rows] == ["active"], rows

    active = await client.get(
        "/ops/prompts/active", params={"prompt_key": PK}, headers=_mine_admin()
    )
    assert active.status_code == 200
    assert active.json()["system_prompt"] == "TENANT PROMPT"

    # Platform staff reach the same rows the way they reach every other tenant-scoped
    # read: by naming the tenant. Their unqualified read is the PLATFORM scope, which is
    # a real scope with its own rows — not a wildcard — and is what `/llmops/prompts`
    # answers for them too, which is the agreement this fix is for.
    named = await client.get(
        "/ops/prompts", params={"prompt_key": PK, "tenant_id": _MINE}, headers=_staff()
    )
    assert [r["status"] for r in named.json()["rows"]] == ["active"]
    unqualified = await client.get(
        "/ops/prompts", params={"prompt_key": PK}, headers=_staff()
    )
    assert unqualified.json()["rows"] == []
    registry.clear_cache()


async def test_naming_another_tenant_is_refused_not_quietly_substituted(db, client):
    registry.clear_cache()
    await _seed()

    for path, params in (
        ("/ops/evals", {"tenant_id": _THEIRS}),
        ("/ops/prompts", {"prompt_key": PK, "tenant_id": _THEIRS}),
        ("/ops/prompts/active", {"prompt_key": PK, "tenant_id": _THEIRS}),
    ):
        resp = await client.get(path, params=params, headers=_mine_admin())
        assert resp.status_code == 403, (path, resp.status_code, resp.text)
        assert "Cross-tenant" in resp.json()["detail"]

    # Naming your OWN tenant is honoured rather than refused: the parameter is real.
    own = await client.get(
        "/ops/evals", params={"tenant_id": _MINE, "limit": 50}, headers=_mine_admin()
    )
    assert own.status_code == 200
    ids = {r["run_id"] for r in own.json()["rows"]}
    assert "run-mine" in ids and "run-theirs" not in ids
    registry.clear_cache()
