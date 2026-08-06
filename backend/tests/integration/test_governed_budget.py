"""E2E (a): a governed request over budget → ``budget_exceeded``, no runaway calls.

Spans the whole new governance seam through the real FastAPI app: hashed-password
login → JWT with a ``tenant_id`` → ``_resolve_governance`` reads the tenant's caps →
the streaming task binds the :class:`GovernanceContext` → the plan node's model call hits
the **real** LiteLLM chokepoint (``core.llm.complete``) → enforcement refuses *before*
any model call → the orchestrator surfaces the terminal ``budget_exceeded`` event and
ends the run BLOCKED. A fake ``litellm`` proves zero ``acompletion`` calls were made
(no runaway spend) and the ledger stays empty.
"""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.core.llm as llm_mod
from app.api import routes as api_routes
from app.core.llm import complete as real_complete
from app.core.security import hash_password
from app.data import (
    Budget,
    BudgetScope,
    BudgetWindow,
    Tenant,
    UsageLedger,
    User,
    get_sessionmaker,
)
from app.main import app

pytestmark = pytest.mark.asyncio


async def _seed_over_budget_tenant() -> None:
    """Seed tenant 1 with a user, a day token cap of 100, and 150 tokens already spent."""
    async with get_sessionmaker()() as session:
        session.add_all(
            [
                Tenant(id=1, name="Acme"),
                User(
                    id=11,
                    username="alice",
                    tenant_id=1,
                    password_hash=hash_password("secret"),
                    is_active=True,
                ),
                Budget(
                    scope_type=BudgetScope.TENANT,
                    scope_id=1,
                    window=BudgetWindow.DAY,
                    token_cap=100,
                ),
                UsageLedger(
                    tenant_id=1, user_id=11, prompt_tokens=100, completion_tokens=50
                ),
            ]
        )
        await session.commit()


async def test_governed_query_over_budget_blocks_with_no_model_calls(
    client, db, monkeypatch, make_deps, parse_sse
):
    # A fake gateway that records every acompletion (there must be none).
    calls = {"n": 0}

    class _FakeLiteLLM:
        ssl_verify = None

        async def acompletion(self, **kwargs):  # noqa: ANN003
            calls["n"] += 1
            message = SimpleNamespace(content="should never run", tool_calls=[])
            usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=message)], usage=usage, model="x"
            )

        def completion_cost(self, *, completion_response):  # noqa: ANN001
            return 0.0

    monkeypatch.setitem(sys.modules, "litellm", _FakeLiteLLM())
    monkeypatch.setattr(llm_mod, "_ssl_configured", False)

    await _seed_over_budget_tenant()

    login = await client.post(
        "/auth/login", json={"username": "alice", "password": "secret"}
    )
    assert login.status_code == 200
    assert login.json()["tenant_id"] == 1
    headers = {"Authorization": f"Bearer {login.json()['token']}"}

    # Deps whose planner call delegates to the REAL chokepoint so enforcement fires.
    deps = make_deps(propose_tool=True)

    async def delegating_complete(
        role, messages, *, tools=None, temperature=0.0, response_format=None
    ):  # noqa: ANN001
        return await real_complete(
            role,
            messages,
            tools=tools,
            temperature=temperature,
            response_format=response_format,
        )

    deps.complete = delegating_complete
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: deps

    resp = await client.post(
        "/query", json={"query": "please resolve request R1"}, headers=headers
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["event"] for e in events]

    # Terminal budget block, cleanly (not an error/crash).
    assert "budget_exceeded" in types
    assert types[-1] == "run_finished"
    finished = json.loads(events[-1]["data"])
    assert finished["status"] == "blocked"

    budget = json.loads(next(e for e in events if e["event"] == "budget_exceeded")["data"])
    assert budget["scope"] == "tenant"
    assert budget["limit_type"] == "token_cap"

    # Bounded cost: nothing was executed and NO model call was made (no runaway spend).
    assert "tool_call" not in types
    assert "tool_result" not in types
    assert calls["n"] == 0

    # And no new usage-ledger row was written (enforcement refused before spend).
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(UsageLedger).where(UsageLedger.tenant_id == 1))
        ).scalars().all()
    assert len(rows) == 1  # only the pre-seeded spend row; the blocked run added none
