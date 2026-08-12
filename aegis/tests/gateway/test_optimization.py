"""Token-optimization surface: routing, the savings summary, and the effective
config knobs — all measured from real per-call data, one source of truth.

These run with no network and no litellm (the fake is injected only where a real
``complete`` call is exercised); the autouse ``_reset_gateway_state`` fixture in
``conftest.py`` gives every test a fresh tally and cleared overrides.
"""

from __future__ import annotations

import sys

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway import optimization_config, optimization_summary, usage_tally
from aegis.gateway.llm import complete, configure
from aegis.gateway.routing import baseline_role, model_for, routing_table

from .conftest import FakeGatewayConfig
from .test_llm import FakeLiteLLM, _make_response


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM(response=_make_response(content="x"))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


# ── Routing picks the right model per role ──────────────────────────────────


def test_routing_table_maps_each_role_to_its_default_deployment():
    table = routing_table()
    assert table["cheap"] == "genailab-maas-gpt-4o-mini"
    assert table["reasoning"] == "genailab-maas-Phi-4-reasoning"
    assert table["generation"] == "genailab-maas-gpt-4o"


def test_model_for_honours_env_override(monkeypatch):
    """Swapping the role→model map (an exposed knob) changes routing."""
    monkeypatch.setenv("MODEL_CHEAP", "some-other-tiny-model")
    assert model_for(ModelRole.CHEAP) == "some-other-tiny-model"
    assert routing_table()["cheap"] == "some-other-tiny-model"


async def test_complete_routes_by_role_to_provider_model(fake_litellm):
    await complete(ModelRole.REASONING, [{"role": "user", "content": "hi"}])
    assert fake_litellm.calls[0]["model"] == "openai/genailab-maas-Phi-4-reasoning"


# ── Fallback chain is exposed + host-overridable ────────────────────────────


async def test_complete_passes_default_fallback_chain(fake_litellm):
    await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    # CHEAP's default fallback is GENERATION.
    assert fake_litellm.calls[0]["fallbacks"] == ["openai/genailab-maas-gpt-4o"]


async def test_configure_fallbacks_override_changes_passed_chain(fake_litellm):
    configure(fallbacks={ModelRole.CHEAP: [ModelRole.REASONING]})
    await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    assert fake_litellm.calls[0]["fallbacks"] == ["openai/genailab-maas-Phi-4-reasoning"]


# ── Cost is never silently zero ─────────────────────────────────────────────


def test_record_call_baseline_is_never_zero_for_real_tokens():
    """A recorded call with real tokens always books a positive baseline cost."""
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0, prompt_tokens=500, completion_tokens=500
    )
    assert usage_tally()["baseline_cost_usd"] > 0.0


async def test_complete_cost_estimate_when_provider_has_no_price(monkeypatch):
    fake = FakeLiteLLM(response=_make_response(content="x"))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    monkeypatch.setattr(fake, "completion_cost", lambda *, completion_response: 0.0)

    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "x"}])
    assert result.usage.cost_usd > 0.0  # token-estimate, not a hard $0


# ── The savings summary agrees with usage_tally (one source of truth) ───────


def _seed_calls():
    """Record two cheap + one generation call with real tokens."""
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0002, prompt_tokens=800, completion_tokens=400,
        role=ModelRole.CHEAP,
    )
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0003, prompt_tokens=900, completion_tokens=500,
        role=ModelRole.CHEAP,
    )
    llm_mod.record_call(
        "genailab-maas-gpt-4o", 0.012, prompt_tokens=1000, completion_tokens=700,
        role=ModelRole.GENERATION,
    )


def test_optimization_summary_top_line_equals_usage_tally():
    _seed_calls()
    summary = optimization_summary()
    tally = usage_tally()
    for key in tally:
        assert summary[key] == tally[key], key


def test_optimization_summary_cost_saved_equals_tally():
    _seed_calls()
    assert optimization_summary()["cost_saved_usd"] == usage_tally()["cost_saved_usd"]


def test_optimization_summary_per_role_costs_sum_to_total():
    _seed_calls()
    summary = optimization_summary()
    per_role_total = sum(r["cost_usd"] for r in summary["by_role"].values())
    assert per_role_total == pytest.approx(summary["total_cost_usd"])


