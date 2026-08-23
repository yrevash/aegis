"""The stage seam, the suites, and the run record's registration.

The battery used to be one list fed to one rail. It now names which of the three
guardrail entry points each probe is aimed at, because an *indirect* prompt injection
pasted into ``check_input`` is not the attack it claims to be — the real one arrives in
a tool result, and screening it anywhere else measures the wrong rail.

The suites exist for the same reason a run is worth storing: history compares two runs
of *the same battery*, so the battery needs a stable name rather than a set of enum
values a caller assembled.

The last test is the one that would have caught the Phase-4 audit's finding five
times: a table that carries ``tenant_id`` and is not in the RLS registry has no policy,
and looks perfectly governed from the outside.
"""

from __future__ import annotations

import pytest

from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import pii
from aegis.redteam import ATTACK_BATTERY, Category, Expectation, run_redteam
from aegis.redteam.battery import SUITES, Stage, UnknownSuiteError, battery_for, suite_for
from aegis.redteam.runner import Rails, estimate_run


@pytest.fixture(autouse=True)
def _pin_regex_pii(monkeypatch):
    """Pin the PII engine to regex so offline verdicts are deterministic everywhere."""
    monkeypatch.setenv("AEGIS_PII_ENGINE", "regex")
    pii._reset_engine_cache()
    yield
    pii._reset_engine_cache()


# ── The stage seam ───────────────────────────────────────────────────────────


def test_the_battery_probes_every_rail():
    """A battery that only calls check_input reports on a fraction of the product.

    Four stages now, not three: ``INGEST`` is the write-time content gate, the only
    rail a corpus-poisoning attack ever meets. Equality rather than a subset, so a
    stage added to the enum without probes behind it fails here.
    """
    assert {a.stage for a in ATTACK_BATTERY} == set(Stage)


async def test_each_probe_is_screened_by_the_rail_its_stage_names():
    """The runner dispatches on the stage rather than sending everything inbound."""
    seen: dict[Stage, list[str]] = {stage: [] for stage in Stage}

    def recorder(stage: Stage):
        async def check(text, *, completer=None):
            seen[stage].append(text)
            return GuardResult(verdict=GuardVerdict.PASS, reason="stub", text=text)

        return check

    await run_redteam(
        rails=Rails(
            check_input=recorder(Stage.INPUT),
            check_output=recorder(Stage.OUTPUT),
            check_tool_result=recorder(Stage.TOOL_RESULT),
            check_ingest=recorder(Stage.INGEST),
        )
    )
    for stage in Stage:
        expected = [a.prompt for a in ATTACK_BATTERY if a.stage is stage]
        assert seen[stage] == expected, f"the {stage.value} rail saw the wrong probes"


async def test_a_single_injected_checker_still_answers_for_every_stage():
    """One fake means one fake: no real rail underneath supplying verdicts."""

    async def always_pass(text, *, completer=None):
        return GuardResult(verdict=GuardVerdict.PASS, reason="stub", text=text)

    report = await run_redteam(check=always_pass)
    assert all(r.verdict == GuardVerdict.PASS.value for r in report.results)
    assert report.block_rate == 0.0


async def test_the_indirect_injections_are_caught_at_the_tool_result_rail():
    """The Phase-5 rail, doing the thing it was built for, with its own verdict text."""
    probes = tuple(a for a in ATTACK_BATTERY if a.category is Category.INDIRECT_INJECTION)
    report = await run_redteam(battery=probes)
    caught = report.blocked
    assert caught, "the deterministic signatures must catch the signposted ones"
    for r in caught:
        assert r.attack.stage is Stage.TOOL_RESULT
        assert r.layer == "injection"
        assert "injection" in r.reason.lower()


async def test_the_report_names_the_rails_that_fired():
    report = await run_redteam()
    rails = dict(report.rails_that_fired())
    assert rails, "a report with blocks must be able to say which rail produced them"
    assert sum(rails.values()) == len(report.blocked)
    assert "unattributed" not in rails


# ── Suites ───────────────────────────────────────────────────────────────────


