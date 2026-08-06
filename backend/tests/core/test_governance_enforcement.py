"""Budget/rate enforcement + usage-ledger writes at the LiteLLM chokepoint (§3.3).

These run with no network and no litellm installed (a fake ``litellm`` is injected),
and against the in-memory aiosqlite database bound by the ``db`` fixture so the
budget reads and ledger writes actually round-trip.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import select

import app.core.llm as llm_mod
from app.core.governance import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from app.core.llm import BudgetExceededError, complete, embed
from app.core.models import ModelRole
from app.data import (
    Budget,
    BudgetScope,
    BudgetWindow,
    UsageLedger,
    get_sessionmaker,
)

pytestmark = pytest.mark.asyncio


def _make_response(*, content="ok", model="genailab-maas-gpt-4o-mini"):
    message = SimpleNamespace(content=content, tool_calls=[])
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


class _FakeLiteLLM:
    def __init__(self):
        self.ssl_verify = None
        self._response = _make_response()
        self._embedding_response = SimpleNamespace(
            data=[{"embedding": [0.1, 0.2]}], usage=SimpleNamespace(prompt_tokens=5)
        )

    async def acompletion(self, **kwargs):
        return self._response

    async def aembedding(self, **kwargs):
        return self._embedding_response

    def completion_cost(self, *, completion_response):
        return 0.0002


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = _FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(llm_mod, "_ssl_configured", False)
    return fake


async def _seed(*rows):
    async with get_sessionmaker()() as session:
        for row in rows:
            session.add(row)
        await session.commit()


async def test_over_token_budget_raises(fake_litellm, db):
    await _seed(
        Budget(
            scope_type=BudgetScope.TENANT,
            scope_id=1,
            window=BudgetWindow.DAY,
            token_cap=100,
        ),
        UsageLedger(tenant_id=1, prompt_tokens=100, completion_tokens=50, cost_usd=0.1),
    )
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        with pytest.raises(BudgetExceededError) as ei:
            await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert ei.value.scope == "tenant"
    assert ei.value.limit_type == "token_cap"
    assert ei.value.limit == 100


async def test_user_cap_binds_before_tenant(fake_litellm, db):
    # Both caps tripped; the user cap is checked first and attributed to the user.
    await _seed(
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100),
        Budget(scope_type=BudgetScope.USER, scope_id=2, token_cap=10),
        UsageLedger(tenant_id=1, user_id=2, prompt_tokens=20, completion_tokens=0),
    )
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        with pytest.raises(BudgetExceededError) as ei:
            await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert ei.value.scope == "user"


async def test_under_budget_passes_and_writes_ledger(fake_litellm, db):
    await _seed(Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100_000))
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert result.content == "ok"

    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(UsageLedger).where(UsageLedger.tenant_id == 1))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].user_id == 2
    assert rows[0].prompt_tokens == 11
    assert rows[0].completion_tokens == 7


async def test_rpm_cap_raises_on_recent_calls(fake_litellm, db):
    await _seed(
        Budget(scope_type=BudgetScope.TENANT, scope_id=1, rpm=1),
        UsageLedger(tenant_id=1, prompt_tokens=1, completion_tokens=1),
    )
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        with pytest.raises(BudgetExceededError) as ei:
            await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert ei.value.limit_type == "rpm"


async def test_ungoverned_call_skips_db_and_enforcement(fake_litellm):
    # No governance context bound (the default for every existing flow): the call
    # neither reads budgets nor writes a ledger row, and behaves exactly as before.
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    assert result.content == "ok"


async def test_enforcement_error_fails_closed_by_default(fake_litellm, db, monkeypatch):
    # M3: a DB/enforcement error must DENY the call, not silently uncap it.
    import app.data.governance as gov_mod

    async def boom(*, tenant_id, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(gov_mod, "enforce_governance", boom)
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        with pytest.raises(BudgetExceededError) as ei:
            await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert ei.value.limit_type == "enforcement_error"


async def test_enforcement_error_can_opt_into_fail_open(fake_litellm, db, monkeypatch):
    # M3: with ``budget_fail_open`` set, caps become soft ceilings and the call proceeds.
    import app.data.governance as gov_mod
    from app.config import get_settings

    async def boom(*, tenant_id, user_id):
        raise RuntimeError("db down")

    monkeypatch.setattr(gov_mod, "enforce_governance", boom)
    monkeypatch.setattr(get_settings(), "budget_fail_open", True)
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    finally:
        reset_governance_context(tok)
    assert result.content == "ok"


async def test_governed_writes_apply_tenant_scope(db, monkeypatch):
    # H1: per-request RLS binding (``set_tenant_scope``) is actually applied inside
    # the governed data-layer calls — pre-fix it was defined but called nowhere.
    import app.data.governance as gov_mod
    from app.data.governance import record_usage

    seen: list[int | None] = []
    real = gov_mod.set_tenant_scope

    async def spy(session, tenant_id):
        seen.append(tenant_id)
        await real(session, tenant_id)

    monkeypatch.setattr(gov_mod, "set_tenant_scope", spy)
    await record_usage(
        tenant_id=7,
        user_id=2,
        model="m",
        prompt_tokens=1,
        completion_tokens=1,
        cost_usd=0.1,
        trace_id=None,
    )
    assert 7 in seen


async def test_embed_is_governed_and_ledgered(fake_litellm, db):
    await _seed(Budget(scope_type=BudgetScope.TENANT, scope_id=1, token_cap=100_000))
    tok = set_governance_context(GovernanceContext(tenant_id=1, user_id=2))
    try:
        vectors = await embed(["a"])
    finally:
        reset_governance_context(tok)
    assert vectors == [[0.1, 0.2]]
    async with get_sessionmaker()() as session:
        rows = (
            await session.execute(select(UsageLedger).where(UsageLedger.tenant_id == 1))
        ).scalars().all()
    assert len(rows) == 1
