"""What ragas returns is not always a measurement, and the difference has to survive.

This module had no test at all, which is why two of the library's own routine outcomes
reached the evals panel as numbers nobody measured.

The panel's whole subject is not fabricating figures. A metric that reports a mean poisoned
by one NaN, or a mean dragged down by a judge outage recorded as 0.0, is the exact failure
the screen exists to make impossible — committed by the screen itself.
"""

from __future__ import annotations

import math

from aegis.evals.libs.ragas_suite import _usable


def test_nan_is_not_a_score() -> None:
    """`Faithfulness` returns NaN when the judge produced no statements.

    That is an ordinary LLM outcome, not an error. NaN is not `None`, so it used to enter
    the sample, count toward `cases`, and poison the mean — one NaN case turned the whole
    metric NaN even when every other case scored, and the panel then showed an empty cell
    with no note beside a claim that two cases contributed.
    """
    assert _usable(float("nan")) is None
    assert _usable(float("inf")) is None
    assert _usable(float("-inf")) is None


def test_a_zero_from_a_failed_judge_is_not_a_score() -> None:
    """`AnswerRelevancy` returns 0.0 when the judge generated no question to compare.

    A judge failure reported as perfect irrelevance. This module's own doctrine is that a
    zero is a measurement and not-running is not one; importing a zero from the library it
    trusts would be that same defect at one remove.
    """
    assert _usable(0.0) is None


def test_a_real_score_survives_untouched() -> None:
    """The anti-vacuity half. A guard that rejects everything measures nothing."""
    assert _usable(0.83) == 0.83
    assert _usable(1.0) == 1.0
    assert _usable(0.0001) == 0.0001


def test_a_non_numeric_value_does_not_raise() -> None:
    """A shape change in ragas must degrade to "not scored", never to a 500 on the panel."""
    assert _usable(None) is None
    assert _usable("nope") is None
    assert _usable(object()) is None


def test_the_guard_is_reachable_from_the_scoring_path() -> None:
    """The seam has to be wired, not merely present.

    Three declared-but-unbound seams shipped in this build before anyone checked, so the
    cheapest possible guard: the scoring path must actually call this function.
    """
    from pathlib import Path

    src = (
        Path(__file__).resolve().parents[2]
        / "src" / "aegis" / "evals" / "libs" / "ragas_suite.py"
    ).read_text()
    assert "_usable(faith.value)" in src and "_usable(rel.value)" in src, (
        "the guard exists but the scoring path does not go through it"
    )
    assert "float(faith.value)" not in src, "the raw float conversion is still in place"


def test_mean_of_a_partial_sample_is_over_what_was_kept() -> None:
    """Sanity on the arithmetic the guard feeds.

    With one case dropped, the mean must be over the survivors — not over the requested
    count with a zero filled in, which is the shape that made a judge outage look like a
    bad model.
    """
    kept = [v for v in (0.9, float("nan"), 0.7) if (u := _usable(v)) is not None]
    assert kept == [0.9, 0.7]
    assert math.isclose(sum(kept) / len(kept), 0.8)
