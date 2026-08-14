"""Non-token billing units, and the small-model classification they exposed.

``Usage`` used to be tokens-only and ``_estimate_cost`` multiplied token counts by
per-1k rates, so a per-minute-billed Whisper call ledgered ``$0.00``. These tests
pin the unit-aware cost model end to end (rate table → estimate → tally → result)
and the parameter-count veto that stops a 90-billion-parameter vision model
counting as a "small model".
"""

from __future__ import annotations

import sys

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import complete, count_images
from aegis.gateway.routing import (
    BillingUnit,
    billable_input_units,
    billing_unit,
    is_routable_role,
    is_small_model,
    unit_cost,
)
from aegis.gateway.types import CostSource, Usage

from .test_llm import FakeLiteLLM, _make_response


@pytest.fixture
def fake_litellm(monkeypatch):
    fake = FakeLiteLLM(response=_make_response(content="x"))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


# ── The rate table now has a UNIT, not just a number ────────────────────────


def test_voice_bills_per_audio_minute():
    """``routing.py``'s ``VOICE: (0.006, 0.0)`` means $/minute, with no output charge."""
    assert billing_unit(ModelRole.VOICE) is BillingUnit.AUDIO_MINUTES


@pytest.mark.parametrize(
    "role",
    [ModelRole.CHEAP, ModelRole.GENERATION, ModelRole.REASONING, ModelRole.EMBEDDING],
)
def test_text_roles_still_bill_per_token(role):
    assert billing_unit(role) is BillingUnit.TOKENS


def test_billing_unit_is_env_overridable(monkeypatch):
    monkeypatch.setenv("COST_VISION_UNIT", "images")
    assert billing_unit(ModelRole.VISION) is BillingUnit.IMAGES


def test_bad_billing_unit_env_falls_back_to_the_default(monkeypatch):
    """A typo can never break a live call — it just leaves the default in place."""
    monkeypatch.setenv("COST_VOICE_UNIT", "furlongs")
    assert billing_unit(ModelRole.VOICE) is BillingUnit.AUDIO_MINUTES


def test_billable_input_units_are_expressed_in_the_role_s_own_unit():
    assert billable_input_units(ModelRole.VOICE, audio_seconds=90.0) == pytest.approx(1.5)
    assert billable_input_units(ModelRole.CHEAP, prompt_tokens=2000) == pytest.approx(2.0)
    # Audio seconds mean nothing to a token-billed role, and vice versa.
    assert billable_input_units(ModelRole.CHEAP, audio_seconds=600.0) == 0.0
    assert billable_input_units(ModelRole.VOICE, prompt_tokens=5000) == 0.0


# ── unit_cost / _estimate_cost ──────────────────────────────────────────────


def test_unit_cost_is_unchanged_for_token_only_calls():
    """Backwards compatibility: the old per-1k-token formula, exactly."""
    assert unit_cost(
        ModelRole.GENERATION, prompt_tokens=1000, completion_tokens=1000
    ) == pytest.approx(0.0025 + 0.01)


def test_unit_cost_prices_audio_by_the_minute():
    assert unit_cost(ModelRole.VOICE, audio_seconds=60.0) == pytest.approx(0.006)
    assert unit_cost(ModelRole.VOICE, audio_seconds=30.0) == pytest.approx(0.003)


def test_a_transcription_with_zero_tokens_is_not_free():
    """The exact bug: tokens are 0, yet the call cost real money."""
    assert unit_cost(ModelRole.VOICE, prompt_tokens=0, audio_seconds=300.0) > 0.0


def test_estimate_cost_keeps_its_positional_token_signature():
    """Every pre-existing caller passes two positional token counts."""
    assert llm_mod._estimate_cost(ModelRole.CHEAP, 1000, 1000) == pytest.approx(
        0.00015 + 0.0006
    )


def test_unit_cost_honours_the_env_rate_override(monkeypatch):
    monkeypatch.setenv("COST_VOICE_IN", "0.012")
    assert unit_cost(ModelRole.VOICE, audio_seconds=60.0) == pytest.approx(0.012)


def test_images_become_billable_when_the_unit_is_configured(monkeypatch):
    monkeypatch.setenv("COST_VISION_UNIT", "images")
    monkeypatch.setenv("COST_VISION_IN", "0.002")
    assert unit_cost(ModelRole.VISION, images=3) == pytest.approx(0.006)


# ── record_call / usage_tally carry the units ───────────────────────────────


def test_record_call_accumulates_non_token_units():
    llm_mod.record_call(
        "genailab-maas-whisper", 0.012, audio_seconds=120.0, role=ModelRole.VOICE
    )
    tally = llm_mod.usage_tally()
    assert tally["total_audio_seconds"] == pytest.approx(120.0)
    assert tally["total_cost_usd"] == pytest.approx(0.012)


def test_record_call_accumulates_image_counts():
    llm_mod.record_call(
        "genailab-maas-Llama-3.2-90B-Vision-Instruct",
        0.02,
        prompt_tokens=1200,
        images=2,
        role=ModelRole.VISION,
    )
    assert llm_mod.usage_tally()["total_images"] == 2
    assert llm_mod.optimization_summary()["by_role"]["vision"]["images"] == 2


def test_token_only_record_call_reports_no_units():
    llm_mod.record_call("genailab-maas-gpt-4o", 0.01, prompt_tokens=10, completion_tokens=5)
    tally = llm_mod.usage_tally()
    assert tally["total_audio_seconds"] == 0.0
    assert tally["total_images"] == 0


