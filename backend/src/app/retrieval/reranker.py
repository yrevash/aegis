"""Second-stage reranking via an **LLM-as-reranker** (API only).

## Why LLM-as-reranker

`docs/backend.md` prescribes two-stage retrieval (wide recall → rerank → top-K), and
`docs/AGENT_BRIEF.md` locks the reranker to **API-based only** — no local cross-encoder,
because the deploy target is a 16 GB, no-GPU machine and the model fleet
(`app.core.models`) has **no dedicated rerank deployment**. We therefore score relevance
with a single cheap gateway call: the model grades each candidate 0–10 for how well it
answers the query and returns strict JSON, which we parse and sort by. Defaults to
`ModelRole.CHEAP`; callers may pass `ModelRole.REASONING` for harder queries.

Candidate text is **spotlighted before it reaches the scoring model** — the reranker
consumes untrusted retrieved content, so it is itself a prompt-injection surface.
"""

from __future__ import annotations

import json

from app.core.models import ModelRole

from .models import Candidate
from .protocols import CompleteFn
from .spotlight import spotlight, spotlight_system_instruction

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


def _parse_scores(content: str, count: int) -> dict[int, float]:
    """Parse the model's JSON scores into an ``{index: score}`` map.

    Malformed or out-of-range entries are ignored; a fully unparseable response yields an
    empty map (the caller then falls back to recall order).
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


async def rerank(
    query: str,
    candidates: list[Candidate],
    *,
    complete: CompleteFn,
    top_k: int,
    role: ModelRole = ModelRole.CHEAP,
) -> list[Candidate]:
    """Rerank `candidates` against `query` and return the top `top_k`.

    Args:
        query: The user query.
        candidates: Wide-recall candidates to reorder.
        complete: The chat completion function (`app.core.llm.complete`).
        top_k: Number of candidates to keep after reranking.
        role: Which model role to score with (`CHEAP` by default).

    Returns:
        Up to `top_k` candidates ordered by descending relevance. On a model/parse
        failure, falls back to the original recall order (still truncated to `top_k`).
    """
    if not candidates:
        return []
    if top_k <= 0:
        return []

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
        return candidates[:top_k]

    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: scores.get(pair[0], float("-inf")),
        reverse=True,
    )
    out: list[Candidate] = []
    for idx, cand in ranked[:top_k]:
        out.append(cand.model_copy(update={"score": scores.get(idx, 0.0)}))
    return out
