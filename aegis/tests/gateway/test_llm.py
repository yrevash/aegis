"""Unit tests for the LiteLLM gateway (aegis.gateway.llm).

These run with **no network and no litellm installed**: a fake ``litellm``
module is injected into ``sys.modules`` before the lazy import in
``aegis.gateway.llm`` runs. Config comes from the fixture ``FakeGatewayConfig``
(see ``conftest.py``); governance/observability stay the default no-op hooks.
"""

from __future__ import annotations

import asyncio
import sys
import time
from types import SimpleNamespace

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import complete, embed

from .conftest import FakeGatewayConfig


class _FakeFunction:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _FakeToolCall:
    def __init__(self, id, name, arguments):
        self.id = id
        self.function = _FakeFunction(name, arguments)


def _make_response(*, content, tool_calls=None, model="genailab-maas-gpt-4o"):
    message = SimpleNamespace(content=content, tool_calls=tool_calls or [])
    choice = SimpleNamespace(message=message)
    usage = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


class FakeLiteLLM:
    """Minimal stand-in for the ``litellm`` module."""

    def __init__(self, *, response=None, embedding_response=None, cost=0.0042):
        self.ssl_verify = None
        self.calls = []
        self.embedding_calls = []
        self._response = response
        self._embedding_response = embedding_response
        self._cost = cost

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        return self._response

    async def aembedding(self, **kwargs):
        self.embedding_calls.append(kwargs)
        return self._embedding_response

    def completion_cost(self, *, completion_response):
        return self._cost


@pytest.fixture
def fake_litellm(monkeypatch):
    """Install a fresh fake ``litellm`` (the config-reset fixture handles the rest)."""
    fake = FakeLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


async def test_complete_returns_content_and_usage(fake_litellm):
    fake_litellm._response = _make_response(content="hello world")

    result = await complete(ModelRole.GENERATION, [{"role": "user", "content": "hi"}])

    assert result.content == "hello world"
    assert result.usage.prompt_tokens == 11
    assert result.usage.completion_tokens == 7
    assert result.usage.cost_usd == pytest.approx(0.0042)
    assert result.model == "genailab-maas-gpt-4o"
    assert result.tool_calls == []


async def test_complete_uses_custom_provider_model_string(fake_litellm):
    fake_litellm._response = _make_response(content="x")

    await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    call = fake_litellm.calls[0]
    # openai/<deployment_id> custom-provider form + base_url/api_key wiring.
    assert call["model"] == "openai/genailab-maas-gpt-4o-mini"
    assert call["api_base"] == "https://genailab.tcs.in"
    assert "api_key" in call


async def test_complete_disables_tls_verify(fake_litellm):
    fake_litellm._response = _make_response(content="x")

    await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    # FakeGatewayConfig.ssl_verify defaults to False → TLS verification disabled.
    assert fake_litellm.ssl_verify is False


async def test_complete_parses_tool_calls(fake_litellm):
    fake_litellm._response = _make_response(
        content="",
        tool_calls=[_FakeToolCall("call_1", "create_ticket", '{"title": "bug"}')],
    )

    result = await complete(
        ModelRole.GENERATION,
        [{"role": "user", "content": "make a ticket"}],
        tools=[{"type": "function", "function": {"name": "create_ticket"}}],
    )

    assert len(result.tool_calls) == 1
    tc = result.tool_calls[0]
    assert tc.id == "call_1"
    assert tc.name == "create_ticket"
    assert tc.args == {"title": "bug"}
    assert fake_litellm.calls[0]["tools"]
    assert fake_litellm.calls[0]["tool_choice"] == "auto"


async def test_complete_passes_fallbacks(fake_litellm):
    fake_litellm._response = _make_response(content="x")

    await complete(ModelRole.GENERATION, [{"role": "user", "content": "hi"}])

    fallbacks = fake_litellm.calls[0]["fallbacks"]
    assert "openai/genailab-maas-Phi-4-reasoning" in fallbacks


async def test_complete_handles_bad_tool_json(fake_litellm):
    fake_litellm._response = _make_response(
        content="",
        tool_calls=[_FakeToolCall("c2", "do_thing", "not-json{")],
    )

    result = await complete(ModelRole.GENERATION, [{"role": "user", "content": "x"}])

    assert result.tool_calls[0].args == {"_raw": "not-json{"}


async def test_complete_cost_falls_back_to_token_estimate(fake_litellm, monkeypatch):
    """When the provider has no price for the custom deployment, cost is estimated
    from tokens (never a misleading $0)."""
    fake_litellm._response = _make_response(content="x")

    def _boom(*, completion_response):
        raise ValueError("This model isn't mapped yet")

    monkeypatch.setattr(fake_litellm, "completion_cost", _boom)

    result = await complete(ModelRole.CHEAP, [{"role": "user", "content": "x"}])

    # Positive, token-derived estimate rather than a hard-coded 0.0.
    assert result.usage.cost_usd > 0.0


def test_usage_tally_tracks_baseline_and_savings():
    """A small-model call costs less than the generation baseline → measured saving."""
    # 1k prompt + 1k completion tokens routed to a small model at a cheap rate.
    llm_mod.record_call(
        "genailab-maas-gpt-4o-mini",
        0.0002,
        prompt_tokens=1000,
        completion_tokens=1000,
    )

    tally = llm_mod.usage_tally()
    assert tally["total_cost_usd"] == pytest.approx(0.0002)
    assert tally["baseline_cost_usd"] > tally["total_cost_usd"]
    assert tally["cost_saved_usd"] == pytest.approx(
        tally["baseline_cost_usd"] - tally["total_cost_usd"]
    )
    assert tally["small_model_share"] == 1.0


