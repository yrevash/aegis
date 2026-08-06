"""Deterministic, lexical retrieval-quality proxies for the offline gate.

These are **RAGAS-style deterministic proxies** — inspired by RAGAS metric *ideas* but
computed here with transparent token/substring overlap, with **no external LLM and no
`ragas` library** (the `ragas` package is not a dependency: it needs a network/LLM and
this gate runs fully offline). They are proxies, not the RAGAS-the-library metrics they
are named after. Three are computed:

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
**and** relevance — is the optional LLM-as-judge (:mod:`app.eval.judge`), which
:func:`app.eval.harness.evaluate` runs when a chat-completion ``complete`` callable is
injected, and which is otherwise skipped so the default path stays offline.

Each proxy is a value in ``[0, 1]``; :func:`aggregate` averages them across the corpus
into the numbers the CI gate asserts against.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.retrieval.models import RetrievalResult

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
        context_recall: Context-recall proxy of gold docs among retrieved sources.
        groundedness: Groundedness/faithfulness proxy — fraction of expected claims
            present (by normalized substring match) in the answer context.
        retrieved_docs: The distinct source doc ids retrieved (for diagnostics).
    """

    query: str
    context_precision: float
    context_recall: float
    groundedness: float
    retrieved_docs: tuple[str, ...]


@dataclass(frozen=True)
class AggregateScore:
    """Corpus-level means over every :class:`CaseScore`."""

    context_precision: float
    context_recall: float
    groundedness: float
    cases: int


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
    recall = (
        sum(gold in retrieved_set for gold in case.gold_doc_ids)
        / len(case.gold_doc_ids)
        if case.gold_doc_ids
        else 1.0
    )

    normalized_context = _normalize(result.answer_context)
    grounded = (
        sum(_claim_present(claim, normalized_context) for claim in case.claims)
        / len(case.claims)
        if case.claims
        else 1.0
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

    Args:
        scores: The per-case scores.

    Returns:
        The :class:`AggregateScore` (zeros when ``scores`` is empty).
    """
    n = len(scores)
    if n == 0:
        return AggregateScore(0.0, 0.0, 0.0, 0)
    return AggregateScore(
        context_precision=sum(s.context_precision for s in scores) / n,
        context_recall=sum(s.context_recall for s in scores) / n,
        groundedness=sum(s.groundedness for s in scores) / n,
        cases=n,
    )
