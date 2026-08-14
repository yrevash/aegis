"""``embed`` must price and tally its calls exactly like ``complete`` does.

Two verified bugs lived here: ``embed`` passed ``_safe_cost(...)`` straight
through with no ``_estimate_cost`` fallback, and never called ``record_call``.
The configured ``genailab-maas-text-embedding-3-large`` is not in LiteLLM's cost
map, so EVERY embedding row was ``$0.00`` — embeddings never counted against a
USD cap — and they were invisible to ``usage_tally`` on top.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import embed
from aegis.gateway.routing import _rate_for

from .test_llm import FakeLiteLLM


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM(
        embedding_response=SimpleNamespace(
            data=[{"embedding": [0.1, 0.2]}], usage=SimpleNamespace(prompt_tokens=4000)
        )
    )
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


class _RecordingGovernance:
    def __init__(self):
        self.record_calls: list[dict] = []

    def get_context(self):
        return SimpleNamespace(tenant_id=1, user_id=2)

    async def enforce(self, ctx):
        return None

    async def record(self, ctx, **kwargs):
        self.record_calls.append(kwargs)


async def test_embedding_cost_falls_back_to_the_token_estimate(fake_litellm):
    """The embedding deployment is unmapped, so the estimate is the real price."""
    await embed(["a"])

    expected = 4 * _rate_for(ModelRole.EMBEDDING)[0]  # 4k prompt tokens
    assert llm_mod.usage_tally()["total_cost_usd"] == pytest.approx(expected)
    assert llm_mod.usage_tally()["total_cost_usd"] > 0.0


async def test_embeddings_are_ledgered_with_a_non_zero_cost(fake_litellm, monkeypatch):
    """A USD cap can only bite if the ledger row is not $0.00."""
    gov = _RecordingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    await embed(["a", "b"])

    assert len(gov.record_calls) == 1
    rec = gov.record_calls[0]
    assert rec["model"] == "genailab-maas-text-embedding-3-large"
    assert rec["prompt_tokens"] == 4000
    assert rec["cost_usd"] > 0.0


async def test_embeddings_are_visible_to_the_usage_tally(fake_litellm):
    await embed(["a"])

    tally = llm_mod.usage_tally()
    assert tally["total_calls"] == 1
    assert llm_mod.optimization_summary()["by_role"]["embedding"]["calls"] == 1
    assert llm_mod.optimization_summary()["by_role"]["embedding"]["prompt_tokens"] == 4000


async def test_embeddings_do_not_move_the_small_model_share(fake_litellm):
    """There is one embedding deployment; routing never chose it."""
    llm_mod.record_call("genailab-maas-gpt-4o-mini", 0.0002, role=ModelRole.CHEAP)
    await embed(["a"])

    assert llm_mod.usage_tally()["small_model_share"] == 1.0


async def test_a_provider_priced_embedding_uses_the_provider_price(
    fake_litellm, monkeypatch
):
    """When the cost map DOES know the model, its number wins over the estimate."""
    monkeypatch.setattr(fake_litellm, "completion_cost", lambda *, completion_response: 0.5)

    await embed(["a"])

    assert llm_mod.usage_tally()["total_cost_usd"] == pytest.approx(0.5)


async def test_per_role_costs_still_sum_to_the_total_with_embeddings(fake_litellm):
    llm_mod.record_call(
        "genailab-maas-gpt-4o", 0.01, prompt_tokens=100, role=ModelRole.GENERATION
    )
    await embed(["a"])

    summary = llm_mod.optimization_summary()
    per_role = sum(r["cost_usd"] for r in summary["by_role"].values())
    assert per_role == pytest.approx(summary["total_cost_usd"])
