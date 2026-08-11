"""Tests for context-aware query rewriting (offline, injected fake ``complete``)."""

from __future__ import annotations

from aegis.core.models import ModelRole
from aegis.retrieval.query_rewrite import RewriteResult, rewrite_query

from .conftest import RecordingComplete


def _history() -> list[dict]:
    return [
        {"role": "user", "content": "Tell me about France."},
        {"role": "assistant", "content": "France is a country in Europe."},
    ]


async def test_rewrite_expands_context_dependent_turn():
    fake = RecordingComplete(
        '{"rewritten": "what is the capital of France", '
        '"reason": "resolved the pronoun \'its\' to France"}'
    )
    out = await rewrite_query("what about its capital?", history=_history(), complete=fake)

    assert isinstance(out, RewriteResult)
    assert out.original == "what about its capital?"
    assert out.rewritten == "what is the capital of France"
    assert out.changed is True
    assert "France" in out.reason
    # Call convention mirrors the reranker: CHEAP role + strict JSON response format.
    assert fake.calls[0]["role"] == ModelRole.CHEAP
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    # The prior conversation is actually threaded into the prompt.
    assert "France is a country" in fake.calls[0]["messages"][-1]["content"]
    # The rewrite call's token spend is reported so the run's telemetry can accrue it.
    assert out.usage.prompt_tokens == 6
    assert out.usage.completion_tokens == 4


async def test_rewrite_no_op_when_complete_is_none():
    out = await rewrite_query("standalone query", history=_history(), complete=None)

    assert out.original == out.rewritten == "standalone query"
    assert out.changed is False
    assert out.reason  # honest, non-empty reason
    # No call was made, so no spend is reported (zero usage).
    assert out.usage.prompt_tokens == 0 and out.usage.cost_usd == 0.0


async def test_rewrite_no_op_when_model_returns_same_query():
    fake = RecordingComplete('{"rewritten": "  standalone query  ", "reason": "already ok"}')
    out = await rewrite_query("standalone query", complete=fake)

    assert out.changed is False
    assert out.rewritten == "standalone query"


async def test_rewrite_no_op_on_unparseable_json():
    fake = RecordingComplete("not json at all")
    out = await rewrite_query("original q", complete=fake)

    assert out.changed is False
    assert out.rewritten == "original q"
    assert "unparseable" in out.reason
    # A call was made even though it did not parse, so its spend is still reported.
    assert out.usage.prompt_tokens == 6


async def test_rewrite_no_op_on_empty_rewrite():
    fake = RecordingComplete('{"rewritten": "   ", "reason": "blank"}')
    out = await rewrite_query("original q", complete=fake)

    assert out.changed is False
    assert out.rewritten == "original q"
