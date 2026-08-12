"""Red-team harness tests — real verdicts from the real guardrail rail.

These run fully offline: the guardrail's deterministic backstops (injection
signatures, MLCommons hazard signatures, PII regex) block the egregious attacks
with no LLM, and the benign controls pass. The PII engine is pinned to the regex
backend so the false-positive measurement is deterministic on any machine (Presidio
may or may not be installed).
"""

from __future__ import annotations

import pytest

from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import pii
from aegis.redteam import (
    ATTACK_BATTERY,
    Category,
    Expectation,
    RedTeamReport,
    RedTeamThresholds,
    run_redteam,
)
from aegis.redteam.battery import Attack
from aegis.redteam.runner import AttackResult, _score


@pytest.fixture(autouse=True)
def _pin_regex_pii(monkeypatch):
    """Pin the PII engine to regex so offline verdicts are deterministic everywhere."""
    monkeypatch.setenv("AEGIS_PII_ENGINE", "regex")
    pii._reset_engine_cache()
    yield
    pii._reset_engine_cache()


# --- completers used to exercise the model layers -----------------------------
class _RecordingBenignCompleter:
    """A completer that judges everything benign (and records that it was called)."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, messages, *, response_format=None):
        self.calls += 1
        return (
            '{"injection": false, "unsafe": false, "categories": [], '
            '"on_topic": true, "grounded": true, "reason": "benign"}'
        )


class _AlwaysInjectionCompleter:
    """A paranoid completer that flags every model-layer check as an injection."""

    async def __call__(self, messages, *, response_format=None):
        return '{"injection": true, "reason": "flagged"}'


# --- battery shape ------------------------------------------------------------
def test_battery_ids_unique_and_well_formed():
    ids = [a.id for a in ATTACK_BATTERY]
    assert len(ids) == len(set(ids)), "attack ids must be unique"
    for a in ATTACK_BATTERY:
        assert a.id and a.prompt and a.owasp
        assert isinstance(a.category, Category)
        assert isinstance(a.expects, Expectation)


def test_battery_has_attacks_controls_and_semantic_only():
    attacks = [a for a in ATTACK_BATTERY if a.expects is Expectation.BLOCK]
    controls = [a for a in ATTACK_BATTERY if a.expects is Expectation.PASS]
    needs_llm = [a for a in ATTACK_BATTERY if a.needs_llm]
    assert len(attacks) >= 15
    assert len(controls) >= 5
    assert needs_llm, "battery must include semantic-only attacks the backstops miss"
    # every semantic-only probe is an attack, never a benign control
    assert all(a.expects is Expectation.BLOCK for a in needs_llm)


def test_all_owasp_categories_represented():
    cats = {a.category for a in ATTACK_BATTERY}
    assert {
        Category.PROMPT_INJECTION,
        Category.JAILBREAK,
        Category.SYSTEM_PROMPT_LEAK,
        Category.PII_EXTRACTION,
        Category.CONTENT_SAFETY,
        Category.BENIGN_CONTROL,
    } <= cats


# --- offline run: real deterministic verdicts ---------------------------------
async def test_offline_deterministic_attacks_are_blocked():
    report = await run_redteam()
    # Every attack NOT marked needs_llm must be neutralized offline by a backstop.
    for r in report.attack_results:
        if not r.attack.needs_llm:
            assert r.neutralized, f"{r.attack.id} should be blocked offline, got {r.verdict}"
            assert r.verdict in {GuardVerdict.BLOCK.value, GuardVerdict.REDACT.value}


async def test_offline_benign_controls_pass():
    report = await run_redteam()
    for r in report.control_results:
        assert r.success, f"benign {r.attack.id} misfired: {r.verdict} ({r.reason})"
    assert report.false_positive_rate == 0.0
    assert report.controls_total >= 5


async def test_offline_leaked_set_is_exactly_the_semantic_only_attacks():
    report = await run_redteam()
    leaked_ids = {r.attack.id for r in report.leaked}
    needs_llm_ids = {a.id for a in ATTACK_BATTERY if a.needs_llm}
    assert leaked_ids == needs_llm_ids


async def test_pii_attack_neutralized_via_redact():
    report = await run_redteam()
    pii_hits = [
        r for r in report.attack_results
        if r.attack.category is Category.PII_EXTRACTION and not r.attack.needs_llm
    ]
    assert pii_hits
    for r in pii_hits:
        assert r.verdict == GuardVerdict.REDACT.value
        assert r.neutralized


async def test_content_safety_signatures_report_hazard_layer():
    report = await run_redteam()
    cs = [
        r for r in report.attack_results
        if r.attack.category is Category.CONTENT_SAFETY and not r.attack.needs_llm
    ]
    assert cs
    for r in cs:
        assert r.verdict == GuardVerdict.BLOCK.value
        assert r.layer == "content_safety"


# --- report math --------------------------------------------------------------
async def test_block_rate_equals_blocked_over_total():
    report = await run_redteam()
    assert report.attacks_total == sum(
        1 for a in ATTACK_BATTERY if a.expects is Expectation.BLOCK
    )
    assert report.block_rate == report.attacks_blocked / report.attacks_total
    # 16 deterministic-catchable of 20 attacks -> 0.8 offline.
    assert report.attacks_blocked == report.attacks_total - len(report.leaked)


async def test_category_rollup_math():
    report = await run_redteam()
    for c in report.by_category():
        assert 0.0 <= c.block_rate <= 1.0
        assert c.blocked <= c.total
        if c.category != Category.BENIGN_CONTROL.value:
            assert c.blocked == c.total - len(c.leaked)


async def test_control_category_blockrate_is_false_positive_rate():
    report = await run_redteam()
    control = next(
        c for c in report.by_category() if c.category == Category.BENIGN_CONTROL.value
    )
    assert control.block_rate == report.false_positive_rate


# --- threshold flip -----------------------------------------------------------
async def test_threshold_flip_changes_pass_fail():
    report = await run_redteam()
    rate = report.block_rate
    strict = RedTeamReport(report.results, RedTeamThresholds(min_block_rate=rate + 0.01))
    lax = RedTeamReport(report.results, RedTeamThresholds(min_block_rate=max(0.0, rate - 0.01)))
    assert strict.passed is False
    assert lax.passed is True


async def test_false_positive_cap_gates_pass():
    report = await run_redteam()
    # With zero false positives offline, even a 0.0 cap passes on the FP axis.
    permissive = RedTeamReport(
        report.results,
        RedTeamThresholds(min_block_rate=0.0, max_false_positive_rate=0.0),
    )
    assert permissive.passed is True


# --- model-layer honesty ------------------------------------------------------
async def test_benign_completer_exercises_model_layer_without_new_catches():
    completer = _RecordingBenignCompleter()
    report = await run_redteam(completer=completer)
    # The model layer actually ran (completer was invoked on the passing prompts).
    assert completer.calls > 0
    # A compliant/fooled model adds no catches: semantic-only attacks still leak,
    # and it does not over-block the benign controls.
    leaked_ids = {r.attack.id for r in report.leaked}
    assert leaked_ids == {a.id for a in ATTACK_BATTERY if a.needs_llm}
    assert report.false_positive_rate == 0.0


async def test_flagging_completer_catches_semantic_attacks_but_overblocks():
    report = await run_redteam(completer=_AlwaysInjectionCompleter())
    # A model that flags everything catches the semantic-only attacks the backstops
    # miss -> 100% block rate; but it also hard-blocks the benign controls -> the
    # false-positive rate honestly climbs to 100%.
    assert report.block_rate == 1.0
    assert report.false_positive_rate == 1.0
    assert report.leaked == ()


# --- verdicts are real / never fabricated -------------------------------------
async def test_verdicts_are_real_guard_verdicts():
    report = await run_redteam()
    valid = {v.value for v in GuardVerdict}
    for r in report.results:
        assert r.verdict in valid


async def test_runner_records_injected_check_verdict_verbatim():
    """A custom checker's verdict flows into the report unchanged (no fabrication)."""

    async def always_pass(text, *, completer=None):
        return GuardResult(verdict=GuardVerdict.PASS, reason="stub", text=text)

    report = await run_redteam(check=always_pass)
    assert all(r.verdict == GuardVerdict.PASS.value for r in report.results)
    # Every attack leaks under a rail that passes everything — reported honestly.
    assert report.block_rate == 0.0
    assert len(report.leaked) == report.attacks_total
    assert report.passed is False


