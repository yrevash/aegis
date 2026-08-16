"""Phase 1 — the retrieval arsenal is real, selectable, and observable.

These tests prove, offline (no Neo4j/Redis/network — an embedded ``:memory:`` Chroma
and injected fakes), that:

* every recall arm can fire and its candidate count is *measured*, not fabricated;
* provenance + observability report which arms ran, that fusion was RRF, whether
  rerank ran (with its top scores), and whether spotlighting was applied;
* the tunable knobs (``rerank_enabled`` / ``spotlight_enabled``) actually change the
  observable behaviour;
* the query-rewrite and Self-RAG loop, when wrapped around retrieval, report the
  rewritten query and the real iteration count;
* the AG-UI stream surfaces the whole observability payload.
"""

from __future__ import annotations

import json

import pytest

from aegis.core import stream_names
from aegis.core.stream import AegisEmitter
from aegis.retrieval.agentic import agentic_retrieve
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.memory import InMemoryKnowledgeBackend, InMemoryRedis
from aegis.retrieval.models import RetrievalResult
from aegis.retrieval.pipeline import RetrievalConfig, Retriever
from aegis.retrieval.query_rewrite import RewriteResult
from aegis.retrieval.spotlight import DATAMARK_TOKEN
from aegis.retrieval.stream import stream_retrieve
from aegis.retrieval.types import FusionMethod, RetrievalOrigin, RetrievalScope

from .conftest import RecordingComplete

#: The unscoped (no tenant) partition these tests run under.
_SCOPE = RetrievalScope(tenant_id=None)

_DOCS = [
    ("closures", "A closure is confirmed by the original approver within a week."),
    ("escalation", "Escalate a closure request to a senior agent when its deadline is at risk."),
    ("approver", "An approver can be a team lead or a duty manager for a closure."),
]

# A rerank response grading three candidates (ids 0..2) with distinct scores.
_RERANK = '{"scores": [{"id": 0, "score": 9}, {"id": 1, "score": 4}, {"id": 2, "score": 7}]}'


def _lite_retriever(config: RetrievalConfig | None = None) -> Retriever:
    """A databaseless lite retriever over ``_DOCS`` (offline embedder + Chroma :memory:)."""
    backend = InMemoryKnowledgeBackend.from_corpus(docs=_DOCS)
    cache = SemanticCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.985)
    complete = RecordingComplete(_RERANK)
    # Offline deterministic embedder (backend default): pass it as the pipeline embedder
    # too so the cache's query vector is dimension-consistent with recall.
    from aegis.retrieval.memory import _default_offline_embed

    return Retriever(
        backend=backend,
        cache=cache,
        complete=complete,
        embed=_default_offline_embed,
        config=config or RetrievalConfig(recall_top_k=8, final_top_k=3),
    )


def _payloads(frames: list[str]) -> list[dict]:
    return [json.loads(f[len("data: ") :].strip()) for f in frames]


class CaptureSink:
    def __init__(self) -> None:
        self.frames: list[str] = []

    async def __call__(self, frame: str) -> None:
        self.frames.append(frame)


# ── each arm fires offline, with measured counts ──────────────────────────────


@pytest.mark.asyncio
async def test_all_three_recall_arms_fire_offline():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    arms = {tuple(a.origins): a for a in result.observability.arms}
    # Vector, graph, and bm25 are each present as their own arm.
    assert (RetrievalOrigin.VECTOR,) in arms
    assert (RetrievalOrigin.GRAPH,) in arms
    assert (RetrievalOrigin.BM25,) in arms


@pytest.mark.asyncio
async def test_arm_candidate_counts_are_measured_and_positive():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    for arm in result.observability.arms:
        # A fired arm reports a real, positive candidate count == fired flag.
        assert arm.fired is (arm.candidates > 0)
    assert any(a.fired for a in result.observability.arms)


@pytest.mark.asyncio
async def test_vector_arm_fires_for_lite_backend():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    vector = next(a for a in result.observability.arms if a.origins == [RetrievalOrigin.VECTOR])
    assert vector.fired is True
    assert vector.candidates > 0


@pytest.mark.asyncio
async def test_bm25_arm_reports_zero_when_no_keyword_overlap():
    # A query with no shared tokens against the corpus → the BM25 arm honestly fires nothing.
    result = await _lite_retriever().retrieve("zzz qqq wxyz", scope=_SCOPE)
    bm25 = next(a for a in result.observability.arms if a.origins == [RetrievalOrigin.BM25])
    assert bm25.candidates == 0
    assert bm25.fired is False


