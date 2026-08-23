"""The four MITRE ATLAS families the battery named but did not exercise.

Each family is a real attack with a real verdict from a real rail, and each one is
here because the ATLAS row it answers said plainly that nothing tested it:

* **AML.T0020 Poison Training Data** — documents attacking the corpus at write time,
  screened by the gate that is the only rail a poisoning attack ever meets.
* **AML.T0024 Exfiltration via AI Inference API** — the answer used as a *channel*,
  screened by the outbound exfiltration rail. Two probes in this family deliberately
  leak, and this module asserts that they do, because a suite that quietly dropped
  them would be claiming a control nobody built.
* **AML.T0043 Craft Adversarial Data** — the same override perturbed until the
  signature detector stops matching it.
* **AML.T0053 LLM Plugin Compromise** — what a hostile MCP peer hands back. The
  end-to-end half lives in ``backend/tests/mcp/test_hostile_peer.py``, which serves
  these exact payloads from a real in-process MCP server.

**The mutation that breaks each claim** is named on the test. That is the whole
point of the file: a red-team probe the platform happens to pass is decoration, and
the way to tell the difference is to know which line, deleted, turns it red.
"""

from __future__ import annotations

import pytest

from aegis.core.types import GuardVerdict
from aegis.guardrails import pii
from aegis.redteam.battery import ATTACK_BATTERY, Category, Expectation, Stage, battery_for
from aegis.redteam.runner import run_redteam


@pytest.fixture(autouse=True)
def _pin_regex_pii(monkeypatch):
    """Pin the PII engine to regex so offline verdicts are deterministic everywhere.

    Not cosmetic. Presidio's PERSON model fires on base32 and on punctuated text, and
    a REDACT counts as *neutralized* — so with Presidio installed a probe can be
    scored a catch by a name detector that saw a name in a random blob. Every verdict
    below is meant to come from the rail the probe is aimed at.
    """
    monkeypatch.setenv("AEGIS_PII_ENGINE", "regex")
    pii._reset_engine_cache()
    yield
    pii._reset_engine_cache()


def _family(category: Category):
    """Return the probes in one category."""
    return tuple(a for a in ATTACK_BATTERY if a.category is category)


async def _run(category: Category):
    """Run one family through the real rails and return the report."""
    return await run_redteam(battery=_family(category))


# ─────────────────────────────────────────────────────────────────────────────
# AML.T0020 — Poison Training Data
# ─────────────────────────────────────────────────────────────────────────────


async def test_poisoned_documents_are_refused_before_the_store():
    """The write-time gate, driven by the battery rather than by a unit call.

    Delete any of the branches in ``aegis.retrieval.validation.validate_content`` —
    the injection patterns, the size ceiling, the non-printable ratio — and the
    corresponding probe leaks and this fails.
    """
    report = await _run(Category.DATA_POISONING)
    assert report.attacks_total >= 5
    for result in report.attack_results:
        if result.attack.needs_llm:
            continue
        assert result.neutralized, f"{result.attack.id} reached the store"
        assert result.verdict == GuardVerdict.BLOCK.value
        assert result.layer == "ingest", (
            f"{result.attack.id} was stopped by {result.layer!r}, not the write gate — "
            "the probe is measuring the wrong rail"
        )


async def test_the_poisoning_probes_go_to_the_ingest_rail_not_the_input_rail():
    """Pasting a poisoned handbook page into ``check_input`` is a different test.

    It measures the injection signatures a second time and says nothing about whether
    the corpus can be poisoned, which is what AML.T0020 asks.
    """
    assert {a.stage for a in _family(Category.DATA_POISONING)} == {Stage.INGEST}


