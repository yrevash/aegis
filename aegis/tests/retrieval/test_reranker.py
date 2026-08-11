"""Tests for the LLM-as-reranker scoring stage."""

from __future__ import annotations

from aegis.retrieval.models import Candidate
from aegis.retrieval.reranker import rerank
from aegis.retrieval.spotlight import DATAMARK_TOKEN

from .conftest import RecordingComplete


def _candidates():
    return [
        Candidate(id="c0", text="irrelevant filler about cats"),
        Candidate(id="c1", text="the capital of france is paris"),
        Candidate(id="c2", text="paris has many museums"),
    ]


async def test_rerank_orders_by_score_and_truncates():
    fake = RecordingComplete('{"scores": [{"id": 0, "score": 1}, {"id": 1, "score": 9}, '
                             '{"id": 2, "score": 5}]}')
    out = await rerank("capital of france", _candidates(), complete=fake, top_k=2)
    assert [c.id for c in out] == ["c1", "c2"]
    assert out[0].score == 9.0


async def test_rerank_spotlights_candidate_text_in_prompt():
    fake = RecordingComplete('{"scores": []}')
    await rerank("q", _candidates(), complete=fake, top_k=1)
    user_msg = fake.calls[0]["messages"][-1]["content"]
    # Candidate text is datamarked before being shown to the scoring model.
    assert DATAMARK_TOKEN in user_msg
    assert fake.calls[0]["response_format"] == {"type": "json_object"}


async def test_rerank_falls_back_to_recall_order_on_bad_json():
    fake = RecordingComplete("this is not json at all")
    out = await rerank("q", _candidates(), complete=fake, top_k=2)
    assert [c.id for c in out] == ["c0", "c1"]


async def test_rerank_empty_candidates():
    fake = RecordingComplete("{}")
    assert await rerank("q", [], complete=fake, top_k=5) == []
