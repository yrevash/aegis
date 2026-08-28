"""Deterministic, lexical retrieval-quality proxies for the offline gate.

These are **RAGAS-style deterministic proxies** — inspired by RAGAS metric *ideas* but
computed here with transparent token/substring overlap, with **no external LLM and no
`ragas` import on this path**. They are proxies, not the RAGAS-the-library metrics they
are named after.

The parenthetical here used to read "the `ragas` package is not a dependency", and that
is no longer true: ``ragas>=0.4.3`` **is** a dependency, and
:mod:`aegis.evals.libs.ragas_suite` runs the real library's metrics through the
platform's metered gateway. This module deliberately does not use it. The offline gate
has to be free, deterministic, and runnable in CI with no keys and no network — a
judged metric is none of those — so the two coexist for different jobs rather than one
replacing the other. Three are computed:

- **context-precision proxy @ k** — of the top-k retrieved sources, the fraction whose
  source document is one of the case's gold documents (are the retrieved passages
  relevant?). Proxy for RAGAS *context precision*.
- **context-recall proxy** — of the case's gold documents, the fraction that appear
  anywhere in the retrieved sources (did we surface the passages the answer needs?).
  Proxy for RAGAS *context recall*.
- **groundedness (faithfulness) proxy** — the fraction of the case's expected claim
  keywords present, by normalized substring match, in the assembled ``answer_context``
  (could a faithful answer cite them from what was retrieved?). A lexical proxy for
  RAGAS *faithfulness*.

RAGAS *answer relevancy* is **not** computed deterministically here (it needs a
generation + semantic-similarity model). The model-graded signal — genuine groundedness
**and** relevance — is the optional LLM-as-judge (:mod:`aegis.evals.judge`), which
:func:`aegis.evals.harness.evaluate` runs when a chat-completion ``complete`` callable is
injected, and which is otherwise skipped so the default path stays offline.

Each proxy is a value in ``[0, 1]``; :func:`aggregate` averages them across the corpus
into the numbers the CI gate asserts against.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from aegis.retrieval.models import RetrievalResult

from .corpus import EvalCase

#: Any run of non-alphanumeric characters — collapsed to a single space so that
#: spotlight datamarking (words joined by ``▁``), fences, punctuation and newlines
#: never break a multi-word claim match.
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def _normalize(text: str) -> str:
    """Lowercase ``text`` and collapse every non-alphanumeric run to one space.

    This neutralises spotlight datamarking (``original▁payment▁method``) and any
    punctuation so a phrase claim like "original payment method" matches the context.
    """
    return " " + _NON_ALNUM.sub(" ", text.lower()).strip() + " "


@dataclass(frozen=True)
class CaseScore:
    """The per-case deterministic-proxy triple plus the identifiers behind it.

    All three are lexical/overlap proxies (see the module docstring), not RAGAS-library
    metrics.

    Attributes:
        query: The evaluated query (for readable failure output).
        context_precision: Context-precision proxy @k of retrieved sources vs gold docs.
        context_recall: Context-recall proxy of gold docs among retrieved sources;
            ``None`` when the case carries no gold docs (nothing to measure).
        groundedness: Groundedness/faithfulness proxy — fraction of expected claims
            present (by normalized substring match) in the answer context; ``None``
            when the case carries no claims (nothing to measure).
        retrieved_docs: The distinct source doc ids retrieved (for diagnostics).

    ``None`` is deliberate and load-bearing: an *unlabelled* facet used to score a
    perfect ``1.0``, which :func:`aggregate` then averaged in over the full case
    count — so adding unlabelled cases lifted the corpus mean and could hold the gate
    above its threshold while a real regression ran underneath. Not-measured is now
    not-counted.
    """

    query: str
    context_precision: float
    context_recall: float | None
    groundedness: float | None
    retrieved_docs: tuple[str, ...]

    def as_dict(self) -> dict[str, object]:
        """Return this case's exact per-metric numbers as a plain, JSON-ready dict.

        The values are the *same* floats the aggregate is built from and the stream/row
        surfaces carry — no rounding, so ``as_dict`` is a lossless projection.
        """
        return {
            "query": self.query,
            "contextPrecision": self.context_precision,
            "contextRecall": self.context_recall,
            "groundedness": self.groundedness,
            "retrievedDocs": list(self.retrieved_docs),
        }


@dataclass(frozen=True)
class AggregateScore:
    """Corpus-level means over every :class:`CaseScore`.

    Each mean is over the cases that actually *carried* that label, not over the whole
    corpus — ``recall_cases`` / ``groundedness_cases`` say how many those were. A metric
    no case was labelled for is ``None`` (honestly not measured), never ``1.0``.
    """

    context_precision: float
    context_recall: float | None
    groundedness: float | None
    cases: int
    recall_cases: int | None = None
    groundedness_cases: int | None = None

    def __post_init__(self) -> None:
        """Default the per-metric contributor counts to the full case count."""
        if self.recall_cases is None:
            object.__setattr__(self, "recall_cases", self.cases)
        if self.groundedness_cases is None:
            object.__setattr__(self, "groundedness_cases", self.cases)

    def as_dict(self) -> dict[str, object]:
        """Return the corpus-level means as a plain dict (the authoritative aggregate)."""
        return {
            "contextPrecision": self.context_precision,
            "contextRecall": self.context_recall,
            "groundedness": self.groundedness,
            "cases": self.cases,
            "recallCases": self.recall_cases,
            "groundednessCases": self.groundedness_cases,
        }


@dataclass(frozen=True)
class MetricConfig:
    """The effective, aggregated configuration + current reading for one metric.

    The single dashboard-facing view of a metric shared by both eval surfaces (the
    RAGAS-style :class:`~aegis.evals.harness.EvalReport` and the DeepEval-pattern
    :class:`~aegis.evals.regression.RegressionReport`): its declarative definition
    (``name`` / ``threshold`` / ``higher_is_better``), the current aggregate ``value``, the
    aggregate pass verdict, how many cases contributed, and whether the value was actually
    ``computed`` (``False`` marks an honestly-not-computed metric such as RAGAS answer
    relevancy, whose ``value`` is then ``None``).

    It is the one authoritative per-metric number — the stream payload and the persisted
    rows are both derived from it, so they can never drift.

    Attributes:
        name: Stable metric id (matches a :class:`Metric.name`).
        threshold: The effective pass bar (the injected/overridden one, if any).
        higher_is_better: Pass direction (``value >= threshold`` when ``True``).
        value: Aggregate reading (mean across contributing cases); ``None`` when
            ``computed`` is ``False``.
        passed: Aggregate verdict — ``True`` iff every contributing case passed.
        cases: How many cases contributed to ``value``.
        computed: Whether this metric is deterministically computed offline. ``False`` is
            the honest signal that the number needs an LLM and is *not* faked here.
    """

    name: str
    threshold: float
    higher_is_better: bool
    value: float | None
    passed: bool
    cases: int
    computed: bool = True

    def as_dict(self) -> dict[str, object]:
        """Return this metric's config + reading as a plain, JSON-ready dict."""
        return {
            "name": self.name,
            "threshold": self.threshold,
            "higherIsBetter": self.higher_is_better,
            "value": self.value,
            "passed": self.passed,
            "cases": self.cases,
            "computed": self.computed,
        }