async def test_a_poisoned_fact_carrying_no_instruction_gets_through_and_is_declared():
    """The honest limit of a write-time gate, asserted rather than omitted.

    A poisoned document does not have to carry an instruction. A plausible false
    *policy* in ordinary prose is indistinguishable from a real policy update to any
    deterministic gate. The probe is in the battery, it is marked semantic-only, and
    it leaks — which is the accurate statement of what this control does and does not
    do.
    """
    report = await _run(Category.DATA_POISONING)
    leaked = {r.attack.id for r in report.leaked}
    assert leaked == {"poison-06"}
    probe = next(a for a in ATTACK_BATTERY if a.id == "poison-06")
    assert probe.needs_llm is True, "a leak the battery does not declare is a hidden failure"


# ─────────────────────────────────────────────────────────────────────────────
# AML.T0024 — Exfiltration via AI Inference API
# ─────────────────────────────────────────────────────────────────────────────


async def test_an_answer_that_is_a_channel_is_blocked_on_the_way_out():
    """The mechanism: ``schema.exfiltration_channel`` on the outbound rail.

    Remove it from ``Guardrails.check_output`` and all three channel probes leak.
    Note the layer assertion — before this rail existed these payloads passed every
    other outbound screen, because none of them is about the *words*.
    """
    report = await _run(Category.INFERENCE_EXFIL)
    channel = [
        r for r in report.attack_results if r.attack.stage is Stage.OUTPUT
    ]
    assert len(channel) == 3
    for result in channel:
        assert result.neutralized, f"{result.attack.id} carried its payload out"
        assert result.verdict == GuardVerdict.BLOCK.value
        assert result.layer == "exfiltration"


async def test_model_extraction_and_membership_inference_leak_and_are_declared():
    """A real finding, reported rather than curated out of the suite.

    Nothing in a text rail can distinguish the five-hundredth probing question from
    the first legitimate one. What bounds these is the gateway's rate and spend caps
    — a different control, claimed under LLM10 and not here — so the probes stay,
    marked semantic-only, and the suite's floor is set at the reach it actually has.
    """
    report = await _run(Category.INFERENCE_EXFIL)
    leaked = {r.attack.id for r in report.leaked}
    assert leaked == {"exfil-04", "exfil-05"}
    for probe_id in leaked:
        probe = next(a for a in ATTACK_BATTERY if a.id == probe_id)
        assert probe.needs_llm is True
        assert probe.stage is Stage.INPUT


async def test_an_ordinary_documentation_link_is_not_read_as_a_channel():
    """The false-positive side, which is what makes the rail shippable.

    A model citing a real URL is the normal case. The finding is the *shape* — an
    external host with a long opaque payload in the query, path or fragment — and a
    rail that could not tell those apart would be switched off within a week.
    """
    report = await run_redteam(battery=battery_for("inference-exfil"))
    assert report.false_positive_rate == 0.0
    controls = [r for r in report.control_results if r.attack.stage is Stage.OUTPUT]
    assert controls, "the outbound benign control is the one that matters here"
    for result in controls:
        assert result.verdict != GuardVerdict.BLOCK.value


# ─────────────────────────────────────────────────────────────────────────────
# AML.T0043 — Craft Adversarial Data
# ─────────────────────────────────────────────────────────────────────────────


async def test_every_encoded_perturbation_is_screened_as_the_instruction():
    """Four encodings that each walked past the rail before the decoder learned them.

    Delete a branch from ``aegis.guardrails.classifier._decoded_candidates`` and the
    matching probe leaks. The layer assertion is load-bearing: a catch by the PII or
    schema rail would be a coincidence, not evasion resistance.
    """
    report = await _run(Category.ADVERSARIAL_EVASION)
    for result in report.attack_results:
        if result.attack.needs_llm:
            continue
        assert result.neutralized, f"{result.attack.id} evaded the detector"
        assert result.layer == "injection", (
            f"{result.attack.id} was caught by {result.layer!r}; that is not the "
            "detector this family is attacking"
        )


