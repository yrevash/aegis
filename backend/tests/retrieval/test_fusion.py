"""Tests for Reciprocal Rank Fusion (rank merging, origin tagging, ties)."""

from __future__ import annotations

import pytest

from app.api.schemas import RetrievalOrigin
from app.retrieval.fusion import (
    ORIGIN_METADATA_KEY,
    RankedList,
    collect_origins,
    reciprocal_rank_fusion,
)
from app.retrieval.models import Candidate


def _cand(cid: str) -> Candidate:
    return Candidate(id=cid, text=f"text-{cid}")


def test_rrf_rewards_agreement_across_lists():
    # `b` is rank-1 in BOTH lists; `a` and `c` each appear once at rank 2.
    vector = RankedList(
        origins=(RetrievalOrigin.VECTOR,),
        candidates=[_cand("b"), _cand("a")],
    )
    graph = RankedList(
        origins=(RetrievalOrigin.GRAPH,),
        candidates=[_cand("b"), _cand("c")],
    )
    fused = reciprocal_rank_fusion([vector, graph], k=60)

    # b agreed at the top of both lists → highest summed RRF score; a and c tie below.
    assert fused[0].id == "b"
    assert {c.id for c in fused} == {"a", "b", "c"}
    scores = {c.id: c.score for c in fused}
    assert scores["b"] > scores["a"]
    assert scores["b"] > scores["c"]
    assert scores["a"] == pytest.approx(scores["c"])


def test_rrf_tags_each_candidate_with_contributing_origins():
    vector = RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=[_cand("a"), _cand("b")])
    bm25 = RankedList(origins=(RetrievalOrigin.BM25,), candidates=[_cand("b"), _cand("c")])
    fused = reciprocal_rank_fusion([vector, bm25])

    by_id = {c.id: c for c in fused}
    # `b` was surfaced by BOTH retrievers → carries both origins (canonical order).
    assert by_id["b"].metadata[ORIGIN_METADATA_KEY] == ["vector", "bm25"]
    assert by_id["a"].metadata[ORIGIN_METADATA_KEY] == ["vector"]
    assert by_id["c"].metadata[ORIGIN_METADATA_KEY] == ["bm25"]
    # collect_origins unions them in canonical (vector, graph, bm25, cache) order.
    assert collect_origins(fused) == [RetrievalOrigin.VECTOR, RetrievalOrigin.BM25]


def test_rrf_ties_break_by_first_appearance_deterministically():
    # Two disjoint singleton lists: equal rank-1 RRF scores → tie.
    first = RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=[_cand("x")])
    second = RankedList(origins=(RetrievalOrigin.BM25,), candidates=[_cand("y")])
    fused = reciprocal_rank_fusion([first, second])

    assert fused[0].score == pytest.approx(fused[1].score)
    assert [c.id for c in fused] == ["x", "y"]  # x seen first → ordered first


def test_rrf_preserves_first_seen_text_and_metadata():
    rich = Candidate(id="a", text="canonical", metadata={"doc": "d1"})
    dupe = Candidate(id="a", text="other", metadata={"doc": "d2"})
    fused = reciprocal_rank_fusion(
        [
            RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=[rich]),
            RankedList(origins=(RetrievalOrigin.GRAPH,), candidates=[dupe]),
        ]
    )
    assert len(fused) == 1
    assert fused[0].text == "canonical"
    assert fused[0].metadata["doc"] == "d1"  # first appearance wins
    assert fused[0].metadata[ORIGIN_METADATA_KEY] == ["vector", "graph"]


def test_rrf_empty_lists_yield_empty_result():
    assert reciprocal_rank_fusion([]) == []
    empty = RankedList(origins=(RetrievalOrigin.BM25,), candidates=[])
    assert reciprocal_rank_fusion([empty]) == []


def test_rrf_rejects_nonpositive_k():
    with pytest.raises(ValueError):
        reciprocal_rank_fusion([], k=0)
