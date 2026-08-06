"""The **DeepEval-pattern** CI regression gate — pytest-native, per-metric thresholds.

This is a native implementation of the *DeepEval pattern*: a declarative,
per-metric-threshold regression gate you assert in a normal pytest run (mirroring
``deepeval``'s ``assert_test`` / ``@pytest.mark.parametrize`` style), sitting alongside
the RAGAS-style aggregate gate in :mod:`app.eval.harness`. It adds two things that matter
for a *DeepEval-shaped* suite:

- **Declarative metrics with per-metric thresholds** (:class:`Metric`) — each metric
  carries its own pass bar and ``higher_is_better`` direction, and each is evaluated to a
  :class:`MetricResult`. A :class:`GateCaseResult` bundles the metrics for one test case;
  the whole run is a :class:`RegressionReport` whose ``passed`` is the CI gate.
- **An agentic / tool-use eval case** — not just RAG. It asserts the real supervisor
  router (:func:`app.agent.router.route_query`) selects the expected specialist role for
  representative queries (a memory-recall phrasing → ``memory``; a factual question →
  ``qa``), scored as the ``tool_selection_accuracy`` metric. This is agent-behavior
  regression testing (does the agent still pick the right tool/role?), which pure
  retrieval metrics do not cover.

**Why native, not the ``deepeval`` package.** ``deepeval`` is heavy and, for most of its
metrics, calls an external LLM judge — which makes it slow, non-deterministic, and
network-dependent. This gate must run in CI with no infra, no keys, and no network, so it
implements the *pattern* natively on top of the existing harness: it drives the **real**
hybrid retriever (:func:`app.eval.harness.build_eval_retriever`) over the fixed
:data:`app.eval.corpus.SEED_CASES` with a deterministic local embedding and a pass-through
reranker (offline), and computes the retrieval metrics with the real, deterministic
:func:`app.eval.metrics.score_case`. **No scores are hardcoded** — every number comes from
a real retrieval or a real router decision.

**Dropping in real DeepEval later.** Because the metric layer is declarative, real
DeepEval is a drop-in *backend*: a ``deepeval`` ``LLMTestCase`` maps 1:1 to a
:class:`GateCaseResult`, and each :class:`Metric` here maps to a ``deepeval`` metric
(``ContextualPrecisionMetric``, ``ContextualRecallMetric``, ``FaithfulnessMetric``, and a
``ToolCorrectnessMetric`` for the agentic case). To adopt it, keep this module's
``run_*`` orchestration and swap the scoring calls for ``deepeval``'s — the thresholds,
the report shape, and the pytest gate stay identical. Until then this stays offline/CI-fast.

Run it directly for a human-readable report and a POSIX exit code::

    python -m app.eval.regression
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from app.agent.router import load_roster, route_query

from .corpus import SEED_CASES
from .harness import build_eval_retriever
from .metrics import aggregate, score_case

#: A chat-completion callable (shape of :func:`app.core.llm.complete`). When ``None``
#: (the default, offline path) the retriever's reranker falls back to RRF order and the
#: router uses its deterministic classifier — no model is called.
CompleteFn = Callable[..., Awaitable[object]]


# ── Declarative metric API (the DeepEval shape) ───────────────────────────────
@dataclass(frozen=True)
class Metric:
    """A declarative metric with its own pass threshold and direction.

    Attributes:
        name: Stable metric id (also the lookup key when overriding thresholds).
        threshold: The pass bar for this metric.
        higher_is_better: ``True`` (default) means ``value >= threshold`` passes; ``False``
            (e.g. a latency/cost metric) means ``value <= threshold`` passes.
    """

    name: str
    threshold: float
    higher_is_better: bool = True

    def evaluate(self, value: float) -> MetricResult:
        """Score a measured ``value`` against this metric's threshold + direction."""
        passed = value >= self.threshold if self.higher_is_better else value <= self.threshold
        return MetricResult(name=self.name, value=value, threshold=self.threshold, passed=passed)


