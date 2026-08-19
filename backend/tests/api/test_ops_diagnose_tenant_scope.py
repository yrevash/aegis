"""``POST /ops/diagnose`` runs in the caller's sealed tenant scope.

``release`` and ``gate`` already carried a tenant, because the draft row and the approval
row each hold one. ``diagnose`` starts from a ``prompt_key`` and nothing else, so the
only place its tenant can come from is the **principal** — and it must come from the
sealed scope, never from the request body (§7.16 row 12; the body cannot carry one, and
``extra="forbid"`` is what keeps it that way).

The end-to-end shape: a tenant admin's diagnose improves the prompt their runs were
served, from their own failures, and the draft lands in their tenant.
"""

from __future__ import annotations

import json

import pgsupport
import pytest
from aegis.ops.models import EvalResult, PromptVersion

from app.api.schemas import Role
from app.core.llm import LLMResult, Usage
from app.core.models import ModelRole
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.ops import registry

pytestmark = pytest.mark.asyncio

PK = "diagnose-scope-key"
_TENANT = 9611
_ADMIN = 96111


def _optimizer(seen: list[str]):
    """A fake gateway recording the prompt the optimizer was shown (never a real call)."""

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001, ARG001
        seen.append(messages[-1]["content"])
        return LLMResult(
            content=json.dumps({"system_prompt": "IMPROVED", "rationale": "r"}),
            usage=Usage(),
        )

    assert ModelRole.REASONING is not None  # the role the optimizer is called with
    return complete


async def _seed() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Diagnose tenant"),
            User(id=_ADMIN, username="diag-admin", role=Role.ADMIN, tenant_id=_TENANT),
            EvalResult(
                run_id="r-mine",
                prompt_key=PK,
                tenant_id=_TENANT,
                metric="answer",
                score=0.2,
                passed=False,
                detail={"critique": "MY-CASE"},
            ),
            EvalResult(
                run_id="r-platform",
                prompt_key=PK,
                tenant_id=None,
                metric="answer",
                score=0.2,
                passed=False,
                detail={"critique": "PLATFORM-CASE"},
            ),
        )
        await session.commit()
    async with get_sessionmaker()() as session:
        platform = await registry.create_draft(
            session, prompt_key=PK, system_prompt="PLATFORM BASE", tenant_id=None
        )
        await registry.promote(session, platform.id)
        mine = await registry.create_draft(
            session, prompt_key=PK, system_prompt="TENANT BASE", tenant_id=_TENANT
        )
        await registry.promote(session, mine.id)
        await session.commit()


def _admin_headers() -> dict[str, str]:
    return {
        "Authorization": "Bearer "
        + create_access_token(
            user_id=_ADMIN, username="diag-admin", role=TENANT_ADMIN, tenant_id=_TENANT
        )
    }


async def test_diagnose_uses_the_callers_tenant_not_the_platform(db, client, monkeypatch):
    registry.clear_cache()
    await _seed()
    seen: list[str] = []
    monkeypatch.setattr("app.core.llm.complete", _optimizer(seen))

    resp = await client.post(
        "/ops/diagnose", json={"prompt_key": PK}, headers=_admin_headers()
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["failures_considered"] == 1, "the platform's failing row is not this tenant's"
    assert "TENANT BASE" in seen[0]
    assert "PLATFORM BASE" not in seen[0]
    assert "MY-CASE" in seen[0] and "PLATFORM-CASE" not in seen[0]

    async with get_sessionmaker()() as session:
        draft = await session.get(PromptVersion, body["draft_version_id"])
        assert draft.tenant_id == _TENANT
    registry.clear_cache()


async def test_a_tenant_over_its_cap_cannot_run_a_diagnose_pass(db, client, monkeypatch):
    """The optimizer spends, so the optimizer is admission-controlled (tasks 9.2/9.6).

    ``/ops/diagnose`` drove live ``complete`` calls with **no governance context bound at
    all**: not capped, and not written to ``usage_ledger`` either, which made the LLM-Ops
    loop the one paid surface that could not appear on a usage rollup. The route now
    binds the sealed tenant it already runs under, and refuses up front with the same
    ``X-Admission-Gate: budget`` 429 the job substrate and the red-team route use.

    ``seen == []`` is the assertion that matters: a route that called the model and then
    noticed the cap would satisfy the status code and not the point of having one.
    """
    from aegis.governance import Budget, BudgetScope, BudgetWindow, UsageLedger

    registry.clear_cache()
    await _seed()
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Budget(
                tenant_id=_TENANT,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT,
                window=BudgetWindow.DAY,
                usd_cap=0.01,
            ),
            UsageLedger(tenant_id=_TENANT, cost_usd=5.0),
        )
        await session.commit()

    seen: list[str] = []
    monkeypatch.setattr("app.core.llm.complete", _optimizer(seen))

    resp = await client.post(
        "/ops/diagnose", json={"prompt_key": PK}, headers=_admin_headers()
    )

    assert resp.status_code == 429, resp.text
    assert resp.headers["X-Admission-Gate"] == "budget"
    assert seen == [], "an over-budget tenant reached the optimizer"
    registry.clear_cache()


async def test_the_body_cannot_name_a_tenant(db, client):
    """The isolation key is never client-supplied — the body has no field for it."""
    await _seed()

    resp = await client.post(
        "/ops/diagnose",
        json={"prompt_key": PK, "tenant_id": 1},
        headers=_admin_headers(),
    )

    assert resp.status_code == 422
