"""Unit tests for the pure recall-scoring math (no infra)."""

from __future__ import annotations

import math

from app.memory import MemoryConfig, RecallCandidate, minmax, rank_top, recency_decay
from app.memory.scoring import ForgetPolicy, score_candidates


def test_recency_decay_half_life():
    assert recency_decay(0.0, 30.0) == 1.0
    assert recency_decay(30.0, 30.0) == 0.5
    assert math.isclose(recency_decay(60.0, 30.0), 0.25)
    assert recency_decay(100.0, 0.0) == 1.0  # non-positive half-life → no decay


def test_minmax_constant_and_empty_map_to_zero():
    assert minmax([]) == []
    assert minmax([5.0, 5.0, 5.0]) == [0.0, 0.0, 0.0]  # no signal → all zero
    assert minmax([0.0, 5.0, 10.0]) == [0.0, 0.5, 1.0]


def test_relevance_dominates_by_default():
    # Default weights: relevance 1.0 dominates recency/importance 0.5.
    cands = [
        RecallCandidate(key="a", text="a", relevance=1.0, age_days=100, importance=1),
        RecallCandidate(key="b", text="b", relevance=0.0, age_days=0, importance=10),
    ]
    top = rank_top(cands, MemoryConfig(), half_life_days=30.0, n=1)
    assert top[0].key == "a"  # most relevant wins even though 'b' is fresh + important


def test_recency_breaks_ties_when_relevance_equal():
    cands = [
        RecallCandidate(key="old", text="o", relevance=0.8, age_days=90),
        RecallCandidate(key="new", text="n", relevance=0.8, age_days=0),
    ]
    top = rank_top(cands, MemoryConfig(), half_life_days=3.0, n=2)
    assert top[0].key == "new"  # equal relevance → fresher first


def test_score_alignment_and_length():
    cands = [
        RecallCandidate(key=str(i), text="t", relevance=i / 4, age_days=i, importance=i + 1)
        for i in range(5)
    ]
    scores = score_candidates(cands, MemoryConfig(), half_life_days=30.0)
    assert len(scores) == 5
    assert all(isinstance(s, float) for s in scores)


def test_rank_top_caps_and_edge_cases():
    cands = [RecallCandidate(key=str(i), text="t", relevance=i / 3) for i in range(3)]
    assert rank_top(cands, MemoryConfig(), half_life_days=30.0, n=0) == []
    assert rank_top([], MemoryConfig(), half_life_days=30.0, n=5) == []
    assert len(rank_top(cands, MemoryConfig(), half_life_days=30.0, n=10)) == 3


def test_forget_policy_archives_invalidated_and_stale():
    pol = ForgetPolicy(forget_floor=0.05, forget_min_age_days=90.0, half_life_days=30.0)
    assert pol.is_archivable(confidence=0.9, age_days=1, access_count=0, invalidated=True)
    # Old, never accessed, decayed below floor → archivable.
    assert pol.is_archivable(confidence=0.5, age_days=200, access_count=0, invalidated=False)
    # Recently accessed / fresh → keep.
    assert not pol.is_archivable(confidence=0.9, age_days=5, access_count=3, invalidated=False)
    assert not pol.is_archivable(confidence=0.9, age_days=10, access_count=0, invalidated=False)