@dataclass(frozen=True)
class MetricResult:
    """One metric measured for one case, with its pass/fail verdict."""

    name: str
    value: float
    threshold: float
    passed: bool


@dataclass(frozen=True)
class GateCaseResult:
    """A single test case (RAG or agentic) evaluated against one or more metrics."""

    name: str
    metrics: list[MetricResult]
    passed: bool


@dataclass(frozen=True)
class RegressionReport:
    """The outcome of a regression-gate run: per-case results + the overall gate flag."""

    cases: list[GateCaseResult]
    passed: bool

    def failures(self) -> list[GateCaseResult]:
        """Return only the failing cases (empty when the gate passed)."""
        return [case for case in self.cases if not case.passed]


# ── DeepEval-style default metrics (thresholds set conservatively below observed) ──
#: Context precision@1 — the *ranking* metric. On single-gold cases it is binary
#: per case, so it is gated at the **corpus** level (the fraction of cases whose top
#: reranked passage is a gold doc). Floor sits below the observed corpus mean so a normal
#: run passes but a fusion/rerank regression that mis-ranks another case trips it.
CONTEXT_PRECISION = Metric(name="context_precision@1", threshold=0.66)
#: Context recall — per case: did every gold doc surface anywhere in the retrieved set.
CONTEXT_RECALL = Metric(name="context_recall", threshold=0.95)
#: Groundedness (faithfulness) proxy — per case: expected claims present in the context.
GROUNDEDNESS = Metric(name="groundedness", threshold=0.85)
#: Tool/role selection accuracy — the agentic case: did the router pick the expected role.
TOOL_SELECTION = Metric(name="tool_selection_accuracy", threshold=0.99)

#: The declarative metric set the gate enforces (DeepEval-style, per-metric thresholds).
DEFAULT_METRICS: tuple[Metric, ...] = (
    CONTEXT_PRECISION,
    CONTEXT_RECALL,
    GROUNDEDNESS,
    TOOL_SELECTION,
)


@dataclass(frozen=True)
class RouterEvalCase:
    """One agentic tool-selection case: a query and the role the router must choose."""

    query: str
    expected_role: str


#: Representative agentic cases: memory-recall phrasings must route to ``memory``, plain
#: factual questions to the default ``qa`` specialist. Deterministic via the router's
#: keyword classifier (no model), so this is a stable agent-behavior regression check.
ROUTER_EVAL_CASES: tuple[RouterEvalCase, ...] = (
    RouterEvalCase("What do you know about me?", "memory"),
    RouterEvalCase("Do you remember what I told you last time?", "memory"),
    RouterEvalCase("How long does a refund take and how is it returned to the customer?", "qa"),
    RouterEvalCase("What is the SLA for an urgent request?", "qa"),
)


def _metric(by_name: dict[str, Metric], default: Metric) -> Metric:
    """Return the overriding metric for ``default.name`` if supplied, else ``default``."""
    return by_name.get(default.name, default)


async def run_tool_selection_eval(
    *, complete: CompleteFn | None = None
) -> tuple[float, list[tuple[str, str, str, bool]]]:
    """Run the agentic router eval; return ``(accuracy, per-query details)``.

    Drives the **real** :func:`app.agent.router.route_query` over the real adapter roster.
    With ``complete=None`` the router stays on its deterministic keyword path (no model),
    so the result is stable and offline. ``details`` rows are ``(query, expected, actual,
    ok)`` for readable diagnostics.
    """
    roster = load_roster()
    details: list[tuple[str, str, str, bool]] = []
    for case in ROUTER_EVAL_CASES:
        decision = await route_query(case.query, roster, complete=complete)
        ok = decision.role == case.expected_role
        details.append((case.query, case.expected_role, decision.role, ok))
    accuracy = sum(1 for *_, ok in details if ok) / len(details) if details else 1.0
    return accuracy, details


