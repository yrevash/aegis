"""Hand-worked fixtures for the retrieval metrics and the paired statistics.

Every expected value here is computed by hand in the test itself, because a metric module
checked against its own output proves only that it is deterministic.
"""

from __future__ import annotations

import math

import pytest

from aegis.evals.ir_metrics import (
    Interval,
    mcnemar_exact,
    ndcg_at_k,
    paired_bootstrap,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    wilson_interval,
)

#: One required span, retrieved at rank 3.
SINGLE = ((3,),)
#: Two required spans (a multi-hop case): one at rank 2, one never retrieved.
PARTIAL = ((2,), ())


def test_recall_at_k_is_binary_for_a_single_gold_span():
    assert recall_at_k(SINGLE, 20) == 1.0
    assert recall_at_k(SINGLE, 6) == 1.0
    assert recall_at_k(SINGLE, 2) == 0.0


def test_recall_at_k_is_a_fraction_only_for_a_multi_span_case():
    assert recall_at_k(PARTIAL, 20) == 0.5
    assert recall_at_k(PARTIAL, 1) == 0.0


def test_recall_of_a_case_with_no_required_spans_is_zero_not_one():
    """An unanswerable case must never score a free 1.0 by having nothing to find."""
    assert recall_at_k((), 20) == 0.0


def test_precision_at_k_divides_by_k_not_by_the_list_length():
    # One hit inside the top 6 → 1/6. The other five slots are wrong by construction,
    # which is the ceiling this metric is read against.
    assert precision_at_k(SINGLE, 6) == pytest.approx(1 / 6)
    # Two distinct hit positions inside the top 6.
    assert precision_at_k(((1,), (4,)), 6) == pytest.approx(2 / 6)


def test_precision_counts_a_chunk_once_even_if_it_holds_two_spans():
    assert precision_at_k(((2,), (2,)), 6) == pytest.approx(1 / 6)


def test_reciprocal_rank_is_one_over_the_first_hit():
    assert reciprocal_rank(SINGLE, 20) == pytest.approx(1 / 3)
    assert reciprocal_rank(((5,), (2,)), 20) == pytest.approx(1 / 2)
    assert reciprocal_rank(SINGLE, 2) == 0.0


def test_ndcg_matches_the_closed_form_for_a_single_gold_span():
    # Single gold, binary relevance: nDCG@k == 1/log2(1+rank). This is the degeneracy the
    # module docstring warns about, asserted rather than described.
    assert ndcg_at_k(SINGLE, 10) == pytest.approx(1 / math.log2(4))
    assert ndcg_at_k(((1,),), 10) == 1.0


def test_ndcg_is_not_degenerate_once_a_case_has_two_spans():
    # Hits at ranks 2 and 5; ideal is hits at 1 and 2.
    gain = 1 / math.log2(3) + 1 / math.log2(6)
    ideal = 1 / math.log2(2) + 1 / math.log2(3)
    assert ndcg_at_k(((2,), (5,)), 10) == pytest.approx(gain / ideal)


def test_wilson_interval_matches_published_values():
    # The textbook worked example: 40/50 = 0.80 → roughly 0.67–0.89.
    interval = wilson_interval(40, 50)
    assert interval.low == pytest.approx(0.6690, abs=1e-3)
    assert interval.high == pytest.approx(0.8880, abs=1e-3)
    assert interval.n == 50


def test_wilson_stays_inside_the_unit_interval_at_a_perfect_score():
    """Where the normal approximation would report an upper bound above 1."""
    interval = wilson_interval(50, 50)
    assert interval.high == 1.0
    assert 0.9 < interval.low < 1.0


def test_no_trials_is_total_ignorance_not_a_point_estimate():
    assert wilson_interval(0, 0) == Interval(0.0, 1.0, 0)


def test_the_bootstrap_is_bit_identical_under_a_fixed_seed():
    deltas = [i / 37 for i in range(-12, 25)]
    first = paired_bootstrap(deltas, iterations=2000, seed=20260830)
    second = paired_bootstrap(deltas, iterations=2000, seed=20260830)
    other = paired_bootstrap(deltas, iterations=2000, seed=1)

    assert first == second
    assert first != other, "a different seed must give a different resample"


def test_a_real_effect_produces_an_interval_that_excludes_zero():
    interval = paired_bootstrap([1.0] * 18 + [0.0] * 32, iterations=2000, seed=7)
    assert interval.low > 0


def test_a_wash_produces_an_interval_that_contains_zero():
    interval = paired_bootstrap([1.0] * 5 + [-1.0] * 5 + [0.0] * 40, iterations=2000, seed=7)
    assert interval.low < 0 < interval.high


def test_mcnemar_matches_the_binomial_by_hand():
    # 10 discordant pairs, all one-directional: 2 * (1/2)^10 = 0.001953125.
    assert mcnemar_exact(0, 10) == pytest.approx(2 * 0.5**10)
    # An even split cannot be evidence of anything.
    assert mcnemar_exact(5, 5) == 1.0
    assert mcnemar_exact(10, 0) == mcnemar_exact(0, 10)


def test_no_disagreement_is_reported_as_no_evidence():
    """Two arms that never disagree are not significantly different — p = 1, not p = 0."""
    assert mcnemar_exact(0, 0) == 1.0