def _source_doc(source: object) -> str:
    """Return the source's originating document id (``metadata['doc']`` or its id)."""
    meta = getattr(source, "metadata", None) or {}
    doc = meta.get("doc")
    if doc:
        return str(doc)
    # Fall back to the chunk id's document part ("<doc>#<ordinal>").
    return str(getattr(source, "id", "")).split("#", 1)[0]


def _claim_present(claim: str, normalized_context: str) -> bool:
    """Return whether ``claim`` is supported by the (normalized) context.

    Both the claim and the context are normalized (lowercased, non-alphanumeric runs
    collapsed to single spaces and space-padded), so a single-token or multi-word claim
    is a padded-substring match — robust to datamarking, punctuation and casing.
    """
    needle = _normalize(claim)
    return needle.strip() != "" and needle in normalized_context


def score_case(
    case: EvalCase, result: RetrievalResult, *, precision_k: int
) -> CaseScore:
    """Score one retrieval result against its labelled case.

    Args:
        case: The labelled eval case (gold docs + expected claims).
        result: The retriever's output for ``case.query``.
        precision_k: The cut-off ``k`` for context precision (top-k sources).

    Returns:
        The :class:`CaseScore` for this case.
    """
    ranked_docs = [_source_doc(s) for s in result.sources]
    top_k = ranked_docs[:precision_k]

    precision = (
        sum(doc in case.gold_doc_ids for doc in top_k) / len(top_k) if top_k else 0.0
    )
    retrieved_set = set(ranked_docs)
    # An unlabelled facet is NOT a passing facet — it is an absent measurement, and
    # scoring it 1.0 would let unlabelled cases pad the corpus mean.
    recall = (
        sum(gold in retrieved_set for gold in case.gold_doc_ids) / len(case.gold_doc_ids)
        if case.gold_doc_ids
        else None
    )

    normalized_context = _normalize(result.answer_context)
    grounded = (
        sum(_claim_present(claim, normalized_context) for claim in case.claims)
        / len(case.claims)
        if case.claims
        else None
    )

    return CaseScore(
        query=case.query,
        context_precision=precision,
        context_recall=recall,
        groundedness=grounded,
        retrieved_docs=tuple(dict.fromkeys(ranked_docs)),
    )


def aggregate(scores: Sequence[CaseScore]) -> AggregateScore:
    """Average the per-case metrics into corpus-level means.

    Recall and groundedness are averaged over the cases that carried that label only —
    an unlabelled case contributes to neither the numerator nor the denominator, so it
    can no longer inflate the corpus mean (and mask a regression) simply by existing.

    Args:
        scores: The per-case scores.

    Returns:
        The :class:`AggregateScore`. ``context_recall``/``groundedness`` are ``None``
        when no case was labelled for them; zeros when ``scores`` is empty.
    """
    n = len(scores)
    if n == 0:
        return AggregateScore(0.0, 0.0, 0.0, 0, 0, 0)

    def _mean(values: list[float]) -> float | None:
        return sum(values) / len(values) if values else None

    recalls = [s.context_recall for s in scores if s.context_recall is not None]
    grounded = [s.groundedness for s in scores if s.groundedness is not None]
    return AggregateScore(
        context_precision=sum(s.context_precision for s in scores) / n,
        context_recall=_mean(recalls),
        groundedness=_mean(grounded),
        cases=n,
        recall_cases=len(recalls),
        groundedness_cases=len(grounded),
    )
