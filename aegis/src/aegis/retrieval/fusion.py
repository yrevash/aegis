"""Reciprocal Rank Fusion (RRF) for hybrid retrieval (§4.3).

The SOTA hybrid pipeline retrieves *wide* from several retrievers (dense vector ANN,
graph traversal, BM25/keyword) and fuses their ranked lists into one before reranking.
`docs/architecture/system-architecture.md` locks RRF as the fusion method: it needs only the
*rank* of a document in each list, so it is robust to the incomparable score scales of a
cosine similarity, a graph-proximity score, and a BM25 score — no fragile weighting.

RRF score of a document ``d``::

    score(d) = Σ_i  1 / (k + rank_i(d))          # rank_i is 1-based; k ≈ 60

Every surviving candidate is tagged (in ``metadata["origins"]``) with the
:class:`~aegis.retrieval.types.RetrievalOrigin` of each list that contributed to it, so the
pipeline can populate honest provenance and the UI can show "vector+graph fused via RRF".

This module is pure (no I/O, no gateway) and shared by *both* the production LightRAG
path and the databaseless lite path so they use one identical fusion core.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from aegis.retrieval.models import Candidate
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalOrigin

#: Metadata key under which a fused candidate records its contributing origins.
ORIGIN_METADATA_KEY = "origins"

#: Canonical display order for origins (stable, deterministic provenance).
_ORIGIN_ORDER: dict[RetrievalOrigin, int] = {
    RetrievalOrigin.VECTOR: 0,
    RetrievalOrigin.GRAPH: 1,
    RetrievalOrigin.BM25: 2,
    RetrievalOrigin.CACHE: 3,
}


@dataclass(frozen=True)
class RankedList:
    """One retriever's ranked candidate list, tagged with its origin(s).

    Attributes:
        origins: The retrieval origin(s) this list represents. A single-signal list
            carries one origin (e.g. ``(BM25,)``); a pre-blended list (e.g. LightRAG
            ``mix``) may carry several (e.g. ``(VECTOR, GRAPH)``). An **empty** tuple is
            meaningful and deliberate: the list re-orders candidates other arms already
            recalled and therefore claims no origin of its own (it fuses, but it never
            appears in provenance as a source of recall — see the pool-scoped keyword
            pass in :meth:`aegis.retrieval.pipeline.Retriever._keyword_signal`).
        candidates: Candidates in descending relevance order (best first).
    """

    origins: tuple[RetrievalOrigin, ...]
    candidates: Sequence[Candidate]


@dataclass
class RankedRecall:
    """A backend's wide recall exposed as multiple origin-tagged ranked lists.

    Returned by backends that can split their recall into separate signals (so RRF
    genuinely fuses them), alongside the graph slice touched for the live viz.
    """

    lists: list[RankedList] = field(default_factory=list)
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[RankedList], *, k: int = 60
) -> list[Candidate]:
    """Fuse several ranked candidate lists into one via Reciprocal Rank Fusion.

    Candidates are merged by their ``id``: a candidate appearing in multiple lists
    accumulates one ``1 / (k + rank)`` term per appearance and unions the origins of
    every list it appeared in. The representative text/metadata is taken from the
    candidate's first appearance; ``score`` becomes its fused RRF score and
    ``metadata[ORIGIN_METADATA_KEY]`` records its contributing origins.

    Args:
        ranked_lists: One :class:`RankedList` per retriever, each already in
            descending relevance order.
        k: The RRF damping constant (higher ``k`` flattens the rank weighting).
            Must be positive; ``60`` is the community default.

    Returns:
        A single list of candidates in descending fused-score order. Ties are broken
        deterministically by first-appearance order across the input lists.

    Raises:
        ValueError: If ``k`` is not positive.
    """
    if k <= 0:
        raise ValueError("RRF constant k must be positive")

    scores: dict[str, float] = {}
    origins: dict[str, list[RetrievalOrigin]] = {}
    reps: dict[str, Candidate] = {}
    order: list[str] = []

    for ranked in ranked_lists:
        for rank, cand in enumerate(ranked.candidates, start=1):
            cid = cand.id
            if cid not in reps:
                reps[cid] = cand
                scores[cid] = 0.0
                origins[cid] = []
                order.append(cid)
            scores[cid] += 1.0 / (k + rank)
            for origin in ranked.origins:
                if origin not in origins[cid]:
                    origins[cid].append(origin)

    first_seen = {cid: i for i, cid in enumerate(order)}
    ranked_ids = sorted(order, key=lambda cid: (-scores[cid], first_seen[cid]))

    fused: list[Candidate] = []
    for cid in ranked_ids:
        cand = reps[cid]
        ordered = sorted(origins[cid], key=lambda o: _ORIGIN_ORDER.get(o, 99))
        metadata = {**cand.metadata, ORIGIN_METADATA_KEY: [o.value for o in ordered]}
        fused.append(cand.model_copy(update={"score": scores[cid], "metadata": metadata}))
    return fused


def order_origins(origins: Iterable[RetrievalOrigin]) -> list[RetrievalOrigin]:
    """Return the distinct ``origins`` in canonical (vector → graph → bm25 → cache) order.

    The single ordering used everywhere provenance is built or merged (the pipeline's
    per-result origins, the agentic loop's union across rounds), so two results that
    contributed the same signals always report them identically.

    Args:
        origins: Any iterable of origins, possibly with duplicates.

    Returns:
        The distinct origins in canonical display order.
    """
    seen: dict[RetrievalOrigin, None] = {}
    for origin in origins:
        seen.setdefault(origin, None)
    return sorted(seen, key=lambda o: _ORIGIN_ORDER.get(o, 99))


def collect_origins(candidates: Iterable[Candidate]) -> list[RetrievalOrigin]:
    """Aggregate the distinct contributing origins across fused candidates.

    Reads the per-candidate ``metadata[ORIGIN_METADATA_KEY]`` tags written by
    :func:`reciprocal_rank_fusion` and returns the union, in canonical order — the
    honest ``provenance.origins`` for the whole result (only origins that actually
    produced a surviving candidate appear).

    Args:
        candidates: The fused candidate pool.

    Returns:
        The distinct origins, ordered vector → graph → bm25 → cache.
    """
    found: list[RetrievalOrigin] = []
    for cand in candidates:
        for raw in cand.metadata.get(ORIGIN_METADATA_KEY, []):
            try:
                found.append(RetrievalOrigin(raw))
            except ValueError:
                continue
    return order_origins(found)
