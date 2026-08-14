"""Tests for ``transcribe`` — the audio path (``ModelRole.VOICE``, per-minute billing).

No network and no litellm: a fake ``litellm`` exposing ``atranscription`` is
injected into ``sys.modules``, and the autouse ``_reset_gateway_state`` fixture
gives every test a fresh tally and the default no-op hooks.

What is NOT covered here: the shape the live TCS fleet's
``azure/genailab-maas-whisper`` deployment actually returns. The gateway
credential is a placeholder, so every assertion below is against a fake.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import BudgetExceededError, transcribe
from aegis.gateway.routing import _rate_for
from aegis.gateway.types import CostSource

from .test_llm import FakeLiteLLM, _make_transcription

_VOICE_RATE_PER_MINUTE = _rate_for(ModelRole.VOICE)[0]  # 0.006 $/audio-minute


@pytest.fixture
def fake_litellm(monkeypatch):
    """A fake litellm whose ``atranscription`` returns a verbose_json-ish reply."""
    fake = FakeLiteLLM(
        transcription_response=_make_transcription(
            text="the quarterly report is late",
            duration=120.0,
            language="en",
            segments=[
                {"id": 0, "start": 0.0, "end": 60.0, "text": "the quarterly report"},
                {"id": 1, "start": 60.0, "end": 120.0, "text": " is late"},
            ],
        )
    )
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


@pytest.fixture
def audio_file(tmp_path):
    """A tiny on-disk 'audio' file (contents are never parsed by the fake)."""
    path = tmp_path / "clip.wav"
    path.write_bytes(b"RIFF....WAVEfake")
    return path


# ── The call shape: a hosted fleet call taking a FILE HANDLE ────────────────


async def test_transcribe_routes_to_the_voice_deployment(fake_litellm, audio_file):
    """Policy is fleet-only: this is a hosted call, not a local Whisper."""
    await transcribe(audio_file)

    call = fake_litellm.transcription_calls[0]
    assert call["model"] == "openai/genailab-maas-whisper"
    assert call["api_base"] == "https://genailab.tcs.in"
    assert "api_key" in call
    # A file handle, not ``messages`` — and one that was really readable.
    assert "messages" not in call
    assert call["_file_bytes"] == b"RIFF....WAVEfake"


async def test_transcribe_accepts_an_open_handle(fake_litellm, audio_file):
    with audio_file.open("rb") as handle:
        result = await transcribe(handle)
    assert result.text == "the quarterly report is late"
    assert fake_litellm.transcription_calls[0]["_file_bytes"] == b"RIFF....WAVEfake"


async def test_transcribe_forwards_language_and_prompt(fake_litellm, audio_file):
    await transcribe(audio_file, language="en", prompt="Aegis, TCS")

    call = fake_litellm.transcription_calls[0]
    assert call["language"] == "en"
    assert call["prompt"] == "Aegis, TCS"
    assert call["response_format"] == "verbose_json"


async def test_transcribe_is_bounded_by_the_configured_timeout(fake_litellm, audio_file):
    await transcribe(audio_file)
    assert fake_litellm.transcription_calls[0]["timeout"] == 60.0


# ── The typed result carries what the provider actually reported ────────────


async def test_transcribe_returns_transcript_language_duration_and_segments(
    fake_litellm, audio_file
):
    result = await transcribe(audio_file)

    assert result.text == "the quarterly report is late"
    assert result.language == "en"
    assert result.duration_seconds == pytest.approx(120.0)
    assert [s.text for s in result.segments] == ["the quarterly report", " is late"]
    assert result.segments[1].start == pytest.approx(60.0)
    assert result.model == "genailab-maas-whisper"


async def test_transcribe_omits_what_the_provider_did_not_report(monkeypatch, audio_file):
    """A plain-text response invents no language, duration or segments."""
    fake = FakeLiteLLM(transcription_response=_make_transcription(text="just words"))
    monkeypatch.setitem(sys.modules, "litellm", fake)

    result = await transcribe(audio_file, response_format="text")

    assert result.text == "just words"
    assert result.language is None
    assert result.duration_seconds is None
    assert result.segments == []


# ── Billing: audio MINUTES, not tokens ──────────────────────────────────────


async def test_transcription_with_known_duration_ledgers_a_non_zero_cost(
    fake_litellm, audio_file
):
    """The headline guarantee: a 2-minute clip is NOT a $0.00 ledger row.

    Whisper bills per audio-minute, so a per-token cost model would book
    ``prompt_tokens=0`` → ``$0.00`` and let a USD-capped tenant transcribe
    without limit.
    """
    result = await transcribe(audio_file)

    assert result.usage.audio_seconds == pytest.approx(120.0)
    assert result.usage.prompt_tokens == 0  # genuinely no tokens were consumed
    assert result.usage.cost_usd > 0.0
    assert result.usage.cost_usd == pytest.approx(2 * _VOICE_RATE_PER_MINUTE)
    assert result.usage.cost_source is CostSource.ESTIMATED


async def test_transcription_cost_scales_with_duration(monkeypatch, audio_file):
    """Twice the audio, twice the charge — the unit really is a minute."""
    fake = FakeLiteLLM(transcription_response=_make_transcription(duration=30.0))
    monkeypatch.setitem(sys.modules, "litellm", fake)
    short = (await transcribe(audio_file)).usage.cost_usd

    fake._transcription_response = _make_transcription(duration=60.0)
    long = (await transcribe(audio_file)).usage.cost_usd

    assert long == pytest.approx(2 * short)


async def test_caller_supplied_duration_is_used_when_provider_reports_none(
    monkeypatch, audio_file
):
    fake = FakeLiteLLM(transcription_response=_make_transcription(text="x"))
    monkeypatch.setitem(sys.modules, "litellm", fake)

    result = await transcribe(audio_file, response_format="text", duration_seconds=60.0)

    assert result.duration_seconds == pytest.approx(60.0)
    assert result.usage.cost_usd == pytest.approx(_VOICE_RATE_PER_MINUTE)


async def test_provider_duration_wins_over_the_caller_hint(fake_litellm, audio_file):
    """The provider measured the clip; the caller only guessed."""
    result = await transcribe(audio_file, duration_seconds=999.0)
    assert result.duration_seconds == pytest.approx(120.0)


async def test_undeterminable_cost_is_visible_not_silently_zero(
    monkeypatch, audio_file, caplog
):
    """No duration anywhere → the $0 is tagged UNPRICED and logged, not hidden."""
    fake = FakeLiteLLM(transcription_response=_make_transcription(text="x"))
    monkeypatch.setitem(sys.modules, "litellm", fake)

    with caplog.at_level("WARNING"):
        result = await transcribe(audio_file, response_format="text")

    assert result.usage.cost_usd == 0.0
    assert result.usage.cost_source is CostSource.UNPRICED
    assert any("duration" in r.message.lower() for r in caplog.records)


async def test_transcription_is_counted_in_the_usage_tally(fake_litellm, audio_file):
    await transcribe(audio_file)

    tally = llm_mod.usage_tally()
    assert tally["total_calls"] == 1
    assert tally["total_cost_usd"] == pytest.approx(2 * _VOICE_RATE_PER_MINUTE)
    assert tally["total_audio_seconds"] == pytest.approx(120.0)
    # VOICE has exactly one deployment in the fleet — small-model ROUTING never
    # chose it, so it must not move a routing metric in either direction.
    assert tally["small_model_share"] is None


async def test_transcription_books_no_fabricated_saving(fake_litellm, audio_file):
    """A frontier chat model cannot transcribe, so the saving is zero, not negative."""
    await transcribe(audio_file)

    tally = llm_mod.usage_tally()
    assert tally["cost_saved_usd"] == 0.0
    assert tally["baseline_cost_usd"] == pytest.approx(tally["total_cost_usd"])


async def test_transcription_does_not_erase_chat_savings(fake_litellm, audio_file):
    """Mixing a voice call into the tally must not eat a real chat saving."""
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini",
        0.0002,
        prompt_tokens=1000,
        completion_tokens=1000,
        role=ModelRole.CHEAP,
    )
    chat_saving = llm_mod.usage_tally()["cost_saved_usd"]
    assert chat_saving > 0.0

    await transcribe(audio_file)

    assert llm_mod.usage_tally()["cost_saved_usd"] == pytest.approx(chat_saving)


async def test_transcription_appears_in_the_per_role_breakdown(fake_litellm, audio_file):
    await transcribe(audio_file)

    by_role = llm_mod.optimization_summary()["by_role"]
    assert by_role["voice"]["calls"] == 1
    assert by_role["voice"]["audio_seconds"] == pytest.approx(120.0)
    assert by_role["voice"]["cost_usd"] > 0.0


# ── Governance: enforced BEFORE spend, ledgered after ───────────────────────


class _RefusingGovernance:
    def __init__(self):
        self.enforce_calls = 0
        self.record_calls: list[dict] = []

    def get_context(self):
        return SimpleNamespace(tenant_id=1, user_id=2)

    async def enforce(self, ctx):
        self.enforce_calls += 1
        raise BudgetExceededError(
            scope="tenant", scope_id=1, limit_type="usd_cap", limit=1.0, used=2.0
        )

    async def record(self, ctx, **kwargs):
        self.record_calls.append(kwargs)


class _RecordingGovernance:
    def __init__(self):
        self.record_calls: list[dict] = []

    def get_context(self):
        return SimpleNamespace(tenant_id=1, user_id=2)

    async def enforce(self, ctx):
        return None

    async def record(self, ctx, **kwargs):
        self.record_calls.append(kwargs)


async def test_budget_is_enforced_before_any_audio_spend(
    fake_litellm, audio_file, monkeypatch
):
    gov = _RefusingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    with pytest.raises(BudgetExceededError) as ei:
        await transcribe(audio_file)

    assert ei.value.limit_type == "usd_cap"
    assert gov.enforce_calls == 1
    assert fake_litellm.transcription_calls == []  # no spend happened
    assert gov.record_calls == []


async def test_transcription_ledgers_audio_seconds_and_cost(
    fake_litellm, audio_file, monkeypatch
):
    gov = _RecordingGovernance()
    monkeypatch.setattr(llm_mod, "_governance", gov)

    await transcribe(audio_file)

    assert len(gov.record_calls) == 1
    rec = gov.record_calls[0]
    assert rec["model"] == "genailab-maas-whisper"
    assert rec["audio_seconds"] == pytest.approx(120.0)
    assert rec["prompt_tokens"] == 0
    assert rec["cost_usd"] > 0.0  # a USD cap can therefore actually bite


async def test_ungoverned_transcription_is_a_full_no_op(fake_litellm, audio_file):
    """The default no-op hook: no enforcement, no ledger, call still works."""
    result = await transcribe(audio_file)
    assert result.text


# ── Observability ───────────────────────────────────────────────────────────


class _SpyObservability(llm_mod._NoOpObservability):
    def __init__(self):
        self.operations: list[llm_mod.GenAIOperation] = []
        self.models: list[str] = []
        self.usages: list[dict] = []

    def span(self, operation, model, *, temperature=None, max_tokens=None):
        self.operations.append(operation)
        self.models.append(model)
        return super().span(operation, model, temperature=temperature, max_tokens=max_tokens)

    def set_usage(self, span, **kwargs):
        self.usages.append(kwargs)


async def test_transcribe_opens_a_transcription_span(fake_litellm, audio_file, monkeypatch):
    spy = _SpyObservability()
    monkeypatch.setattr(llm_mod, "_observability", spy)

    await transcribe(audio_file)

    assert spy.operations == [llm_mod.GenAIOperation.TRANSCRIPTION]
    assert spy.models == ["genailab-maas-whisper"]
    assert spy.usages[0]["cost_usd"] > 0.0
    assert spy.usages[0]["response_model"] == "genailab-maas-whisper"


def test_gateway_transcription_operation_maps_onto_the_otel_sink():
    """The gateway enum and the semconv enum agree by value (the sink's contract)."""
    from aegis.observability.semconv import GenAIOperation as SemconvOperation

    assert (
        SemconvOperation(llm_mod.GenAIOperation.TRANSCRIPTION.value)
        is SemconvOperation.TRANSCRIPTION
    )
