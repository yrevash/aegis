"""Tests for the injected governance hook contract (enforce before spend, record after)."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import BudgetExceededError, complete, embed

from .test_llm import FakeLiteLLM, _make_response


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


class _RefusingGovernance:
    """A governance hook that always refuses — proves enforce() runs BEFORE spend."""

    def __init__(self):
        self.enforce_calls = 0
        self.record_calls = []

    def get_context(self):
        return SimpleNamespace(tenant_id=1, user_id=2)

    async def enforce(self, ctx):
        self.enforce_calls += 1
        raise BudgetExceededError(
            scope="tenant", scope_id=1, limit_type="token_cap", limit=100, used=200
        )

    async def record(self, ctx, **kwargs):
        self.record_calls.append(kwargs)


class _RecordingGovernance:
    """A governance hook that allows the call and records the resulting spend."""

    def __init__(self):
        self.record_calls: list[dict] = []

    def get_context(self):
        return SimpleNamespace(tenant_id=1, user_id=2)

    async def enforce(self, ctx):
        return None

    async def record(self, ctx, **kwargs):
        self.record_calls.append(kwargs)


class _NoneContextGovernance:
    """A governance hook that reports "ungoverned" — must be a full no-op."""

    def get_context(self):
        return None

    async def enforce(self, ctx):  # pragma: no cover - never called
        raise AssertionError("enforce() must not be called when get_context() is None")

    async def record(self, ctx, **kwargs):  # pragma: no cover - never called
        raise AssertionError("record() must not be called when get_context() is None")


async def test_enforce_raises_before_any_model_call(fake_litellm, monkeypatch):
    gov = _RefusingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    with pytest.raises(BudgetExceededError) as ei:
        await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    assert ei.value.scope == "tenant"
    assert ei.value.limit_type == "token_cap"
    assert gov.enforce_calls == 1
    assert fake_litellm.calls == []  # no spend happened
    assert gov.record_calls == []  # nothing was ledgered either


async def test_record_called_after_a_successful_call(fake_litellm, monkeypatch):
    fake_litellm._response = _make_response(content="ok")
    gov = _RecordingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    assert result.content == "ok"
    assert len(gov.record_calls) == 1
    rec = gov.record_calls[0]
    assert rec["model"] == "genailab-maas-gpt-4o"
    assert rec["prompt_tokens"] == 11
    assert rec["completion_tokens"] == 7
    assert rec["cost_usd"] == pytest.approx(0.0042)


async def test_embed_is_also_governed(fake_litellm, monkeypatch):
    fake_litellm._embedding_response = SimpleNamespace(
        data=[{"embedding": [0.1, 0.2]}], usage=SimpleNamespace(prompt_tokens=5)
    )
    gov = _RecordingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    vectors = await embed(["a"])

    assert vectors == [[0.1, 0.2]]
    assert len(gov.record_calls) == 1


async def test_ungoverned_context_is_a_full_no_op(fake_litellm):
    fake_litellm._response = _make_response(content="ok")
    # The default (autouse) fixture already wires _NoOpGovernance, whose
    # get_context() returns None — complete() must not touch enforce/record.
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    assert result.content == "ok"


async def test_none_context_governance_hook_is_never_enforced(fake_litellm, monkeypatch):
    fake_litellm._response = _make_response(content="ok")
    monkeypatch.setattr(llm_mod, "_governance", _NoneContextGovernance())

    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    assert result.content == "ok"


async def test_record_write_failure_is_swallowed_not_raised(fake_litellm, monkeypatch):
    """A ledger write is best-effort: a failure must not fail the (already
    successful) model call."""
    fake_litellm._response = _make_response(content="ok")

    class _BrokenLedger(_RecordingGovernance):
        async def record(self, ctx, **kwargs):
            raise RuntimeError("ledger db is down")

    monkeypatch.setattr(llm_mod, "_governance", _BrokenLedger())

    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    assert result.content == "ok"  # the call still succeeds
