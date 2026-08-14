"""AG-UI streaming for retrieval — emits its work à la carte over the emitter.

Wraps one `Retriever.retrieve` (or any injected retriever with a compatible
`retrieve(query, *, persona=None) -> RetrievalResult`) call in a
`STEP_STARTED`/`STEP_FINISHED` bracket, emitting the `RETRIEVAL_CITATIONS` custom
event in between so the frontend can render candidates, sources, and provenance as
soon as retrieval finishes.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from aegis.core import stream_names
from aegis.core.events import SpanKind
from aegis.retrieval.models import RetrievalResult

if TYPE_CHECKING:
    from aegis.core.stream import AegisEmitter

_STEP_NAME = "retrieve"


class RetrieveLike(Protocol):
    """Structural type of the retriever `stream_retrieve` drives."""

    async def retrieve(
        self, query: str, *, persona: str | None = None
    ) -> RetrievalResult:
        """Run retrieval for `query` and return a `RetrievalResult`."""
        ...


def _source_label(metadata: dict) -> str | None:
    """Return a display label for a source from its metadata, if one is present."""
    for key in ("title", "section", "doc", "source"):
        value = metadata.get(key)
        if value:
            return str(value)
    return None


def _observability_payload(result: RetrievalResult) -> dict:
    """Serialise the "which arsenal methods ran" record for the citations event.

    Every number here is measured by the pipeline (arm candidate counts, fused pool
    size, rerank scores, iteration count) — the UI renders exactly what ran, never a
    fabricated funnel.
    """
    obs = result.observability
    return {
        "arms": [
            {
                "origins": [o.value for o in arm.origins],
                "candidates": arm.candidates,
                "fired": arm.fired,
            }
            for arm in obs.arms
        ],
        "fusion": obs.fusion.value,
        "fused_candidates": obs.fused_candidates,
        "rerank": {
            "ran": obs.rerank.ran,
            # `graded`/`degraded_reason` travel with the scores they qualify: a rerank
            # call that ran but could not grade leaves fused RRF scores in `top_scores`,
            # and the UI must be able to tell those from real relevance grades.
            "graded": obs.rerank.graded,
            "input_candidates": obs.rerank.input_candidates,
            "kept": obs.rerank.kept,
            "ungraded": obs.rerank.ungraded,
            "degraded_reason": obs.rerank.degraded_reason,
            "top_scores": obs.rerank.top_scores,
        },
        "keyword": {
            "ran": obs.keyword.ran,
            "scope": obs.keyword.scope,
            "matched": obs.keyword.matched,
            "adds_recall": obs.keyword.adds_recall,
        },
        "spotlight_applied": obs.spotlight_applied,
        "rewrite": (
            {
                "ran": obs.rewrite.ran,
                "changed": obs.rewrite.changed,
                "rewritten": obs.rewrite.rewritten,
            }
            if obs.rewrite is not None
            else None
        ),
        "agentic": (
            {
                "ran": obs.agentic.ran,
                "used_rounds": obs.agentic.used_rounds,
                "max_rounds": obs.agentic.max_rounds,
                "round_queries": obs.agentic.round_queries,
                "round_new_sources": obs.agentic.round_new_sources,
            }
            if obs.agentic is not None
            else None
        ),
    }


async def stream_retrieve(
    retriever: RetrieveLike,
    query: str,
    emitter: AegisEmitter,
    *,
    persona: str | None = None,
) -> RetrievalResult:
    """Retrieve for `query`, streaming the citations evidence over `emitter`.

    Emits `STEP_STARTED("retrieve")` → `CUSTOM(retrieval_citations)` →
    `STEP_FINISHED("retrieve")`, bracketing one call to `retriever.retrieve`.

    Args:
        retriever: Anything satisfying `RetrieveLike` (typically a
            :class:`~aegis.retrieval.pipeline.Retriever`).
        query: The user query.
        emitter: The AG-UI emitter for streaming events.
        persona: Optional adapter persona id, forwarded to `retriever.retrieve`.

    Returns:
        The full :class:`~aegis.retrieval.models.RetrievalResult`.
    """
    async with emitter.step(_STEP_NAME, SpanKind.RETRIEVER):
        result = await retriever.retrieve(query, persona=persona)

        # Cache observability: the pipeline has already decided hit vs miss (a served
        # result carries `cache_hit=True` + `provenance.cache`). Surface it as its own
        # event, carrying the CacheProvenance (near-exact `cache-exact` vs semantic
        # `cache-near`, original query, cached-at) so the UI can render the cache story
        # without inferring it from the citations payload. This adds no caching logic —
        # it only reports what `Retriever.retrieve` already resolved.
        cache = result.provenance.cache
        await emitter.custom(
            stream_names.RETRIEVAL_CACHE,
            {
                "event": "hit" if result.cache_hit else "miss",
                "kind": cache.kind if cache is not None else None,
                "original_query": cache.original_query if cache is not None else None,
                "cached_at": cache.cached_at if cache is not None else None,
            },
        )

        await emitter.custom(
            stream_names.RETRIEVAL_CITATIONS,
            {
                "num_candidates": result.num_candidates,
                "sources": [
                    {
                        "id": s.id,
                        "label": _source_label(s.metadata) or s.id,
                        "score": s.score,
                        "origin": s.metadata.get("origins", []),
                        "snippet": s.text[:280],
                    }
                    for s in result.sources
                ],
                "cache_hit": result.cache_hit,
                "provenance": {
                    "origins": [o.value for o in result.provenance.origins],
                    "fusion": result.provenance.fusion.value,
                    "cache_kind": (
                        result.provenance.cache.kind
                        if result.provenance.cache is not None
                        else None
                    ),
                },
                "graph_delta": {
                    "nodes": [
                        {"id": n.id, "label": n.label, "kind": n.kind}
                        for n in result.graph_delta.nodes
                    ],
                    "edges": [
                        {"source": e.source, "target": e.target, "relation": e.relation}
                        for e in result.graph_delta.edges
                    ],
                },
                "observability": _observability_payload(result),
            },
        )
    return result