# ── small_model_share only counts routable roles ────────────────────────────


def test_only_routable_roles_count_toward_small_model_share():
    assert is_routable_role(ModelRole.CHEAP)
    assert is_routable_role(ModelRole.VISION)
    assert not is_routable_role(ModelRole.EMBEDDING)
    assert not is_routable_role(ModelRole.VOICE)
    # A legacy role-less caller keeps today's behaviour.
    assert is_routable_role(None)


def test_non_routable_calls_do_not_dilute_the_routing_metric():
    llm_mod.record_call("genailab-maas-gpt-4o-mini", 0.0002, role=ModelRole.CHEAP)
    llm_mod.record_call("genailab-maas-whisper", 0.006, audio_seconds=60.0, role=ModelRole.VOICE)
    llm_mod.record_call(
        "genailab-maas-text-embedding-3-large", 0.0001, role=ModelRole.EMBEDDING
    )

    tally = llm_mod.usage_tally()
    assert tally["total_calls"] == 3  # every ledgered call is counted
    assert tally["small_model_share"] == 1.0  # ...but only 1 was ever routable


# ── The 90B "small model" bug ───────────────────────────────────────────────


def test_the_90b_vision_model_is_not_a_small_model():
    """A 90-billion-parameter model is not small, whatever its generation.

    ``_SMALL_MODEL_MARKERS`` contained ``"llama-3.2"`` and the routed VISION
    deployment is ``genailab-maas-Llama-3.2-90B-Vision-Instruct``, so every
    vision call inflated ``small_model_share`` — and the headline savings story —
    in the favourable direction.
    """
    from aegis.gateway.routing import model_for

    assert is_small_model(model_for(ModelRole.VISION)) is False
    assert is_small_model("genailab-maas-Llama-3.2-90B-Vision-Instruct") is False


def test_genuinely_small_llama_3_2_models_are_still_small():
    """The fix is a size veto, not the removal of the generation marker."""
    assert is_small_model("genailab-maas-Llama-3.2-1B-Instruct") is True
    assert is_small_model("genailab-maas-Llama-3.2-3B-Instruct") is True


def test_the_11b_vision_variant_is_also_not_small():
    assert is_small_model("genailab-maas-Llama-3.2-11B-Vision-Instruct") is False


def test_existing_small_model_classification_is_unchanged():
    assert is_small_model("genailab-maas-gpt-4o-mini") is True
    assert is_small_model("genailab-maas-phi-3.5-mini") is True
    assert is_small_model("genailab-maas-gpt-4o") is False
    assert is_small_model("genailab-maas-text-embedding-3-large") is False


def test_a_vision_call_is_attributed_as_a_large_model():
    llm_mod.record_call(
        "genailab-maas-Llama-3.2-90B-Vision-Instruct",
        0.02,
        prompt_tokens=1000,
        images=1,
        role=ModelRole.VISION,
    )
    tally = llm_mod.usage_tally()
    assert tally["small_calls"] == 0
    assert tally["small_model_share"] == 0.0
    assert llm_mod.optimization_summary()["by_role"]["vision"]["small_model"] is False


# ── Image counts flow end to end through ``complete`` ───────────────────────


def test_count_images_counts_multimodal_parts():
    messages = [
        {"role": "user", "content": "plain text"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "what is in these?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,BBB"}},
            ],
        },
    ]
    assert count_images(messages) == 2


def test_count_images_is_zero_for_a_text_only_chat():
    assert count_images([{"role": "user", "content": "hi"}]) == 0


async def test_complete_carries_the_image_count_into_usage(fake_litellm):
    result = await complete(
        ModelRole.VISION,
        [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {"type": "image_url", "image_url": {"url": "https://x/y.png"}},
                ],
            }
        ],
    )

    assert result.usage.images == 1
    assert llm_mod.usage_tally()["total_images"] == 1


async def test_a_text_only_completion_reports_no_images(fake_litellm):
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    assert result.usage.images == 0


# ── A cost that cannot be determined is visible ─────────────────────────────


async def test_a_priced_chat_call_is_tagged_as_provider_priced(fake_litellm):
    result = await complete(ModelRole.GENERATION, [{"role": "user", "content": "hi"}])
    assert result.usage.cost_source is CostSource.PROVIDER


async def test_an_unmapped_chat_call_is_tagged_as_estimated(fake_litellm, monkeypatch):
    monkeypatch.setattr(fake_litellm, "completion_cost", lambda *, completion_response: 0.0)
    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    assert result.usage.cost_usd > 0.0
    assert result.usage.cost_source is CostSource.ESTIMATED


def test_unpriced_billable_work_is_logged_not_hidden(monkeypatch, caplog):
    monkeypatch.setenv("COST_CHEAP_IN", "0")
    monkeypatch.setenv("COST_CHEAP_OUT", "0")
    with caplog.at_level("WARNING"):
        cost, source = llm_mod._resolve_cost(
            None, None, ModelRole.CHEAP, prompt_tokens=500, completion_tokens=500
        )
    assert cost == 0.0
    assert source is CostSource.UNPRICED
    assert any("Unpriced" in r.message for r in caplog.records)


def test_usage_defaults_are_backwards_compatible():
    """A token-only ``Usage`` constructed the old way is unchanged."""
    usage = Usage(prompt_tokens=10, completion_tokens=5, cost_usd=0.1)
    assert usage.audio_seconds == 0.0
    assert usage.images == 0
    assert usage.cost_source is CostSource.PROVIDER