def test_every_suite_carries_the_benign_controls_whatever_it_asked_for():
    """A block rate without a false-positive rate is the number a vendor quotes."""
    for suite in SUITES:
        probes = battery_for(suite)
        assert any(a.category is Category.BENIGN_CONTROL for a in probes), suite.id
        assert any(a.expects is Expectation.BLOCK for a in probes), suite.id


def test_a_suite_selects_its_own_categories_and_nothing_else():
    probes = battery_for("excessive-agency")
    assert {a.category for a in probes} == {
        Category.EXCESSIVE_AGENCY,
        Category.BENIGN_CONTROL,
    }


def test_an_unknown_suite_is_refused_and_names_the_known_ones():
    with pytest.raises(UnknownSuiteError) as exc:
        suite_for("does-not-exist")
    assert "owasp-full" in str(exc.value)


async def test_every_suite_clears_its_own_offline_floor():
    """The floors are measured, not aspired to.

    A suite made mostly of semantic-only probes honestly reaches less offline, and
    stating one floor for all of them would make an honest run of that suite fail.
    """
    for suite in SUITES:
        report = await run_redteam(battery=battery_for(suite))
        assert report.block_rate >= suite.offline_floor, (
            f"{suite.id} blocks {report.block_rate:.0%} offline but claims a "
            f"{suite.offline_floor:.0%} floor"
        )
        assert report.false_positive_rate == 0.0, suite.id


# ── The cost estimate ────────────────────────────────────────────────────────


def test_an_offline_run_is_estimated_at_zero_model_calls():
    """Not a rounding-down: the deterministic backstops genuinely call nothing."""
    offline = estimate_run(ATTACK_BATTERY, live=False)
    assert offline.model_calls == 0
    assert offline.prompt_tokens == 0
    assert offline.probes == len(ATTACK_BATTERY)


def test_a_live_run_is_estimated_per_probe_and_per_stage():
    live = estimate_run(ATTACK_BATTERY, live=True)
    assert live.model_calls > live.probes, "each probe meets more than one model layer"
    assert live.prompt_tokens > 0
    # An outbound probe meets fewer model-backed layers than an inbound one, so the
    # estimate is not a flat multiple of the probe count.
    only_output = estimate_run(
        tuple(a for a in ATTACK_BATTERY if a.stage is Stage.OUTPUT), live=True
    )
    only_input = estimate_run(
        tuple(a for a in ATTACK_BATTERY if a.stage is Stage.INPUT)[: only_output.probes],
        live=True,
    )
    assert only_output.model_calls < only_input.model_calls


# ── The run record is registered for RLS in the same change ──────────────────


def test_the_run_record_is_registered_as_tenant_scoped():
    """A tenant-scoped table with no line in the registry gets no policy at all.

    :func:`aegis.governance.rls.bootstrap_rls` builds its DDL from
    :data:`~aegis.governance.rls._TENANT_SCOPED_TABLES`, so a new table that carries
    ``tenant_id`` and is missing from it is protected by the app-level ``WHERE``
    clause and nothing else — which holds right up until the one query that forgets
    it. This is the check the Phase-4 audit had to make by hand five times.
    """
    from aegis.governance.rls import _TENANT_SCOPED_TABLES
    from aegis.redteam.models import REDTEAM_RUNS_TABLE, RedTeamRun

    assert "tenant_id" in RedTeamRun.__table__.columns
    assert REDTEAM_RUNS_TABLE in _TENANT_SCOPED_TABLES


def test_importing_the_run_record_is_not_forced_on_the_leaf_harness():
    """``aegis.redteam`` stays importable without SQLAlchemy.

    ``models``/``store`` are deliberately not re-exported from the package: the
    harness is a leaf that anything may import to attack its own guardrails, and
    dragging an ORM into that import graph would end the property
    ``test_isolation.py`` asserts.
    """
    import aegis.redteam as harness

    assert not hasattr(harness, "RedTeamRun")
    assert "sqlalchemy" not in getattr(harness, "__all__", [])