def test_usage_tally_defaults_to_zero_savings():
    tally = llm_mod.usage_tally()
    assert tally["baseline_cost_usd"] == 0.0
    assert tally["cost_saved_usd"] == 0.0


# ── Per-call safety: max_tokens ─────────────────────────────────────────────


async def test_complete_forwards_default_max_tokens_and_timeout(fake_litellm):
    """Every call is bounded: the configured default cap + timeout are forwarded."""
    fake_litellm._response = _make_response(content="x")

    await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])

    call = fake_litellm.calls[0]
    assert call["max_tokens"] == FakeGatewayConfig().max_output_tokens == 1024
    assert call["timeout"] == FakeGatewayConfig().timeout_seconds


async def test_complete_forwards_explicit_max_tokens(fake_litellm):
    """A per-call ``max_tokens`` overrides the configured default cap."""
    fake_litellm._response = _make_response(content="x")

    await complete(
        ModelRole.CHEAP, [{"role": "user", "content": "hi"}], max_tokens=256
    )

    assert fake_litellm.calls[0]["max_tokens"] == 256


# ── Per-call safety: timeout ────────────────────────────────────────────────


async def test_complete_timeout_becomes_handled_error(fake_litellm, monkeypatch):
    """A hung upstream is bounded by the timeout and surfaces as a handled error.

    The call must NOT await unboundedly: with a tiny timeout, a completion that
    sleeps far longer is converted into a raised ``TimeoutError`` (the existing
    transport-failure / fail-closed path), and returns quickly.
    """

    async def _hang(**kwargs):
        await asyncio.sleep(5)
        return _make_response(content="never")

    monkeypatch.setattr(fake_litellm, "acompletion", _hang)
    monkeypatch.setattr(llm_mod, "_config", FakeGatewayConfig(timeout_seconds=0.02))

    start = time.perf_counter()
    with pytest.raises((asyncio.TimeoutError, TimeoutError)):
        await complete(ModelRole.CHEAP, [{"role": "user", "content": "hi"}])
    elapsed = time.perf_counter() - start

    assert elapsed < 2.0  # bounded — not the 5s hang


# ── Per-call safety: one JSON re-ask ────────────────────────────────────────


def _sequenced(monkeypatch, fake_litellm, responses):
    """Make the fake return ``responses`` in order, capturing each call's kwargs."""
    captured: list[dict] = []

    async def _seq(**kwargs):
        captured.append(kwargs)
        return responses[len(captured) - 1]

    monkeypatch.setattr(fake_litellm, "acompletion", _seq)
    return captured


async def test_complete_json_reask_on_invalid_then_valid(fake_litellm, monkeypatch):
    """Invalid JSON triggers exactly ONE re-ask; a valid retry is returned."""
    captured = _sequenced(
        monkeypatch,
        fake_litellm,
        [
            _make_response(content="not json at all"),
            _make_response(content='{"ok": true}'),
        ],
    )

    result = await complete(
        ModelRole.CHEAP,
        [{"role": "user", "content": "give me json"}],
        response_format={"type": "json_object"},
    )

    assert len(captured) == 2  # exactly one corrective re-ask, no loop
    assert result.content == '{"ok": true}'
    # The re-ask re-called the model with the corrective nudge appended.
    reask_messages = captured[1]["messages"]
    assert reask_messages[-1]["role"] == "user"
    assert "valid JSON" in reask_messages[-1]["content"]
    # The structured-output spec is preserved on the re-ask call.
    assert captured[1]["response_format"] == {"type": "json_object"}


async def test_complete_json_reask_still_invalid_returns_second(
    fake_litellm, monkeypatch
):
    """A still-invalid second reply returns today's behaviour — no third call."""
    captured = _sequenced(
        monkeypatch,
        fake_litellm,
        [
            _make_response(content="nope"),
            _make_response(content="still nope"),
        ],
    )

    result = await complete(
        ModelRole.CHEAP,
        [{"role": "user", "content": "json pls"}],
        response_format={"type": "json_object"},
    )

    assert len(captured) == 2  # bounded: no third attempt
    assert result.content == "still nope"


async def test_complete_no_reask_when_json_valid(fake_litellm):
    """Valid JSON on the first reply is returned without any re-ask."""
    fake_litellm._response = _make_response(content='{"a": 1}')

    result = await complete(
        ModelRole.CHEAP,
        [{"role": "user", "content": "x"}],
        response_format={"type": "json_object"},
    )

    assert len(fake_litellm.calls) == 1  # no re-ask
    assert result.content == '{"a": 1}'


async def test_complete_no_reask_without_response_format(fake_litellm):
    """Non-JSON prose is fine when no structured output was requested."""
    fake_litellm._response = _make_response(content="just some prose")

    result = await complete(ModelRole.GENERATION, [{"role": "user", "content": "x"}])

    assert len(fake_litellm.calls) == 1
    assert result.content == "just some prose"


async def test_embed_returns_vectors(fake_litellm):
    fake_litellm._embedding_response = SimpleNamespace(
        data=[{"embedding": [0.1, 0.2]}, {"embedding": [0.3, 0.4]}],
        usage=SimpleNamespace(prompt_tokens=4),
    )

    vectors = await embed(["a", "b"])

    assert vectors == [[0.1, 0.2], [0.3, 0.4]]
    call = fake_litellm.embedding_calls[0]
    assert call["model"] == "openai/genailab-maas-text-embedding-3-large"
    assert call["input"] == ["a", "b"]