async def test_custom_battery_subset_runs():
    subset = tuple(a for a in ATTACK_BATTERY if a.category is Category.SYSTEM_PROMPT_LEAK)
    report = await run_redteam(battery=subset)
    assert report.attacks_total == len(subset)
    assert report.controls_total == 0
    assert report.block_rate == 1.0  # all leak signatures are deterministic


# --- scoring semantics --------------------------------------------------------
def test_score_benign_redact_is_not_a_false_positive():
    """A REDACT on a benign control is a privacy action, not a false positive."""
    benign = Attack(
        id="x", category=Category.BENIGN_CONTROL, owasp="-", prompt="hi",
        expects=Expectation.PASS,
    )
    redacted = GuardResult(verdict=GuardVerdict.REDACT, reason="pii", text="hi", layer="pii")
    scored = _score(benign, redacted)
    assert scored.success is True  # not blocked -> passes the control
    report = RedTeamReport((scored,))
    assert report.false_positive_rate == 0.0


def test_score_benign_block_is_a_false_positive():
    benign = Attack(
        id="y", category=Category.BENIGN_CONTROL, owasp="-", prompt="hi",
        expects=Expectation.PASS,
    )
    blocked = GuardResult(verdict=GuardVerdict.BLOCK, reason="x", text="hi", layer="schema")
    report = RedTeamReport((_score(benign, blocked),))
    assert report.false_positive_rate == 1.0
    assert len(report.false_positives) == 1


# --- as_dict / summary --------------------------------------------------------
async def test_as_dict_shape_is_lossless_and_json_ready():
    import json

    report = await run_redteam()
    d = report.as_dict()
    assert set(d) >= {
        "passed", "overall", "thresholds", "categories", "leaked",
        "falsePositiveDetail", "attacks",
    }
    assert d["overall"]["attacksTotal"] == report.attacks_total
    assert d["overall"]["blockRate"] == round(report.block_rate, 4)
    assert len(d["attacks"]) == len(report.results)
    assert len(d["leaked"]) == len(report.leaked)
    # round-trips through JSON (no non-serializable objects leaked in)
    json.dumps(d)


async def test_summary_reports_verdict_and_leaks():
    report = await run_redteam()
    text = report.summary()
    assert ("PASS" in text) or ("FAIL" in text)
    assert "attacks blocked" in text
    for r in report.leaked:
        assert r.attack.id in text
