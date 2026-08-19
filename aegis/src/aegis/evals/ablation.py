"""The ablation: an additive ladder from a competent naive baseline to the shipped path.

## The arms

| arm | what changes |
|---|---|
| **A0** naive | the PDF's text layer, fixed word windows, dense arm only, no rerank |
| **A1** + structure | the layout-aware parse and :func:`~aegis.retrieval.chunker.chunk_sections` |
| **A2** + prefix | the D7 chunk prefix (title · type · date · heading path), embedded |
| **A3** + hybrid | vector + graph + corpus-wide BM25, fused by RRF |
| **A4** shipped | A3 plus the local ONNX cross-encoder rerank |
| **L1** | A4 with the graph arm removed |
| **L2** | A4 with the BM25 arm removed |

The ladder answers *"what did the work buy?"*. The two leave-one-out probes answer the
harder question — *"does every piece earn its place?"* — and they are here because being
willing to publish a probe showing an arm contributes nothing is worth more than a clean
table.

## The two rules that keep A0 honest

1. **A0 uses the same embedder as every other arm.** Swapping the embedder between arms
   attributes the embedder's quality to our pipeline, and it is the most common way an
   ablation table lies. Every arm here embeds with whatever :class:`AblationRun` was
   constructed with — there is no per-arm embedder to get wrong.
2. **A0 is what a competent team ships in a weekend**, not a strawman. It reads the text
   layer with PDFium (the naive `pypdf.extract_text()` equivalent), packs 400-word
   windows **with the same 60-word overlap the shipped chunker uses**, embeds them with
   the same model, and retrieves top-k by vector similarity.

   *That overlap is a deliberate deviation from `research/eval-design.md` §4.1, which
   specifies "no overlap".* Zero-overlap windows lose an answer sentence whenever it
   straddles a window boundary, which would credit our pipeline for an artefact of the
   baseline's chunk arithmetic rather than for structure. The stronger baseline is the
   one worth beating.

## What this measures, and what it does not

The corpus is held by :class:`~aegis.retrieval.memory.InMemoryKnowledgeBackend` — a real
in-process Qdrant vector search, a real corpus-wide BM25, and a co-occurrence graph
expansion — which is the **lite** shipped configuration, not the LightRAG/Neo4j one. The
graph arm measured here is therefore co-occurrence expansion, and a leave-one-out result
for it is a result about *that* arm. Said plainly rather than left to be discovered.

Entity extraction is deliberately switched off (:class:`NoOpExtractor`): it populates the
graph *slice* the visualiser animates, not the graph *list* that is fused, so running it
would cost minutes of spaCy over thousands of chunks and change no number here.

## Why the pool, and not ``Retriever.retrieve``

Every number in the table needs the ranked pool **20 deep**, and
:meth:`~aegis.retrieval.pipeline.Retriever.retrieve` returns the ``final_top_k``
survivors. So this module assembles the same stages the pipeline does — the backend's own
recall lists, the same :func:`~aegis.retrieval.fusion.reciprocal_rank_fusion`, the same
:class:`~aegis.retrieval.local_reranker.LocalCrossEncoderReranker` — and keeps the whole
pool. That it *is* the same path is not asserted in prose: ``test_ablation.py`` runs a
query through both and requires the shipped retriever's top-k to equal arm A3's, so the
two cannot drift apart silently.

One consequence worth stating before anyone reads the table: **A4's recall@20 is A3's
recall@20 by construction**, because reranking reorders the pool and cannot add to it.
That is precisely why the recall@20 → recall@6 gap is the reranker's contribution
isolated, and it is a property of the design, not a coincidence in the data.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol

from aegis.retrieval.fusion import RankedList, reciprocal_rank_fusion
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import Candidate
from aegis.retrieval.reranker import RerankOutcome
from aegis.retrieval.types import RetrievalOrigin, RetrievalScope

from .goldset import GoldCase, hit_ranks
from .ir_metrics import (
    BOOTSTRAP_ITERATIONS,
    Interval,
    mcnemar_exact,
    ndcg_at_k,
    paired_bootstrap,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    wilson_interval,
)

#: The pool that reaches the reranker (``RetrievalConfig.recall_top_k``).
RECALL_TOP_K = 20
#: What the generator actually reads (``RetrievalConfig.final_top_k``).
FINAL_TOP_K = 6
#: The cut-off nDCG is reported at, for recognisability.
NDCG_K = 10


class PoolReranker(Protocol):
    """What an arm needs of a reranker: reorder a pool, keep the best ``top_k``.

    Structural rather than nominal so a test can inject a deterministic fake without the
    ONNX weights, and so the eval never reaches for a global.
    :class:`~aegis.retrieval.local_reranker.LocalCrossEncoderReranker` satisfies it.
    """

    def rerank(
        self, query: str, candidates: list[Candidate], *, top_k: int
    ) -> RerankOutcome:
        """Return ``candidates`` reordered by relevance to ``query``, best ``top_k`` kept."""
        ...


class Chunking(StrEnum):
    """How a corpus was cut up — the axis A0→A1→A2 moves along."""

    #: PDFium's text layer, fixed word windows. The naive baseline.
    NAIVE_WINDOWS = "naive_windows"
    #: The layout-aware parse, packed by ``chunk_sections``, no prefix.
    STRUCTURAL = "structural"
    #: The same, with the D7 prefix folded into the embedded text.
    STRUCTURAL_PREFIXED = "structural_prefixed"


class Signal(StrEnum):
    """A recall arm that can be switched on or off."""

    VECTOR = "vector"
    GRAPH = "graph"
    BM25 = "bm25"


@dataclass(frozen=True)
class Arm:
    """One configuration of the retrieval path.

    Attributes:
        name: Short id used in the table (``A0``).
        label: What changed, for the row header.
        chunking: Which corpus this arm reads.
        signals: The recall arms fused for this configuration.
        rerank: Whether the local cross-encoder reorders the pool.
    """

    name: str
    label: str
    chunking: Chunking
    signals: frozenset[Signal]
    rerank: bool


#: The ladder plus the two leave-one-out probes, in report order.
ABLATION_ARMS: tuple[Arm, ...] = (
    Arm("A0", "naive: text layer + fixed windows, dense only",
        Chunking.NAIVE_WINDOWS, frozenset({Signal.VECTOR}), False),
    Arm("A1", "+ layout-aware parse and structural chunking",
        Chunking.STRUCTURAL, frozenset({Signal.VECTOR}), False),
    Arm("A2", "+ enriched chunk prefix (D7)",
        Chunking.STRUCTURAL_PREFIXED, frozenset({Signal.VECTOR}), False),
    Arm("A3", "+ hybrid recall (vector + graph + BM25, RRF)",
        Chunking.STRUCTURAL_PREFIXED,
        frozenset({Signal.VECTOR, Signal.GRAPH, Signal.BM25}), False),
    Arm("A4", "= shipped: A3 + local cross-encoder rerank",
        Chunking.STRUCTURAL_PREFIXED,
        frozenset({Signal.VECTOR, Signal.GRAPH, Signal.BM25}), True),
    Arm("L1", "A4 minus the graph arm",
        Chunking.STRUCTURAL_PREFIXED,
        frozenset({Signal.VECTOR, Signal.BM25}), True),
    Arm("L2", "A4 minus the BM25 arm",
        Chunking.STRUCTURAL_PREFIXED,
        frozenset({Signal.VECTOR, Signal.GRAPH}), True),
)


@dataclass(frozen=True)
class CaseOutcome:
    """One arm's result on one gold case.

    Attributes:
        case_id: The gold case.
        kind: Its :class:`~aegis.evals.goldset.GoldKind`, so subsets can be cut later.
        doc_id: The document the answer lives in.
        ranks: Per required span, the 1-based ranks it was retrieved at.
        recall_20: Fraction of required spans inside the pool.
        recall_6: Fraction inside what the generator reads.
        precision_6: Fraction of the delivered 6 that carry a required span.
        mrr_20: Reciprocal rank of the first hit inside the pool.
        ndcg_10: Position-discounted gain at 10.
    """

    case_id: str
    kind: str
    doc_id: str
    ranks: tuple[tuple[int, ...], ...]
    recall_20: float
    recall_6: float
    precision_6: float
    mrr_20: float
    ndcg_10: float


@dataclass(frozen=True)
class ArmResult:
    """Everything one arm produced, per case and in aggregate.

    Attributes:
        arm: Which configuration ran.
        outcomes: Per-case results, in gold-set order.
        chunks: How many chunks the corpus this arm read was cut into.
        seconds: Wall clock for the arm's queries (not for building its corpus).
    """

    arm: Arm
    outcomes: tuple[CaseOutcome, ...]
    chunks: int
    seconds: float

    def scores(self, metric: str) -> list[float]:
        """Return the per-case values of ``metric``, in case order.

        Args:
            metric: One of the :class:`CaseOutcome` metric field names.

        Returns:
            The per-case list — the input to every paired test.
        """
        return [float(getattr(outcome, metric)) for outcome in self.outcomes]

    def mean(self, metric: str) -> float:
        """Mean of ``metric`` across the graded cases.

        Args:
            metric: A :class:`CaseOutcome` metric field name.

        Returns:
            The mean, or ``0.0`` when the arm graded nothing.
        """
        values = self.scores(metric)
        return sum(values) / len(values) if values else 0.0

    def interval(self, metric: str) -> Interval:
        """Wilson 95% interval for a **binary** metric's mean.

        Args:
            metric: ``recall_20`` / ``recall_6`` — metrics that are 0/1 per case for the
                single-span majority of the set.

        Returns:
            The interval. Multi-span cases contribute a fraction, which is rounded into
            the success count; with five such cases in a set of fifty the effect on the
            interval is below its own width, and reporting an interval that ignores them
            entirely would be the less honest option.
        """
        values = self.scores(metric)
        successes = round(sum(values))
        return wilson_interval(successes, len(values))


@dataclass(frozen=True)
class Comparison:
    """A paired arm-vs-arm delta with its interval and discordant counts.

    Attributes:
        baseline: The arm being improved on.
        arm: The arm under test.
        metric: Which per-case metric was compared.
        delta: Mean paired difference (``arm`` minus ``baseline``).
        interval: Paired-bootstrap 95% interval of that difference.
        favouring_arm: Cases the arm won and the baseline lost.
        favouring_baseline: Cases the baseline won and the arm lost.
        p_value: Exact two-sided McNemar p over the discordant pairs.
    """

    baseline: str
    arm: str
    metric: str
    delta: float
    interval: Interval
    favouring_arm: int
    favouring_baseline: int
    p_value: float

    @property
    def significant(self) -> bool:
        """Whether the 95% interval of the delta excludes zero."""
        return self.interval.low > 0 or self.interval.high < 0


def compare(
    baseline: ArmResult, arm: ArmResult, *, metric: str, seed: int
) -> Comparison:
    """Compare two arms on the same cases, paired.

    Args:
        baseline: The arm being improved on.
        arm: The arm under test.
        metric: A :class:`CaseOutcome` metric field name.
        seed: Bootstrap seed, pinned into the run artifact.

    Returns:
        The :class:`Comparison`.

    Raises:
        ValueError: If the two arms did not run the same cases in the same order — a
            paired test over unpaired data is a wrong number, not an approximation.
    """
    if [o.case_id for o in baseline.outcomes] != [o.case_id for o in arm.outcomes]:
        raise ValueError("paired comparison needs the same cases in the same order")
    left = baseline.scores(metric)
    right = arm.scores(metric)
    deltas = [b - a for a, b in zip(left, right, strict=True)]
    wins = sum(1 for d in deltas if d > 0)
    losses = sum(1 for d in deltas if d < 0)
    return Comparison(
        baseline=baseline.arm.name,
        arm=arm.arm.name,
        metric=metric,
        delta=sum(deltas) / len(deltas) if deltas else 0.0,
        interval=paired_bootstrap(deltas, iterations=BOOTSTRAP_ITERATIONS, seed=seed),
        favouring_arm=wins,
        favouring_baseline=losses,
        p_value=mcnemar_exact(losses, wins),
    )


@dataclass
class AblationRun:
    """Runs the arms over one gold set, reusing a backend per chunking strategy.

    Attributes:
        backends: One :class:`~aegis.retrieval.memory.InMemoryKnowledgeBackend` per
            :class:`Chunking`, each holding that strategy's chunks. Built by the caller,
            because building them means parsing PDFs and this package does not import a
            parser.
        chunk_counts: How many chunks each strategy produced, for the report.
        scope: The retrieval scope every arm runs under.
        reranker: The local cross-encoder. ``None`` makes the reranking arms fail loudly
            rather than quietly measuring an unreranked pool under a reranked name.
        recall_top_k: Pool depth.
        final_top_k: What the generator reads.
    """

    backends: dict[Chunking, InMemoryKnowledgeBackend]
    chunk_counts: dict[Chunking, int]
    scope: RetrievalScope
    reranker: PoolReranker | None = None
    recall_top_k: int = RECALL_TOP_K
    final_top_k: int = FINAL_TOP_K
    _pools: dict[tuple[Chunking, frozenset[Signal], str], list[Candidate]] = field(
        default_factory=dict, init=False, repr=False
    )

    async def _pool(self, arm: Arm, query: str) -> list[Candidate]:
        """Return the fused recall pool for ``query`` under ``arm``, cached.

        The cache is keyed on (corpus, signals, query) rather than on the arm, so A3 and
        A4 — which differ only after fusion — recall once. That is not merely an
        optimisation: it is what makes "the reranker only reorders the pool" true in the
        data as well as in the argument.

        Args:
            arm: The configuration.
            query: The gold question.

        Returns:
            Up to ``recall_top_k`` fused candidates, best first.
        """
        key = (arm.chunking, arm.signals, query)
        cached = self._pools.get(key)
        if cached is not None:
            return cached
        backend = self.backends[arm.chunking]
        recall = await backend.recall_ranked(
            query, top_k=self.recall_top_k, scope=self.scope
        )
        by_origin = {
            RetrievalOrigin.VECTOR: Signal.VECTOR,
            RetrievalOrigin.GRAPH: Signal.GRAPH,
        }
        lists: list[RankedList] = [
            ranked
            for ranked in recall.lists
            if by_origin.get(ranked.origins[0]) in arm.signals
        ]
        if Signal.BM25 in arm.signals:
            hits = await backend.keyword_recall(
                query, top_k=self.recall_top_k, scope=self.scope
            )
            lists.append(RankedList(origins=(RetrievalOrigin.BM25,), candidates=hits))
        if len(lists) == 1:
            pool = list(lists[0].candidates)[: self.recall_top_k]
        else:
            pool = reciprocal_rank_fusion(lists)[: self.recall_top_k]
        self._pools[key] = pool
        return pool

    async def _ranked_texts(self, arm: Arm, query: str) -> list[str]:
        """Return the arm's ranked chunk texts for ``query``, pool-deep.

        Args:
            arm: The configuration.
            query: The gold question.

        Returns:
            The chunk texts in final order. Reranking arms reorder the **whole** pool
            rather than truncating to ``final_top_k``, so recall@20 stays measurable and
            is — correctly — identical to the un-reranked arm's.

        Raises:
            RuntimeError: If the arm reranks and no reranker was injected.
        """
        pool = await self._pool(arm, query)
        if not arm.rerank:
            return [candidate.text for candidate in pool]
        if self.reranker is None:
            raise RuntimeError(
                f"arm {arm.name} reranks but no reranker was injected; refusing to "
                "report an un-reranked pool under a reranked arm's name"
            )
        outcome = self.reranker.rerank(query, list(pool), top_k=len(pool))
        return [candidate.text for candidate in outcome.candidates]

    async def run(self, arm: Arm, cases: Sequence[GoldCase]) -> ArmResult:
        """Run one arm over every gradeable case.

        Args:
            arm: The configuration.
            cases: The gold set. Ungradeable (unanswerable) cases are skipped here —
                they measure refusal, which is an answer-side property.

        Returns:
            The :class:`ArmResult`.
        """
        started = time.perf_counter()
        outcomes: list[CaseOutcome] = []
        for case in cases:
            if not case.gradeable:
                continue
            texts = await self._ranked_texts(arm, case.query)
            ranks = hit_ranks(case, texts)
            outcomes.append(
                CaseOutcome(
                    case_id=case.id,
                    kind=str(case.kind),
                    doc_id=case.doc_id,
                    ranks=ranks,
                    recall_20=recall_at_k(ranks, self.recall_top_k),
                    recall_6=recall_at_k(ranks, self.final_top_k),
                    precision_6=precision_at_k(ranks, self.final_top_k),
                    mrr_20=reciprocal_rank(ranks, self.recall_top_k),
                    ndcg_10=ndcg_at_k(ranks, NDCG_K),
                )
            )
        return ArmResult(
            arm=arm,
            outcomes=tuple(outcomes),
            chunks=self.chunk_counts.get(arm.chunking, 0),
            seconds=time.perf_counter() - started,
        )


def markdown_table(results: Sequence[ArmResult]) -> str:
    """Render the ablation table, every rate carrying its Wilson interval and ``n``.

    Args:
        results: The arm results, in report order.

    Returns:
        A Markdown table.
    """
    header = (
        "| arm | what changed | chunks | recall@20 | recall@6 | precision@6 | MRR@20 | "
        "nDCG@10 |\n|---|---|---:|---|---|---:|---:|---:|"
    )
    rows = []
    for result in results:
        r20, r6 = result.interval("recall_20"), result.interval("recall_6")
        rows.append(
            f"| **{result.arm.name}** | {result.arm.label} | {result.chunks} | "
            f"{result.mean('recall_20'):.3f} ({r20.low:.2f}–{r20.high:.2f}) | "
            f"{result.mean('recall_6'):.3f} ({r6.low:.2f}–{r6.high:.2f}) | "
            f"{result.mean('precision_6'):.3f} | {result.mean('mrr_20'):.3f} | "
            f"{result.mean('ndcg_10'):.3f} |"
        )
    n = results[0].outcomes and len(results[0].outcomes)
    return f"{header}\n" + "\n".join(rows) + f"\n\nn = {n}; intervals are Wilson 95%.\n"
