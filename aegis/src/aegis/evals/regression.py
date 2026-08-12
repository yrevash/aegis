"""The **DeepEval-pattern** CI regression gate — pytest-native, per-metric thresholds.

This is a native implementation of the *DeepEval pattern*: a declarative,
per-metric-threshold regression gate you assert in a normal pytest run (mirroring
``deepeval``'s ``assert_test`` / ``@pytest.mark.parametrize`` style), sitting alongside
the RAGAS-style aggregate gate in :mod:`aegis.evals.harness`. It adds two things that
matter for a *DeepEval-shaped* suite:

- **Declarative metrics with per-metric thresholds** (:class:`Metric`) — each metric
  carries its own pass bar and ``higher_is_better`` direction, and each is evaluated to a
  :class:`MetricResult`. A :class:`GateCaseResult` bundles the metrics for one test case;
  the whole run is a :class:`RegressionReport` whose ``passed`` is the CI gate.
- **An optional agentic / tool-use eval case** — not just RAG. When a router is injected
  (``route_fn`` + ``roster``) it asserts the router selects the expected specialist role
  for representative queries (a memory-recall phrasing → ``memory``; a factual question →
  ``qa``), scored as the ``tool_selection_accuracy`` metric. This is agent-behavior
  regression testing (does the agent still pick the right tool/role?), which pure
  retrieval metrics do not cover. **The router is inject-only** — with no ``route_fn`` the
  agentic case is skipped and the RAG-path metrics are preserved unconditionally, so this
  module never imports an agent layer.

**Why native, not the ``deepeval`` package.** ``deepeval`` is heavy and, for most of its
metrics, calls an external LLM judge — which makes it slow, non-deterministic, and
network-dependent. This gate must run in CI with no infra, no keys, and no network, so it
implements the *pattern* natively on top of the existing harness: it drives the **real**
hybrid retriever (:func:`aegis.evals.harness.build_eval_retriever`) over the fixed
:data:`aegis.evals.corpus.SEED_CASES` with a deterministic local embedding and a
pass-through reranker (offline), and computes the retrieval metrics with the real,
deterministic :func:`aegis.evals.metrics.score_case`. **No scores are hardcoded** — every
number comes from a real retrieval or a real router decision.

Run it directly for a human-readable report and a POSIX exit code::

    python -m aegis.evals.regression
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from .corpus import SEED_CASES
from .harness import build_eval_retriever
from .metrics import MetricConfig, aggregate, score_case

#: A chat-completion callable (shape of a gateway ``complete``). When ``None`` (the
#: default, offline path) the retriever's reranker falls back to RRF order and an
#: injected router uses its deterministic classifier — no model is called.
CompleteFn = Callable[..., Awaitable[object]]

#: An injected router callable: ``route_fn(query, roster, *, complete=None) -> decision``
#: where ``decision.role`` is the chosen specialist role. Inject-only (with the roster) —
#: absent it, the agentic tool-selection case is skipped.
RouteFn = Callable[..., Awaitable[object]]


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
        return MetricResult(
            name=self.name,
            value=value,
            threshold=self.threshold,
            passed=passed,
            higher_is_better=self.higher_is_better,
        )


@dataclass(frozen=True)
class MetricResult:
    """One metric measured for one case, with its pass/fail verdict.

    ``higher_is_better`` is carried through from the :class:`Metric` that produced this
    result so any downstream surface (accessor/stream/row) can re-derive the pass rule
    without reaching back to the metric definition.
    """

    name: str
    value: float
    threshold: float
    passed: bool
    higher_is_better: bool = True


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

    def metric_configs(self) -> list[MetricConfig]:
        """Aggregate the per-case metric results into one :class:`MetricConfig` per metric.

        Metrics that recur across cases (e.g. ``context_recall`` on every seed case) are
        folded into a single entry whose ``value`` is the mean of the case readings and
        whose ``passed`` is ``True`` only if every contributing case passed. Insertion
        order (first appearance across the cases) is preserved so the surface is stable.

        This is the **single source** the stream payload and the persisted rows read from —
        there is exactly one authoritative number per metric.
        """
        order: list[str] = []
        buckets: dict[str, list[MetricResult]] = {}
        for case in self.cases:
            for m in case.metrics:
                if m.name not in buckets:
                    buckets[m.name] = []
                    order.append(m.name)
                buckets[m.name].append(m)
        configs: list[MetricConfig] = []
        for name in order:
            results = buckets[name]
            value = sum(m.value for m in results) / len(results)
            configs.append(
                MetricConfig(
                    name=name,
                    threshold=results[0].threshold,
                    higher_is_better=results[0].higher_is_better,
                    value=value,
                    passed=all(m.passed for m in results),
                    cases=len(results),
                )
            )
        return configs

    def overall(self) -> float:
        """Return the overall score — the mean of the per-metric aggregate values.

        Defined once, here, so ``evaluate()``/stream/rows never compute an ``overall`` a
        different way. ``0.0`` when the report carries no metrics.
        """
        configs = self.metric_configs()
        return sum(c.value for c in configs) / len(configs) if configs else 0.0

    def as_dict(self) -> dict[str, object]:
        """Return the whole report as a plain dict: overall, passed, per-metric, per-case.

        The lossless, authoritative projection every downstream surface (stream event,
        persisted rows, dashboard accessor) is built from — so *computed == streamed ==
        persisted == accessor* holds by construction (no recompute, no rounding).
        """
        return {
            "overall": self.overall(),
            "passed": self.passed,
            "metrics": [c.as_dict() for c in self.metric_configs()],
            "cases": [
                {
                    "name": case.name,
                    "passed": case.passed,
                    "metrics": [
                        {
                            "name": m.name,
                            "value": m.value,
                            "threshold": m.threshold,
                            "higherIsBetter": m.higher_is_better,
                            "passed": m.passed,
                        }
                        for m in case.metrics
                    ],
                }
                for case in self.cases
            ],
        }

    def to_eval_rows(
        self, *, run_id: str | None = None, prompt_key: str | None = None
    ) -> list[dict[str, object]]:
        """Project the aggregated metrics into ``EvalResult``-column-shaped rows.

        Returns one plain dict per :class:`MetricConfig` with exactly the columns of the
        ``eval_results`` table (:class:`aegis.ops.models.EvalResult`) — ``run_id``,
        ``prompt_key``, ``metric``, ``score``, ``passed``, ``detail`` — using the *same*
        aggregate ``value`` the accessor and stream carry. A host persists these as-is, so
        the number written to the row equals the number computed and streamed.

        Kept ORM-free on purpose (returns dicts, not model instances) so ``aegis.evals``
        pulls no SQLAlchemy; the caller constructs the rows.
        """
        return [
            {
                "run_id": run_id,
                "prompt_key": prompt_key,
                "metric": c.name,
                "score": c.value,
                "passed": c.passed,
                "detail": {
                    "threshold": c.threshold,
                    "higherIsBetter": c.higher_is_better,
                    "cases": c.cases,
                    "source": "regression_gate",
                },
            }
            for c in self.metric_configs()
        ]


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
    *,
    complete: CompleteFn | None = None,
    route_fn: RouteFn,
    roster: object,
) -> tuple[float, list[tuple[str, str, str, bool]]]:
    """Run the agentic router eval; return ``(accuracy, per-query details)``.

    Drives the **injected** ``route_fn`` over the injected ``roster``. With
    ``complete=None`` a deterministic router stays on its keyword path (no model), so the
    result is stable and offline. ``details`` rows are ``(query, expected, actual, ok)``
    for readable diagnostics.

    Args:
        complete: Optional chat callable threaded to the router (offline leaves it ``None``).
        route_fn: The injected router — ``route_fn(query, roster, *, complete=None)``
            returning a decision with a ``.role`` attribute.
        roster: The injected agent roster the router selects from.
    """
    details: list[tuple[str, str, str, bool]] = []
    for case in ROUTER_EVAL_CASES:
        decision = await route_fn(case.query, roster, complete=complete)
        ok = decision.role == case.expected_role
        details.append((case.query, case.expected_role, decision.role, ok))
    accuracy = sum(1 for *_, ok in details if ok) / len(details) if details else 1.0
    return accuracy, details


async def run_regression_gate(
    *,
    complete: CompleteFn | None = None,
    metrics: Sequence[Metric] = DEFAULT_METRICS,
    route_fn: RouteFn | None = None,
    roster: object | None = None,
) -> RegressionReport:
    """Run the DeepEval-pattern regression gate over the seed corpus (+ optional agentic case).

    Steps (all real, nothing hardcoded):

    1. Drive the real hybrid retriever over each :data:`aegis.evals.corpus.SEED_CASES` case
       and score it with :func:`aegis.evals.metrics.score_case`. Each case becomes a
       :class:`GateCaseResult` gated per-case on **context_recall** and **groundedness**.
    2. Add a corpus-level :class:`GateCaseResult` for **context_precision@1** (the mean
       over the cases) — precision@1 is a ranking rate best read across the corpus for
       single-gold cases.
    3. **When a router is injected** (``route_fn`` + ``roster``), add the agentic
       :class:`GateCaseResult` for **tool_selection_accuracy** from
       :func:`run_tool_selection_eval`. With no router the agentic case is skipped and the
       RAG-path metrics stand on their own.

    Args:
        complete: Optional chat callable (offline path leaves it ``None``).
        metrics: Metric set (per-metric thresholds); override to make the gate stricter or
            laxer. Missing names fall back to the module defaults, so a caller can override
            just one threshold.
        route_fn: Optional injected router (with ``roster``) to run the agentic case.
        roster: Optional injected agent roster the router selects from.

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

    # 3. Agentic tool/role-selection case — only when a router is injected.
    if route_fn is not None:
        accuracy, _details = await run_tool_selection_eval(
            complete=complete, route_fn=route_fn, roster=roster
        )
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