# ── fusion is RRF and reported ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fusion_is_rrf_and_reported_in_observability_and_provenance():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    assert result.observability.fusion is FusionMethod.RRF
    assert result.provenance.fusion is FusionMethod.RRF
    # fused_candidates is the honest wide-recall pool size N (>= surviving sources K).
    assert result.observability.fused_candidates == result.num_candidates
    assert result.observability.fused_candidates >= len(result.sources)


# ── rerank observability + knob ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rerank_runs_and_reports_top_scores_when_enabled():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    rr = result.observability.rerank
    assert rr.ran is True
    assert rr.kept == len(result.sources)
    assert rr.input_candidates == result.num_candidates
    # The reported top scores ARE the survivors' rerank grades (from _RERANK), in order.
    assert rr.top_scores == [s.score for s in result.sources]
    assert set(rr.top_scores) <= {9.0, 7.0, 4.0}


@pytest.mark.asyncio
async def test_rerank_top_scores_are_descending_grades():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    scores = result.observability.rerank.top_scores
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_rerank_knob_off_skips_model_call_and_keeps_fused_order():
    config = RetrievalConfig(recall_top_k=8, final_top_k=3, rerank_enabled=False)
    retriever = _lite_retriever(config)
    result = await retriever.retrieve("closure original approver", scope=_SCOPE)

    rr = result.observability.rerank
    assert rr.ran is False
    # No rerank grade → the kept scores are the fused RRF scores (small positive floats),
    # never the 0-10 grades from _RERANK.
    assert all(0.0 < s < 1.0 for s in rr.top_scores)
    # The behaviour actually changed: the rerank model was NEVER called.
    assert retriever.complete.calls == []


@pytest.mark.asyncio
async def test_rerank_on_vs_off_changes_source_ordering_signal():
    on = await _lite_retriever(
        RetrievalConfig(recall_top_k=8, final_top_k=3, rerank_enabled=True)
    ).retrieve("closure original approver", scope=_SCOPE)
    off = await _lite_retriever(
        RetrievalConfig(recall_top_k=8, final_top_k=3, rerank_enabled=False)
    ).retrieve("closure original approver", scope=_SCOPE)
    # Rerank-on scores come from the LLM grades; rerank-off from RRF — observably different.
    assert on.observability.rerank.ran and not off.observability.rerank.ran
    assert max(on.observability.rerank.top_scores) >= 1.0
    assert max(off.observability.rerank.top_scores) < 1.0


# ── spotlight observability + knob ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_spotlight_applied_by_default():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    assert result.observability.spotlight_applied is True
    assert DATAMARK_TOKEN in result.answer_context


@pytest.mark.asyncio
async def test_spotlight_knob_off_changes_context_and_flag():
    config = RetrievalConfig(recall_top_k=8, final_top_k=3, spotlight_enabled=False)
    result = await _lite_retriever(config).retrieve("closure original approver", scope=_SCOPE)
    assert result.observability.spotlight_applied is False
    # The datamarking token is gone → the injection-defence layer was really omitted.
    assert DATAMARK_TOKEN not in result.answer_context
    # But the answer context is still assembled from the sources.
    assert result.answer_context


@pytest.mark.asyncio
async def test_spotlight_applied_false_when_no_sources():
    # A query with a degenerate embedding + no keyword overlap → no sources → nothing to spotlight.
    result = await _lite_retriever().retrieve("zzz qqq wxyz", scope=_SCOPE)
    if not result.sources:
        assert result.observability.spotlight_applied is False


# ── query rewrite observability (via the loop wrapper) ────────────────────────


async def _changed_rewrite(query: str, *, history=None) -> RewriteResult:
    """A rewrite_fn that always expands the query (deterministic, offline)."""
    return RewriteResult(
        original=query,
        rewritten=f"{query} expanded standalone",
        changed=True,
        reason="test rewrite",
    )


@pytest.mark.asyncio
async def test_query_rewrite_reported_with_rewritten_query():
    retriever = _lite_retriever()
    judge = RecordingComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')

    out = await agentic_retrieve(
        "closure original approver",
        retrieve_fn=retriever.retrieve,
        complete=judge,
        rewrite_fn=_changed_rewrite,
        max_rounds=2,
        scope=_SCOPE,
    )
    rw = out.result.observability.rewrite
    assert rw is not None
    assert rw.ran is True
    assert rw.changed is True
    assert rw.rewritten == "closure original approver expanded standalone"
    # The loop actually retrieved with the rewritten query (round 1).
    assert out.rounds[0].query == "closure original approver expanded standalone"


@pytest.mark.asyncio
async def test_rewrite_absent_on_single_shot_retrieve():
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    # A bare retrieve() had no rewrite layer → honestly None (not a fabricated no-op).
    assert result.observability.rewrite is None
    assert result.observability.agentic is None


