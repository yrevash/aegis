"""Tests for the LLM-as-reranker scoring stage."""

from __future__ import annotations

from aegis.retrieval.models import Candidate
from aegis.retrieval.reranker import rerank, rerank_scored
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


# ── a failed rerank is labelled, never passed off as relevance grades ─────────


async def test_rerank_outcome_reports_a_failed_grading_instead_of_hiding_it():
    # REGRESSION: an unparseable response silently returned recall order, and the
    # pipeline stamped it `ran=True` with the RRF scores as `top_scores` — visually
    # indistinguishable from real relevance grades.
    fake = RecordingComplete("this is not json at all")

    outcome = await rerank_scored("q", _candidates(), complete=fake, top_k=2)

    assert [c.id for c in outcome.candidates] == ["c0", "c1"]  # honest fallback order…
    assert outcome.graded is False  # …but labelled as ungraded
    assert outcome.ungraded == 2
    assert outcome.reason is not None


async def test_rerank_does_not_fabricate_a_zero_grade_for_ungraded_candidates():
    # REGRESSION: candidates missing from the model's response sorted with -inf but were
    # stamped `score=0.0` — a grade the model never gave.
    candidates = [
        Candidate(id="c0", text="irrelevant filler about cats", score=0.031),
        Candidate(id="c1", text="the capital of france is paris", score=0.028),
        Candidate(id="c2", text="paris has many museums", score=0.016),
    ]
    fake = RecordingComplete('{"scores": [{"id": 2, "score": 7}]}')

    outcome = await rerank_scored("q", candidates, complete=fake, top_k=3)

    # The one graded candidate leads, carrying its real grade.
    assert outcome.candidates[0].id == "c2"
    assert outcome.candidates[0].score == 7.0
    # The ungraded remainder follows in recall order, keeping the fused scores it came
    # in with — not a made-up 0.0 — and is counted so the mixed scales are readable.
    assert [c.id for c in outcome.candidates[1:]] == ["c0", "c1"]
    assert [c.score for c in outcome.candidates[1:]] == [0.031, 0.028]
    assert outcome.graded is True
    assert outcome.ungraded == 2
    assert "graded only 1 of 3" in outcome.reason


async def test_rerank_fully_graded_reports_no_degradation():
    fake = RecordingComplete('{"scores": [{"id": 0, "score": 1}, {"id": 1, "score": 9}, '
                             '{"id": 2, "score": 5}]}')

    outcome = await rerank_scored("q", _candidates(), complete=fake, top_k=3)

    assert outcome.graded is True
    assert outcome.ungraded == 0
    assert outcome.reason is None