def test_optimization_summary_per_role_breakdown_is_measured():
    _seed_calls()
    by_role = optimization_summary()["by_role"]
    assert by_role["cheap"]["calls"] == 2
    assert by_role["cheap"]["prompt_tokens"] == 1700
    assert by_role["cheap"]["completion_tokens"] == 900
    assert by_role["cheap"]["small_model"] is True
    assert by_role["generation"]["calls"] == 1
    assert by_role["generation"]["small_model"] is False


def test_optimization_summary_small_model_share_matches_tally():
    _seed_calls()
    # 2 of 3 calls routed to a small model.
    assert optimization_summary()["small_model_share"] == pytest.approx(2 / 3)


def test_optimization_summary_empty_before_any_call():
    summary = optimization_summary()
    assert summary["total_calls"] == 0
    assert summary["cost_saved_usd"] == 0.0
    assert summary["small_model_share"] is None
    assert summary["by_role"] == {}


# ── Effective optimization config accessor ──────────────────────────────────


def test_optimization_config_reports_effective_knobs():
    cfg = optimization_config()
    assert cfg["routing"]["cheap"] == "genailab-maas-gpt-4o-mini"
    assert cfg["fallbacks"]["cheap"] == ["generation"]
    assert cfg["timeout_seconds"] == FakeGatewayConfig().timeout_seconds
    assert cfg["max_output_tokens"] == FakeGatewayConfig().max_output_tokens
    assert cfg["baseline_role"] == "generation"
    assert cfg["baseline_model"] == "genailab-maas-gpt-4o"


def test_optimization_config_reflects_fallback_override():
    configure(fallbacks={ModelRole.CHEAP: [ModelRole.REASONING]})
    assert optimization_config()["fallbacks"]["cheap"] == ["reasoning"]


# ── The frontier-baseline is an exposed knob that moves the savings figure ───


def test_baseline_role_env_override(monkeypatch):
    monkeypatch.setenv("GATEWAY_BASELINE_ROLE", "reasoning")
    assert baseline_role() is ModelRole.REASONING


def test_baseline_role_bad_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("GATEWAY_BASELINE_ROLE", "not-a-role")
    assert baseline_role() is ModelRole.GENERATION


def test_configure_baseline_role_changes_baseline_and_savings():
    """Swapping the baseline model reprices the savings from the SAME real call."""
    tokens = {"prompt_tokens": 1000, "completion_tokens": 1000}

    # Default GENERATION baseline.
    llm_mod.record_call("genailab-maas-gpt-4o-mini", 0.0002, role=ModelRole.CHEAP, **tokens)
    generation_baseline = usage_tally()["baseline_cost_usd"]

    # Reset and reprice the identical call against a CHEAP baseline.
    llm_mod._tally = llm_mod._UsageTally()
    configure(baseline_role=ModelRole.CHEAP)
    llm_mod.record_call("genailab-maas-gpt-4o-mini", 0.0002, role=ModelRole.CHEAP, **tokens)
    cheap_baseline = usage_tally()["baseline_cost_usd"]

    assert cheap_baseline < generation_baseline  # cheaper baseline → smaller gap
    assert optimization_config()["baseline_role"] == "cheap"
    assert optimization_summary()["baseline_model"] == "genailab-maas-gpt-4o-mini"


def test_summary_and_config_baseline_model_agree():
    configure(baseline_role=ModelRole.REASONING)
    assert (
        optimization_summary()["baseline_model"]
        == optimization_config()["baseline_model"]
        == model_for(ModelRole.REASONING)
    )


# ── Legacy role-less record_call still attributes so per-role sums hold ──────


def test_role_less_record_call_is_still_attributed():
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini", 0.0002, prompt_tokens=100, completion_tokens=50
    )
    llm_mod.record_call(
        "genailab-maas-gpt-4o", 0.01, prompt_tokens=100, completion_tokens=50
    )
    summary = optimization_summary()
    per_role_total = sum(r["cost_usd"] for r in summary["by_role"].values())
    assert per_role_total == pytest.approx(summary["total_cost_usd"])
    assert summary["by_role"]["cheap"]["calls"] == 1  # small id → CHEAP bucket
