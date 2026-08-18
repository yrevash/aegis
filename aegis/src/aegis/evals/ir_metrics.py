"""Retrieval metrics, intervals and paired significance tests — pure stdlib.

## Which metrics, and at which k

The k values are the ones this codebase actually runs at
(:class:`~aegis.retrieval.pipeline.RetrievalConfig`: ``recall_top_k=20``,
``final_top_k=6``), not the textbook 5/10:

* **recall@20** — the pool ceiling. If the answer is not in the 20 candidates that reach
  the reranker, no rerank, prompt or model recovers it. This is the number ingestion and
  hybrid recall move.
* **recall@6** — what the generator actually reads.
* **The gap between them is the reranker's contribution, isolated.** Both arms saw the
  same pool; only the ordering inside it differs.
* **precision@6** — context-window efficiency. It moves *against* recall, which is
  exactly why both are reported: one blended "retrieval quality" number would hide
  whichever of the two moved the wrong way.
* **MRR@20** — the interpretable one: "the answer sits at rank 1.3 on average."
* **nDCG@10** — computed for recognisability and **flagged as degenerate here**: with
  single-gold binary relevance nDCG@10 collapses to ``1/log2(1+rank)``, which carries
  almost the same information as MRR. It earns its place only on the multi-span subset.

## Why the statistics are here at all

An arm-vs-arm difference without an interval is an anecdote. The design is **paired** —
every arm answers the same questions — so the test is on the per-query *difference*,
which has far lower variance than comparing two independent proportions and is the only
reason n=50 is a workable sample size. :func:`wilson_interval` puts an interval on one
arm's absolute rate; :func:`paired_bootstrap` puts one on a delta;
:func:`mcnemar_exact` gives the exact p-value over the discordant pairs, which is what
actually determines power.

**The honest headline that belongs on the slide: n=50 defends a difference of roughly 15
points and cannot defend one of 5.** Saying that before a judge asks is worth more than
an extra decimal place.

Everything is stdlib (``math``, ``random``) so ``aegis.evals`` stays dependency-light and
``tests/evals/test_isolation.py`` keeps holding.
"""

from __future__ import annotations

import math
import random
from collections.abc import Sequence
from dataclasses import dataclass

#: Number of bootstrap resamples. 10 000 is the IR convention; it is a constant rather
#: than a default argument nobody passes, so two runs are comparable by construction.
BOOTSTRAP_ITERATIONS = 10_000


def recall_at_k(hit_ranks: Sequence[Sequence[int]], k: int) -> float:
    """Fraction of a case's required spans that appear within the top ``k``.

    Args:
        hit_ranks: One sequence of 1-based ranks per *required span* — the positions in
            the ranked list whose chunk contains that span (empty when it was never
            retrieved).
        k: The cut-off.

    Returns:
        The fraction in ``[0, 1]``. For a single-gold case this is binary — the usual
        hit@k — which is what the great majority of the gold set is; multi-span cases are
        the only ones that can score a fraction, and they are reported as their own
        subset for exactly that reason.
    """
    if not hit_ranks:
        return 0.0
    found = sum(1 for ranks in hit_ranks if any(rank <= k for rank in ranks))
    return found / len(hit_ranks)


def precision_at_k(hit_ranks: Sequence[Sequence[int]], k: int) -> float:
    """Fraction of the top ``k`` delivered chunks that contain a required span.

    Args:
        hit_ranks: As :func:`recall_at_k`.
        k: The cut-off (the denominator, always — a short list is not a precise one).

    Returns:
        The fraction in ``[0, 1]``. With single-gold cases the arithmetic ceiling is
        ``1/k``, so this is a **relative** movement between arms and never an absolute
        quality claim — a chunk can only contain the gold span once, and the other five
        slots are structurally "wrong" even when they are the right passage's neighbours.
    """
    if k <= 0:
        return 0.0
    relevant = {rank for ranks in hit_ranks for rank in ranks if rank <= k}
    return len(relevant) / k