async def run_regression_gate(
    *,
    complete: CompleteFn | None = None,
    metrics: Sequence[Metric] = DEFAULT_METRICS,
) -> RegressionReport:
    """Run the DeepEval-pattern regression gate over the seed corpus + agentic case.

    Steps (all real, nothing hardcoded):

    1. Drive the real hybrid retriever over each :data:`app.eval.corpus.SEED_CASES` case
       and score it with :func:`app.eval.metrics.score_case`. Each case becomes a
       :class:`GateCaseResult` gated per-case on **context_recall** and **groundedness**.
    2. Add a corpus-level :class:`GateCaseResult` for **context_precision@1** (the mean
       over the cases) — precision@1 is a ranking rate best read across the corpus for
       single-gold cases.
    3. Add the agentic :class:`GateCaseResult` for **tool_selection_accuracy** from the
       real router (:func:`run_tool_selection_eval`).

    Args:
        complete: Optional chat callable (offline path leaves it ``None``).
        metrics: Metric set (per-metric thresholds); override to make the gate stricter or
            laxer. Missing names fall back to the module defaults, so a caller can override
            just one threshold.

    Returns:
        A :class:`RegressionReport`; ``passed`` is the CI gate.
    """
    by_name = {m.name: m for m in metrics}
    precision_metric = _metric(by_name, CONTEXT_PRECISION)
    recall_metric = _metric(by_name, CONTEXT_RECALL)
    grounded_metric = _metric(by_name, GROUNDEDNESS)
    tool_metric = _metric(by_name, TOOL_SELECTION)

    cases: list[GateCaseResult] = []

    # 1. Per-case retrieval metrics from the REAL hybrid pipeline.
    scores = []
    for case in SEED_CASES:
        retriever = build_eval_retriever()  # fresh cache per case (no cross-case hits)
        result = await retriever.retrieve(case.query)
        score = score_case(case, result, precision_k=1)
        scores.append(score)
        metric_results = [
            recall_metric.evaluate(score.context_recall),
            grounded_metric.evaluate(score.groundedness),
        ]
        cases.append(
            GateCaseResult(
                name=f"retrieval: {case.query}",
                metrics=metric_results,
                passed=all(m.passed for m in metric_results),
            )
        )

    # 2. Corpus-level context precision@1 (binary per single-gold case → gate the rate).
    agg = aggregate(scores)
    precision_result = precision_metric.evaluate(agg.context_precision)
    cases.append(
        GateCaseResult(
            name="retrieval-corpus: context_precision@1",
            metrics=[precision_result],
            passed=precision_result.passed,
        )
    )

    # 3. Agentic tool/role-selection case (real router, deterministic offline).
    accuracy, _details = await run_tool_selection_eval(complete=complete)
    tool_result = tool_metric.evaluate(accuracy)
    cases.append(
        GateCaseResult(
            name="agentic: tool_selection",
            metrics=[tool_result],
            passed=tool_result.passed,
        )
    )

    return RegressionReport(cases=cases, passed=all(c.passed for c in cases))


def _main() -> int:
    """Run the regression gate, print a human-readable report, return a POSIX exit code."""
    import asyncio

    report = asyncio.run(run_regression_gate())
    print(f"DeepEval-pattern regression gate over {len(report.cases)} cases:")  # noqa: T201
    for case in report.cases:
        flag = "PASS" if case.passed else "FAIL"
        print(f"  [{flag}] {case.name}")  # noqa: T201
        for m in case.metrics:
            mark = "ok" if m.passed else "XX"
            print(  # noqa: T201
                f"        {mark} {m.name} = {m.value:.3f} (threshold {m.threshold:.3f})"
            )
    if report.passed:
        print("PASS")  # noqa: T201
        return 0
    failed = ", ".join(c.name for c in report.failures())
    print(f"FAIL: {failed}")  # noqa: T201
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