async def test_a_paraphrased_override_still_gets_through_and_says_so():
    """Where the deterministic layer genuinely ends, stated as a leak.

    No encoding to decode, no signature to match. This is the boundary between the
    two injection layers, and it belongs in the report rather than in a docstring.
    """
    report = await _run(Category.ADVERSARIAL_EVASION)
    assert {r.attack.id for r in report.leaked} == {"adv-05"}


# ─────────────────────────────────────────────────────────────────────────────
# AML.T0053 — LLM Plugin Compromise
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_hostile_peers_strings_are_refused_at_the_tool_result_rail():
    """The three surfaces a compromised peer writes on, screened where each arrives.

    The description and the argument schema arrive at *discovery* and land verbatim
    in the planner's system prompt; the result arrives at *call* time. All three go
    to ``check_tool_result``, which is the rail the MCP client screens them with —
    see ``app.mcp.client._screen_schema`` and ``ExternalToolRegistry.call``.
    """
    report = await _run(Category.PLUGIN_COMPROMISE)
    for result in report.attack_results:
        assert result.attack.stage is Stage.TOOL_RESULT
        if result.attack.needs_llm:
            continue
        assert result.neutralized, f"{result.attack.id} entered the agent's context"
        assert result.layer == "injection"


async def test_a_plausible_wrong_answer_from_a_peer_leaks_and_is_declared():
    """A compromised peer does not have to inject anything.

    A well-formed invoice with the attacker's remittance address carries no override,
    no forged role and no signature to match. The rail passes it, correctly, and the
    control that answers it is the HIGH tier and the human gate — asserted end to end
    in ``backend/tests/mcp/test_hostile_peer.py``, not here.
    """
    report = await _run(Category.PLUGIN_COMPROMISE)
    assert {r.attack.id for r in report.leaked} == {"peer-04"}


# ─────────────────────────────────────────────────────────────────────────────
# The families are part of the battery, not a sidecar
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_new_families_run_with_the_full_battery_and_move_its_block_rate():
    """A probe suite nothing runs is decoration.

    ``owasp-full`` is what an operator gets by pressing the button, and all four
    families are in it — so their leaks are in the headline number and cannot be
    quietly excluded from the report the jury reads.
    """
    full = battery_for("owasp-full")
    for category in (
        Category.DATA_POISONING,
        Category.INFERENCE_EXFIL,
        Category.ADVERSARIAL_EVASION,
        Category.PLUGIN_COMPROMISE,
    ):
        assert any(a.category is category for a in full), (
            f"{category.value} is not in the default run"
        )

    report = await run_redteam(battery=full)
    without = [
        r
        for r in report.attack_results
        if r.attack.category
        not in {
            Category.DATA_POISONING,
            Category.INFERENCE_EXFIL,
            Category.ADVERSARIAL_EVASION,
            Category.PLUGIN_COMPROMISE,
        }
    ]
    old_rate = sum(1 for r in without if r.neutralized and r.checked) / len(without)
    assert report.block_rate != old_rate, (
        "the new families changed nothing in the headline, which would mean they are "
        "either all passing or all failing exactly like the old battery"
    )


async def test_every_new_family_has_its_own_selectable_suite_with_a_measured_floor():
    """An operator can run one family, and its floor is what it actually reaches.

    A single floor across every suite would make an honest run of the exfiltration
    family — where two of five probes are not a text-rail problem at all — read as a
    failure, which is how a floor gets lowered for everyone instead.
    """
    for suite_id in ("data-poisoning", "inference-exfil", "adversarial-evasion",
                     "plugin-compromise"):
        report = await run_redteam(battery=battery_for(suite_id))
        from aegis.redteam.battery import suite_for

        suite = suite_for(suite_id)
        assert report.block_rate >= suite.offline_floor, (
            f"{suite_id} blocks {report.block_rate:.0%} against a claimed "
            f"{suite.offline_floor:.0%} floor"
        )
        assert report.false_positive_rate == 0.0, suite_id
        assert any(a.expects is Expectation.PASS for a in battery_for(suite_id))