# ── Self-RAG loop iteration count ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_self_rag_iteration_count_reported_single_round():
    retriever = _lite_retriever()
    judge = RecordingComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')
    out = await agentic_retrieve(
        "closure original approver",
        retrieve_fn=retriever.retrieve,
        complete=judge,
        max_rounds=3,
        scope=_SCOPE,
    )
    ag = out.result.observability.agentic
    assert ag is not None
    assert ag.ran is True
    assert ag.used_rounds == 1  # judged sufficient on round 1
    assert ag.max_rounds == 3
    assert ag.round_queries == ["closure original approver"]


@pytest.mark.asyncio
async def test_self_rag_iterates_and_reports_multiple_rounds():
    retriever = _lite_retriever()
    # Always insufficient → the loop iterates until max_rounds.
    judge = RecordingComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": "senior agent deadline"}'
    )
    out = await agentic_retrieve(
        "closure original approver",
        retrieve_fn=retriever.retrieve,
        complete=judge,
        max_rounds=2,
        scope=_SCOPE,
    )
    ag = out.result.observability.agentic
    assert ag.used_rounds == 2
    assert out.used_rounds == 2
    assert len(ag.round_queries) == 2


# ── stream surfaces the full observability payload ────────────────────────────


@pytest.mark.asyncio
async def test_stream_emits_observability_payload():
    retriever = _lite_retriever()
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    result = await stream_retrieve(retriever, "closure original approver", emitter, scope=_SCOPE)

    citations = next(
        p["value"] for p in _payloads(sink.frames)
        if p.get("name") == stream_names.RETRIEVAL_CITATIONS
    )
    obs = citations["observability"]
    assert obs["fusion"] == "rrf"
    assert obs["fused_candidates"] == result.num_candidates
    assert obs["rerank"]["ran"] is True
    assert obs["rerank"]["top_scores"] == [s.score for s in result.sources]
    assert obs["spotlight_applied"] is True
    # Arms are present with per-arm origins + counts.
    arm_origins = {tuple(a["origins"]) for a in obs["arms"]}
    assert ("vector",) in arm_origins
    assert ("graph",) in arm_origins
    assert ("bm25",) in arm_origins
    # Single-shot stream → no rewrite / agentic layer.
    assert obs["rewrite"] is None
    assert obs["agentic"] is None


@pytest.mark.asyncio
async def test_stream_observability_arm_counts_match_result():
    retriever = _lite_retriever()
    sink = CaptureSink()
    emitter = AegisEmitter(thread_id="t", run_id="r", sink=sink)
    result = await stream_retrieve(retriever, "closure original approver", emitter, scope=_SCOPE)

    citations = next(
        p["value"] for p in _payloads(sink.frames)
        if p.get("name") == stream_names.RETRIEVAL_CITATIONS
    )
    streamed = {tuple(a["origins"]): a["candidates"] for a in citations["observability"]["arms"]}
    measured = {tuple(o.value for o in a.origins): a.candidates for a in result.observability.arms}
    assert streamed == measured


# ── defaults / no-op safety ───────────────────────────────────────────────────


def test_observability_defaults_are_empty_and_unaffecting():
    result = RetrievalResult(answer_context="ctx")
    obs = result.observability
    assert obs.arms == []
    assert obs.fusion is FusionMethod.NONE
    assert obs.fused_candidates == 0
    assert obs.rerank.ran is False
    assert obs.spotlight_applied is False
    assert obs.rewrite is None
    assert obs.agentic is None


@pytest.mark.asyncio
async def test_knobs_untouched_preserve_existing_behaviour():
    # With default config, rerank + spotlight are ON (unchanged from pre-observability).
    result = await _lite_retriever().retrieve("closure original approver", scope=_SCOPE)
    assert result.observability.rerank.ran is True
    assert result.observability.spotlight_applied is True
    assert result.provenance.fusion is FusionMethod.RRF


@pytest.mark.asyncio
async def test_observability_round_trips_through_cache():
    retriever = _lite_retriever()
    first = await retriever.retrieve(
        "closure original approver", scope=RetrievalScope(tenant_id=None, persona="p")
    )
    assert first.cache_hit is False
    # Exact repeat → served from cache; the ORIGINAL retrieval's observability is preserved
    # (consistent with how num_candidates / provenance round-trip through the cache).
    cached = await retriever.retrieve(
        "closure original approver", scope=RetrievalScope(tenant_id=None, persona="p")
    )
    assert cached.cache_hit is True
    assert cached.observability.fusion is FusionMethod.RRF
    assert cached.observability.fused_candidates == first.observability.fused_candidates
