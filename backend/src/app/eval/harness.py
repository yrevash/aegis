"""The offline eval runner + the CI quality gate thresholds.

:func:`evaluate` drives the **real** hybrid retrieval pipeline
(:class:`~app.retrieval.pipeline.Retriever` over the databaseless
:class:`~app.retrieval.memory.InMemoryKnowledgeBackend`) across the fixed
:data:`~app.eval.corpus.SEED_CASES`, scores each with the deterministic
:mod:`~app.eval.metrics`, and returns an :class:`EvalReport` whose ``passed`` flag is
what the CI gate asserts. It is fully offline: the embedding is a local deterministic
hash and the reranker is a pass-through (so the RRF-fused order is what is measured).

Run it directly for a human-readable report::

    python -m app.eval.harness
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from app.core.llm import LLMResult, Usage
from app.retrieval.cache import SemanticCache
from app.retrieval.memory import InMemoryKnowledgeBackend, InMemoryRedis, _local_embed
from app.retrieval.pipeline import RetrievalConfig, Retriever

from .corpus import SEED_CASES, EvalCase, corpus_chunks
from .judge import JudgeSummary, JudgeVerdict, judge_answer, summarize_verdicts
from .metrics import AggregateScore, CaseScore, aggregate, score_case

#: A chat-completion callable (matching :func:`app.core.llm.complete`'s shape) that the
#: LLM-as-judge is driven through. When ``evaluate`` is given one, the judge runs; when
#: it is ``None`` (the default, offline path) the judge is skipped and no model is called.
CompleteFn = Callable[..., Awaitable[LLMResult]]


@dataclass(frozen=True)
class EvalThresholds:
    """Minimum corpus-level means the retrieval path must clear to pass the gate.

    Attributes:
        min_context_precision: Floor for mean context precision@k (top-result relevance
            at ``precision_k``).
        min_context_recall: Floor for mean context recall (gold docs surfaced at all).
        min_groundedness: Floor for mean groundedness proxy.
        precision_k: The ``k`` cut-off used for context precision (``1`` = is the
            top-ranked retrieved passage the relevant one).
    """

    min_context_precision: float
    min_context_recall: float
    min_groundedness: float
    precision_k: int = 1


#: The bar the CI gate enforces. Set conservatively **below** the seed corpus's
#: observed scores so normal runs pass, but high enough that a real regression in
#: fusion, assembly, or the corpus mapping trips it. Precision is measured @1 (the
#: fused, reranked top result) because the cases are single-gold.
DEFAULT_THRESHOLDS = EvalThresholds(
    min_context_precision=0.66,
    min_context_recall=0.95,
    min_groundedness=0.85,
    precision_k=1,
)


@dataclass(frozen=True)
class EvalReport:
    """The outcome of an eval run: per-case scores, aggregate, and pass/fail.

    Attributes:
        cases: The per-case scores.
        aggregate: The corpus-level means of the deterministic proxies.
        thresholds: The bar this report was judged against.
        passed: Whether every aggregate metric cleared its threshold.
        judge: The corpus-level LLM-as-judge summary (model-graded groundedness +
            relevance) when :func:`evaluate` was given a ``complete`` callable;
            ``None`` on the default offline path where no model is called.
    """

    cases: tuple[CaseScore, ...]
    aggregate: AggregateScore
    thresholds: EvalThresholds
    passed: bool
    judge: JudgeSummary | None = None

    def failures(self) -> list[str]:
        """Return a human-readable reason for each unmet threshold (empty if passed)."""
        agg, thr = self.aggregate, self.thresholds
        reasons: list[str] = []
        if agg.context_precision < thr.min_context_precision:
            reasons.append(
                f"context_precision {agg.context_precision:.3f} < "
                f"{thr.min_context_precision:.3f}"
            )
        if agg.context_recall < thr.min_context_recall:
            reasons.append(
                f"context_recall {agg.context_recall:.3f} < {thr.min_context_recall:.3f}"
            )
        if agg.groundedness < thr.min_groundedness:
            reasons.append(
                f"groundedness {agg.groundedness:.3f} < {thr.min_groundedness:.3f}"
            )
        return reasons


async def _fake_complete(
    role: object,
    messages: list[dict],
    *,
    tools: list[dict] | None = None,
    temperature: float = 0.0,
    response_format: dict | None = None,
) -> LLMResult:
    """A pass-through reranker: return empty content so rerank keeps the RRF order.

    The reranker falls back to recall order on an unparseable response, so returning
    empty content makes the fused Reciprocal-Rank-Fusion ordering the thing measured —
    deterministic and offline, with no gateway call.
    """
    return LLMResult(content="", usage=Usage())


async def _fake_embed(texts: list[str]) -> list[list[float]]:
    """Deterministic local embedding for the cache probe (offline, no gateway)."""
    return [_local_embed(text) for text in texts]


def build_eval_retriever() -> Retriever:
    """Build the offline hybrid retriever over the seed corpus.

    Uses the production :class:`~app.retrieval.pipeline.Retriever` and the real
    :class:`~app.retrieval.memory.InMemoryKnowledgeBackend` (genuine vector + graph +
    BM25 recall fused by RRF); only the embedding and reranker are deterministic local
    fakes so the run needs no network.
    """
    backend = InMemoryKnowledgeBackend(corpus_chunks())
    cache = SemanticCache(InMemoryRedis(), similarity_threshold=0.99)
    return Retriever(
        backend=backend,
        cache=cache,
        complete=_fake_complete,
        embed=_fake_embed,
        config=RetrievalConfig(),
    )


async def evaluate(
    cases: tuple[EvalCase, ...] = SEED_CASES,
    thresholds: EvalThresholds = DEFAULT_THRESHOLDS,
    *,
    complete: CompleteFn | None = None,
) -> EvalReport:
    """Run the deterministic eval over ``cases`` and judge it against ``thresholds``.

    The deterministic proxies (:mod:`app.eval.metrics`) are always computed and are what
    ``passed`` gates on. When a ``complete`` callable is supplied, the optional
    **LLM-as-judge** (:func:`app.eval.judge.judge_answer`) additionally grades each case's
    retrieved context for model-graded groundedness + relevance, and the corpus-level
    :class:`~app.eval.judge.JudgeSummary` is surfaced on the report. With no ``complete``
    (the default offline path) the judge is skipped and ``report.judge`` is ``None`` — so
    the gate degrades gracefully and never touches the network.

    Args:
        cases: The labelled eval cases (defaults to the seed corpus cases).
        thresholds: The quality bar to judge the aggregate against.
        complete: Optional chat-completion callable (shape of
            :func:`app.core.llm.complete`); when given, the LLM-as-judge runs through it.

    Returns:
        A fully-populated :class:`EvalReport` (with ``judge`` populated iff ``complete``
        was supplied).
    """
    scores: list[CaseScore] = []
    verdicts: list[JudgeVerdict] = []
    for case in cases:
        # A fresh cache per case so an earlier query can never semantic-hit a later one.
        retriever = build_eval_retriever()
        result = await retriever.retrieve(case.query)
        scores.append(score_case(case, result, precision_k=thresholds.precision_k))
        if complete is not None:
            # Model-graded pass: grade the retrieved context as the answer-under-test
            # (groundedness is then trivially high; relevance measures whether the
            # surfaced context actually addresses the query).
            verdicts.append(
                await judge_answer(
                    case.query,
                    result.answer_context,
                    result.answer_context,
                    complete=complete,
                )
            )

    agg = aggregate(scores)
    passed = (
        agg.context_precision >= thresholds.min_context_precision
        and agg.context_recall >= thresholds.min_context_recall
        and agg.groundedness >= thresholds.min_groundedness
    )
    judge = summarize_verdicts(verdicts) if complete is not None else None
    return EvalReport(
        cases=tuple(scores),
        aggregate=agg,
        thresholds=thresholds,
        passed=passed,
        judge=judge,
    )


def _main() -> int:
    """Run the eval and print a human-readable report; return a POSIX exit code."""
    import asyncio

    report = asyncio.run(evaluate())
    agg = report.aggregate
    print(f"Eval over {agg.cases} cases:")  # noqa: T201 - CLI output
    print(  # noqa: T201
        f"  context_precision@{report.thresholds.precision_k} = "
        f"{agg.context_precision:.3f}  (>= {report.thresholds.min_context_precision})"
    )
    print(  # noqa: T201
        f"  context_recall              = {agg.context_recall:.3f}  "
        f"(>= {report.thresholds.min_context_recall})"
    )
    print(  # noqa: T201
        f"  groundedness                = {agg.groundedness:.3f}  "
        f"(>= {report.thresholds.min_groundedness})"
    )
    if report.passed:
        print("PASS")  # noqa: T201
        return 0
    print("FAIL: " + "; ".join(report.failures()))  # noqa: T201
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
