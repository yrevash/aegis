"""Pure recall-scoring math — the Generative-Agents composite.

Blends relevance + recency + importance + frequency, all components min-max normalized
across the candidate set.

No I/O, no ORM, no infra — every function here is deterministic and unit-testable in
isolation (that is deliberate: recall ranking is where correctness bugs hide, so the
math is separated from the stores and the Qdrant-ANN-vs-SQL plumbing). See
``docs/architecture/memory-spec.md`` §B.1.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from aegis.memory.config import MemoryConfig


@dataclass
class RecallCandidate:
    """One scorable memory item (a fact or a past turn), decoupled from the ORM.

    ``relevance`` is a precomputed similarity in [0, 1] (the cosine score returned by
    the Qdrant ANN search) so the scoring math never touches embeddings or a database.
    ``payload`` carries the source row for the
    caller to render after selection.

    Attributes:
        key: Stable identity used for cross-tier dedup (facts: ``(subject,predicate)``;
            turns: the message id).
        text: The natural-language rendering injected into the window.
        relevance: Precomputed similarity to the query, in [0, 1].
        age_days: Age used for recency decay (non-negative).
        importance: Poignancy on the Generative-Agents 1..10 scale.
        access_count: How many times this item has been surfaced — bumped on the recall
            READ path (:func:`aegis.memory.recall.recall`) each turn it is recalled, and on
            the write path when a duplicate re-asserts it (consolidation dedup). Drives
            the frequency term of the composite (``config.w_freq``); 0 until first access.
        payload: Opaque source object (e.g. the ORM row) for rendering.
    """

    key: str
    text: str
    relevance: float
    age_days: float = 0.0
    importance: int = 5
    access_count: int = 0
    payload: Any = None


def recency_decay(age_days: float, half_life_days: float) -> float:
    """Exponential recency in (0, 1]: ``0.5 ** (age / half_life)``.

    A fresh item (age 0) scores 1.0; one exactly ``half_life_days`` old scores 0.5.

    Args:
        age_days: Non-negative age in days (clamped at 0).
        half_life_days: Positive half-life; non-positive is treated as "no decay" (1.0).

    Returns:
        The recency weight in (0, 1].
    """
    if half_life_days <= 0:
        return 1.0
    return 0.5 ** (max(0.0, age_days) / half_life_days)


def minmax(values: list[float]) -> list[float]:
    """Min-max normalize to [0, 1]; a constant (or empty) vector maps to all zeros.

    Mapping a constant column to 0 (rather than 1) keeps a component from silently
    dominating the blend when it carries no discriminating signal.

    Args:
        values: Raw component values.

    Returns:
        The normalized values, aligned to the input.
    """
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.0 for _ in values]
    span = hi - lo
    return [(v - lo) / span for v in values]


def score_candidates(
    candidates: list[RecallCandidate],
    config: MemoryConfig,
    *,
    half_life_days: float,
) -> list[float]:
    """Score candidates by the weighted, min-max-normalized composite.

    Each component (relevance, recency, importance, frequency) is normalized *across
    the candidate set* and combined with the config weights. Returns scores aligned to
    ``candidates`` (does not sort — the caller selects top-n).

    Args:
        candidates: The items to score (already carrying a precomputed ``relevance``).
        config: Weights come from here (``w_rel/w_rec/w_imp/w_freq``).
        half_life_days: Recency half-life for this tier (facts vs episodic differ).

    Returns:
        One composite score per candidate, in input order.
    """
    if not candidates:
        return []
    rel = minmax([c.relevance for c in candidates])
    rec = minmax([recency_decay(c.age_days, half_life_days) for c in candidates])
    imp = minmax([c.importance / 10.0 for c in candidates])
    freq = minmax([math.log1p(max(0, c.access_count)) for c in candidates])
    return [
        config.w_rel * rel[i]
        + config.w_rec * rec[i]
        + config.w_imp * imp[i]
        + config.w_freq * freq[i]
        for i in range(len(candidates))
    ]


def rank_top(
    candidates: list[RecallCandidate],
    config: MemoryConfig,
    *,
    half_life_days: float,
    n: int,
) -> list[RecallCandidate]:
    """Return the top-``n`` candidates by composite score (stable, highest first).

    Args:
        candidates: Items to rank.
        config: Composite weights.
        half_life_days: Tier recency half-life.
        n: How many to keep (``<= 0`` returns empty).

    Returns:
        The ``n`` highest-scoring candidates, most relevant first.
    """
    if n <= 0 or not candidates:
        return []
    scores = score_candidates(candidates, config, half_life_days=half_life_days)
    order = sorted(range(len(candidates)), key=lambda i: scores[i], reverse=True)
    return [candidates[i] for i in order[:n]]


@dataclass
class ForgetPolicy:
    """Thresholds for the prune sweep (soft-archival, never hard-delete)."""

    forget_floor: float
    forget_min_age_days: float
    half_life_days: float = 30.0
    fields: dict[str, Any] = field(default_factory=dict)

    def is_archivable(
        self, *, confidence: float, age_days: float, access_count: int, invalidated: bool
    ) -> bool:
        """Whether a fact may be archived out of hot recall (kept for audit).

        Archivable when it is already invalidated, or its confidence-weighted recency
        has decayed below the floor while it has never been recalled and is old enough.
        """
        if invalidated:
            return True
        decayed = confidence * recency_decay(age_days, self.half_life_days)
        return (
            decayed < self.forget_floor
            and access_count == 0
            and age_days > self.forget_min_age_days
        )


__all__ = [
    "ForgetPolicy",
    "RecallCandidate",
    "minmax",
    "rank_top",
    "recency_decay",
    "score_candidates",
]