def reciprocal_rank(hit_ranks: Sequence[Sequence[int]], k: int) -> float:
    """``1 / rank`` of the first chunk containing any required span, else ``0``.

    Args:
        hit_ranks: As :func:`recall_at_k`.
        k: The cut-off beyond which a hit does not count.

    Returns:
        The reciprocal rank in ``[0, 1]``.
    """
    ranks = [rank for group in hit_ranks for rank in group if rank <= k]
    return 1.0 / min(ranks) if ranks else 0.0


def ndcg_at_k(hit_ranks: Sequence[Sequence[int]], k: int) -> float:
    """Position-discounted gain over binary relevance.

    Args:
        hit_ranks: As :func:`recall_at_k`.
        k: The cut-off.

    Returns:
        nDCG@k in ``[0, 1]``. The ideal ranking puts every required span in the first
        positions, so the denominator is the DCG of ``min(len(hit_ranks), k)`` hits at
        ranks 1..n. Degenerate for single-gold cases — see the module docstring.
    """
    if not hit_ranks or k <= 0:
        return 0.0
    relevant = sorted({rank for ranks in hit_ranks for rank in ranks if rank <= k})
    gain = sum(1.0 / math.log2(1 + rank) for rank in relevant)
    ideal_hits = min(len(hit_ranks), k)
    ideal = sum(1.0 / math.log2(1 + rank) for rank in range(1, ideal_hits + 1))
    return gain / ideal if ideal else 0.0


@dataclass(frozen=True)
class Interval:
    """A two-sided confidence interval and the sample it was computed from.

    Attributes:
        low: Lower bound.
        high: Upper bound.
        n: The sample size behind it — carried with the interval so a number can never be
            quoted without it.
    """

    low: float
    high: float
    n: int


def wilson_interval(successes: int, n: int, *, z: float = 1.96) -> Interval:
    """The Wilson score interval for a binomial proportion.

    Wilson rather than the normal-approximation ("Wald") interval, because Wald is badly
    wrong exactly where retrieval numbers live: at p near 1 with n around 50 it produces
    an upper bound above 1 and an interval whose true coverage is nowhere near 95%.

    Args:
        successes: Number of successes.
        n: Number of trials.
        z: The standard normal quantile (1.96 = 95%).

    Returns:
        The interval, clamped to ``[0, 1]``. ``n = 0`` yields ``(0, 1)`` — total
        ignorance, stated as such rather than as a point estimate of nothing.
    """
    if n <= 0:
        return Interval(0.0, 1.0, 0)
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return Interval(max(0.0, centre - half), min(1.0, centre + half), n)


def paired_bootstrap(
    deltas: Sequence[float],
    *,
    iterations: int = BOOTSTRAP_ITERATIONS,
    seed: int,
) -> Interval:
    """Percentile bootstrap interval for the mean paired difference between two arms.

    Paired, because both arms answered the same questions: resampling *cases* (carrying
    each case's difference with it) is what removes the between-question variance that
    would otherwise swamp a 50-case sample.

    Args:
        deltas: One per-case difference (arm B's score minus arm A's).
        iterations: Resamples. The default is the IR convention.
        seed: RNG seed — **required**, because a bootstrap interval that cannot be
            reproduced from the run artifact is not evidence.

    Returns:
        The 95% interval of the mean difference. An interval excluding zero is the claim
        worth making; one that includes it is the claim we do not make.
    """
    n = len(deltas)
    if n == 0:
        return Interval(0.0, 0.0, 0)
    rng = random.Random(seed)
    means: list[float] = []
    for _ in range(iterations):
        total = 0.0
        for _ in range(n):
            total += deltas[rng.randrange(n)]
        means.append(total / n)
    means.sort()
    low = means[int(0.025 * iterations)]
    high = means[min(iterations - 1, int(0.975 * iterations))]
    return Interval(low, high, n)


def mcnemar_exact(b: int, c: int) -> float:
    """Exact two-sided McNemar p-value over the discordant pairs.

    Args:
        b: Cases the first arm got right and the second did not.
        c: Cases the second arm got right and the first did not.

    Returns:
        The two-sided p-value from the binomial test at ``p = 0.5`` over ``b + c``
        discordant trials. ``1.0`` when there are no discordant pairs — two arms that
        never disagree carry no evidence that one is better, which is the correct
        reading and not a failure to compute.
    """
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / (2**n)
    return min(1.0, 2 * tail)
