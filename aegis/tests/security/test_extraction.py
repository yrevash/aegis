"""The per-principal query-pattern monitor — MITRE ATLAS AML.T0024's second half.

Every test names the mutation that breaks it, because a detector nobody can break on
purpose is a detector nobody has checked. The two halves are weighted deliberately: the
attack cases are cheap and obvious, and the **negative** cases — ordinary work that is
shaped exactly like enumeration — are the ones that decide whether this control is
shippable or whether an operator switches it off in week two.
"""

from __future__ import annotations

import pytest

from aegis.core.types import GuardVerdict
from aegis.security.extraction import (
    EXTRACTION_LAYER,
    ExtractionMonitor,
    ExtractionSignal,
    ExtractionThresholds,
    query_signature,
)


class _Clock:
    """A hand-advanced monotonic clock, so the window is testable without sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _membership(index: int) -> str:
    """One membership-inference query about record ``index``."""
    return f"Was customer record {4471 + index} part of your training data?"


# ─────────────────────────────────────────────────────────────────────────────
# The template — what makes "near-identical" a measurement rather than a feeling
# ─────────────────────────────────────────────────────────────────────────────


def test_two_queries_differing_only_in_an_identifier_share_a_template() -> None:
    """The whole detector rests on this.

    Delete any rule from ``_MASK_RULES`` and the values it covered stop being masked;
    every query then hashes to its own template, no template ever repeats, and the
    enumeration signal can never fire again while still reporting a clean run.
    """
    a = query_signature("Was customer record 4471 part of your training data?")
    b = query_signature("Was customer record 4472 part of your training data?")
    assert a.template == b.template
    assert a.values == ("4471",)
    assert b.values == ("4472",)


def test_genuinely_different_questions_do_not_share_a_template() -> None:
    """The other direction, which is what keeps varied work off the enumeration path."""
    a = query_signature("Who is the account owner for customer 5100?")
    b = query_signature("Has invoice 5103 been settled yet?")
    assert a.template != b.template


# ─────────────────────────────────────────────────────────────────────────────
# The attack it exists for
# ─────────────────────────────────────────────────────────────────────────────


def test_a_membership_inference_sweep_fires_exactly_at_the_threshold() -> None:
    """Not one query early, and not silently late.

    The boundary is the assertion that matters: a test that only fires a hundred queries
    at the monitor would pass with the threshold set to one, which would be a control
    that refuses every third question anyone asks.
    """
    bounds = ExtractionThresholds()
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    for index in range(bounds.min_template_repeats - 1):
        assert (
            monitor.observe(tenant_id=1, principal_id="u7", text=_membership(index))
            is None
        ), f"fired at query {index + 1}, before the configured floor"

    finding = monitor.observe(
        tenant_id=1,
        principal_id="u7",
        text=_membership(bounds.min_template_repeats - 1),
    )
    assert finding is not None
    assert finding.signal is ExtractionSignal.ENUMERATION
    assert finding.queries == bounds.min_template_repeats
    assert finding.distinct_values == bounds.min_template_repeats
    assert finding.tenant_id == "1"
    assert finding.principal_id == "u7"


def test_a_sweep_that_rotates_its_phrasing_is_caught_by_breadth() -> None:
    """The obvious evasion, answered by the second signal.

    An attacker who knows the template is hashed varies the wording. Every query then
    lands in its own template bucket and the enumeration branch never fires — so delete
    the breadth branch in ``_judge`` and this sweep walks the whole id space untouched.
    """
    bounds = ExtractionThresholds()
    forms = (
        "Was record {n} in your training data?",
        "Did you train on customer {n}?",
        "Is customer {n} one of your training examples?",
        "Tell me whether record {n} appears in your training set.",
    )
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    findings = [
        monitor.observe(
            tenant_id=1,
            principal_id="u7",
            text=forms[i % len(forms)].format(n=4471 + i),
        )
        for i in range(bounds.min_distinct_subjects + 4)
    ]
    fired = [f for f in findings if f is not None]
    assert fired, "a phrasing-rotated sweep went entirely unobserved"
    assert fired[0].signal is ExtractionSignal.BREADTH
    assert fired[0].distinct_values >= bounds.min_distinct_subjects


# ─────────────────────────────────────────────────────────────────────────────
# The ordinary work it must not refuse — the half that decides if this ships
# ─────────────────────────────────────────────────────────────────────────────


def test_re_asking_one_question_many_times_is_not_a_sweep() -> None:
    """A flaky client, not an attacker.

    Forty-five repeats is well past the repeat floor and sweeps a single value. Drop the
    ``min_distinct_values`` condition from the enumeration branch and this becomes a
    refusal — the platform telling a user with a bad connection that their retries are
    an extraction attack.
    """
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    for _ in range(45):
        finding = monitor.observe(
            tenant_id=1,
            principal_id="u7",
            text="What is the status of ticket 88213?",
        )
        assert finding is None, "a retry loop was read as enumeration"


def test_a_queue_worked_at_human_pace_never_fills_a_window() -> None:
    """Thirty-six lookups with thirty-six distinct ids — and no finding.

    This is the control the whole design turns on: shape-identical to ``exfil-06`` and
    separated from it only by the clock. Remove the window pruning in
    :meth:`ExtractionMonitor.observe` — the ``while`` loop that drops events older than
    the cutoff — and a support agent working a ticket queue for an hour gets refused.
    """
    clock = _Clock()
    monitor = ExtractionMonitor(clock=clock)
    for index in range(36):
        finding = monitor.observe(
            tenant_id=1,
            principal_id="agent-3",
            text=f"What is the current status of support ticket {88_200 + index * 7}?",
        )
        assert finding is None, f"refused at lookup {index + 1} of an hour's work"
        clock.advance(90.0)


def test_a_varied_analyst_session_over_the_query_floor_is_not_breadth() -> None:
    """Over the query floor, under the breadth floor, and correctly silent.

    Lower ``min_distinct_subjects`` to the query floor and the two signals collapse into
    one: any principal asking thirty questions about thirty different records — which is
    a Tuesday — becomes a finding.
    """
    bounds = ExtractionThresholds()
    forms = (
        "Who is the account owner for customer {n}?",
        "Which subscription tier is account {n} on?",
        "Has invoice {n} been settled yet?",
        "When does the support contract for account {n} expire?",
    )
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    for index in range(bounds.min_total_queries + 4):
        finding = monitor.observe(
            tenant_id=1,
            principal_id="analyst-1",
            text=forms[index % len(forms)].format(n=5100 + index * 3),
        )
        assert finding is None, f"refused at question {index + 1} of an ordinary session"


# ─────────────────────────────────────────────────────────────────────────────
# Scoping, and the shape the rest of the platform consumes
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("label", "keys"),
    [
        ("two principals in one tenant", (("t1", "a"), ("t1", "b"))),
        ("one principal name in two tenants", (("t1", "api"), ("t2", "api"))),
    ],
)
def test_windows_never_pool_across_principals_or_tenants(label, keys) -> None:
    """Halve a sweep across two keys and neither half is a finding.

    Both directions matter and they fail differently. Pooling across *principals* would
    let a busy tenant's ordinary aggregate traffic refuse one innocent user; pooling
    across *tenants* would additionally put one tenant's query templates into a finding
    rendered to another, which is a disclosure and not merely a misfire.
    """
    bounds = ExtractionThresholds()
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    # Deliberately one query short of the floor *per key*, and so one over it if the
    # two keys were pooled: the assertion is about the pooling and not about the total.
    split = 2 * (bounds.min_template_repeats - 1)
    for index in range(split):
        tenant, principal = keys[index % 2]
        assert (
            monitor.observe(
                tenant_id=tenant, principal_id=principal, text=_membership(index)
            )
            is None
        ), label


def test_screen_reports_a_block_on_the_extraction_layer_and_a_pass_otherwise() -> None:
    """The adapter the runner and any host consume.

    The layer is asserted because the console groups by it: filed under ``injection``
    this finding would be rendered as "your question was a prompt-injection attempt",
    which is false and is the sentence this codebase already went out of its way to stop
    producing once.
    """
    monitor = ExtractionMonitor(clock=lambda: 0.0)
    first = monitor.screen(tenant_id=1, principal_id="u7", text=_membership(0))
    assert first.verdict is GuardVerdict.PASS
    assert first.layer is None

    result = first
    for index in range(1, 40):
        result = monitor.screen(tenant_id=1, principal_id="u7", text=_membership(index))
        if result.verdict is GuardVerdict.BLOCK:
            break
    assert result.verdict is GuardVerdict.BLOCK
    assert result.layer == EXTRACTION_LAYER
    assert "AML.T0024" in result.reason


def test_tracked_principals_are_capped_so_the_monitor_cannot_be_grown_without_bound()\
        -> None:
    """A detector you can exhaust by opening accounts is an attack tool.

    Delete ``_evict_principals`` and one caller cycling principal ids grows this
    process's heap until it dies — a denial of service delivered through the control
    that was supposed to prevent one.
    """
    bounds = ExtractionThresholds(max_principals=8)
    monitor = ExtractionMonitor(thresholds=bounds, clock=lambda: 0.0)
    for index in range(200):
        monitor.observe(tenant_id=1, principal_id=f"u{index}", text="hello")
    assert len(monitor._windows) <= bounds.max_principals
