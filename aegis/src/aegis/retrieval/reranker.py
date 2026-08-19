"""Second-stage reranking via an **LLM-as-reranker**, over the model gateway.

## What this is now: the fallback, not the reranker

This module used to be the whole story, and its docstring said so on a premise that was
false. The old sentence — *"API only, no local cross-encoder, because the deploy target is a
16 GB, no-GPU machine"* — treated "local cross-encoder" as a synonym for "GPU and a heavy
model". It is not. `fastembed`'s ONNX ``TextCrossEncoder`` needs no GPU and pulls no torch,
and the checkpoint we ship is 33M parameters / ~130 MB. The deploy machine was never the
obstacle; the belief that it was kept the second retrieval stage switched off entirely, which
is the most expensive kind of documentation defect — a reader inherits the sentence and with
it the wrong decision.

**What the second stage is worth, external result and ours, side by side.** The +12.1 pp
recall@5 figure this docstring used to state flatly is **not ours**: it is the BEIR-style
cross-encoder result reported for `jinaai/jina-reranker-v1-tiny-en`, on their benchmarks and
their passage lengths. Our own ablation (``runs/eval-goldset-20260819.json``, n=53, A3 → A4)
moves **recall@6 by +0.009** — one case — because both arms see the same 20-candidate pool
and reranking cannot put into the top 6 what recall never retrieved. What it buys us is
**ordering**: **MRR@20 0.557 → 0.686 (+12.9 pp)** and nDCG@10 up with it. That is the honest
claim, and it is a good one — the answer moves toward rank 1, which is what the generator
reads first — but it is a different claim from "+12.1 pp recall", and n=53 cannot defend a
one-case recall delta either way.

Since task 4.9 the primary reranker is :mod:`aegis.retrieval.local_reranker`, and this module
is what runs **behind** it: when the local model cannot load or dies mid-query, the pipeline
logs at ERROR and comes here. It is also the whole reranker for a deployment that genuinely
cannot carry model weights (an air-gapped box with no cached ONNX file, say) — which is why
it stays a first-class, tested path rather than dead code.

## Why an LLM can rerank at all

`docs/architecture/backend.md` prescribes two-stage retrieval (wide recall → rerank → top-K),
and the model fleet has **no dedicated rerank deployment**. So we score relevance with a
single cheap gateway call: the model grades each candidate 0-10 for how well it answers the
query and returns strict JSON, which we parse and sort by. Defaults to `ModelRole.CHEAP`;
callers may pass `ModelRole.REASONING` for harder queries. Its costs versus the local
encoder are real and are the reason it is second in line: one billed call per query, a
non-deterministic order that two eval runs cannot be compared across, and a parse that can
fail (which is why :class:`RerankOutcome` reports ``graded`` separately from ``ran``).

Candidate text is **spotlighted before it reaches the scoring model** — the reranker
consumes untrusted retrieved content, so it is itself a prompt-injection surface. The local
encoder does not need this: it emits a float, not a continuation, so there is no instruction
for injected text to be obeyed by.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from aegis.core.models import ModelRole
from aegis.retrieval.models import Candidate, RerankEngine
from aegis.retrieval.protocols import CompleteFn
from aegis.retrieval.spotlight import spotlight, spotlight_system_instruction

_RERANK_SYSTEM = (
    "You are a precise relevance-ranking function for a retrieval system. "
    "You will be given a QUERY and a numbered list of candidate DOCUMENTS. "
    "Score how well each document helps answer the query on a 0-10 scale "
    "(10 = directly and fully answers it, 0 = irrelevant). "
    + spotlight_system_instruction()
    + " Respond with ONLY a JSON object of the form "
    '{"scores": [{"id": <int>, "score": <number>}, ...]} and nothing else.'
)


def _build_user_prompt(query: str, candidates: list[Candidate]) -> str:
    """Render the query and spotlighted candidates into the scoring prompt body."""
    lines = [f"QUERY: {query}", "", "DOCUMENTS:"]
    for idx, cand in enumerate(candidates):
        lines.append(f"[{idx}]\n{spotlight(cand.text)}")
    return "\n".join(lines)


@dataclass(frozen=True)
class RerankOutcome:
    """What reranking actually achieved — the survivors *and* whether they were graded.

    A rerank call can succeed at the transport level and still return nothing usable
    (unparseable JSON, no in-range ids), or grade only some of the candidates. The
    survivors alone cannot express that: an ungraded fallback list is byte-shaped
    exactly like a graded one. This carries the difference out to the pipeline so it can
    be reported instead of silently passing off recall order as relevance order.

    Attributes:
        candidates: The survivors, best first (up to ``top_k``).
        graded: Whether the model produced usable grades that ordered the survivors.
        ungraded: How many survivors carry no model grade. These always sort last and
            keep the score they arrived with (the fused RRF score) — never a fabricated
            ``0.0``.
        reason: Why a call that ran could not grade, else ``None``.
        engine: Which reranker produced this order — ``"api"`` for everything this module
            returns, ``"local"`` for :mod:`aegis.retrieval.local_reranker`. Carried on the
            outcome rather than assumed by the caller, because after a local failure the
            fallback's result is byte-shaped exactly like a first-choice one.
    """

    candidates: list[Candidate]
    graded: bool
    ungraded: int
    reason: str | None = None
    engine: RerankEngine = "api"


def _parse_scores(content: str, count: int) -> dict[int, float]:
    """Parse the model's JSON scores into an ``{index: score}`` map.

    Malformed or out-of-range entries are ignored; a fully unparseable response yields an
    empty map (the caller then falls back to recall order, and says so).
    """
    try:
        data = json.loads(content)
        rows = data["scores"]
    except (json.JSONDecodeError, TypeError, KeyError):
        return {}
    scores: dict[int, float] = {}
    for row in rows:
        try:
            idx = int(row["id"])
            score = float(row["score"])
        except (TypeError, ValueError, KeyError):
            continue
        if 0 <= idx < count:
            scores[idx] = score
    return scores


async def rerank_scored(
    query: str,
    candidates: list[Candidate],
    *,
    complete: CompleteFn,
    top_k: int,
    role: ModelRole = ModelRole.CHEAP,
) -> RerankOutcome:
    """Rerank `candidates` against `query`, reporting whether grading actually happened.

    Graded candidates are ordered by descending grade and stamped with it. Candidates
    the model did **not** grade are not invented into a ``0.0`` grade: they sort after
    every graded one, keep their incoming fused score, and are counted in
    :attr:`RerankOutcome.ungraded`. When nothing parses at all the fused recall order is
    kept — the honest fallback — but ``graded=False`` and a ``reason`` say so, so the
    caller can never mistake RRF scores for relevance grades.

    Args:
        query: The user query.
        candidates: Wide-recall candidates to reorder.
        complete: The injected chat-completion function (a :class:`CompleteFn`).
        top_k: Number of candidates to keep after reranking.
        role: Which model role to score with (`CHEAP` by default).

    Returns:
        A :class:`RerankOutcome` with up to `top_k` survivors and the grading verdict.
    """
    if not candidates or top_k <= 0:
        return RerankOutcome(candidates=[], graded=False, ungraded=0)

    messages: list[dict[str, object]] = [
        {"role": "system", "content": _RERANK_SYSTEM},
        {"role": "user", "content": _build_user_prompt(query, candidates)},
    ]
    result = await complete(
        role,
        messages,
        temperature=0.0,
        response_format={"type": "json_object"},
    )
    scores = _parse_scores(result.content, len(candidates))

    if not scores:
        kept = candidates[:top_k]
        return RerankOutcome(
            candidates=kept,
            graded=False,
            ungraded=len(kept),
            reason="rerank response carried no usable scores; kept the fused recall order",
        )

    # Graded first (by grade, ties by recall order), then the ungraded remainder in
    # recall order — each keeping the score it arrived with rather than a made-up grade.
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (pair[0] in scores, scores.get(pair[0], 0.0), -pair[0]),
        reverse=True,
    )
    out: list[Candidate] = []
    ungraded = 0
    for idx, cand in ranked[:top_k]:
        if idx in scores:
            out.append(cand.model_copy(update={"score": scores[idx]}))
        else:
            out.append(cand)
            ungraded += 1
    reason = (
        f"model graded only {len(scores)} of {len(candidates)} candidates"
        if len(scores) < len(candidates)
        else None
    )
    return RerankOutcome(candidates=out, graded=True, ungraded=ungraded, reason=reason)


async def rerank(
    query: str,
    candidates: list[Candidate],
    *,
    complete: CompleteFn,
    top_k: int,
    role: ModelRole = ModelRole.CHEAP,
) -> list[Candidate]:
    """Rerank `candidates` against `query` and return the top `top_k`.

    The list-only convenience wrapper over :func:`rerank_scored`, kept for callers that
    genuinely only want the survivors. Anything that *reports* on retrieval should call
    :func:`rerank_scored` instead — a bare list cannot say whether the order came from
    the model or from a failed call.

    Args:
        query: The user query.
        candidates: Wide-recall candidates to reorder.
        complete: The injected chat-completion function (a :class:`CompleteFn`).
        top_k: Number of candidates to keep after reranking.
        role: Which model role to score with (`CHEAP` by default).

    Returns:
        Up to `top_k` candidates ordered by descending relevance. On a model/parse
        failure, falls back to the original recall order (still truncated to `top_k`).
    """
    outcome = await rerank_scored(
        query, candidates, complete=complete, top_k=top_k, role=role
    )
    return outcome.candidates
