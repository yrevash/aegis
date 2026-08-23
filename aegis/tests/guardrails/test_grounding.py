"""Tests for the output grounding / hallucination self-check (advisory FLAG)."""

from __future__ import annotations

import pytest

from aegis.core.types import GuardVerdict
from aegis.guardrails.grounding import (
    GroundingVerdict,
    check_citation_integrity,
    check_grounding,
    retrieved_source_labels,
)
from aegis.guardrails.pipeline import Guardrails
from aegis.retrieval.spotlight import (
    build_spotlighted_context,
    spotlight_system_instruction,
)


def completer_returning(raw: str):
    """A fake ChatCompleter that always returns ``raw``."""

    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        return raw

    return _c


async def _boom(messages, *, response_format=None):  # noqa: ANN001, ARG001
    raise RuntimeError("gateway down")


def output_completer(*, unsafe=False, grounded=True, contradicted=False):
    """A fake answering the output self-checks (content-safety then grounding)."""

    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        system = messages[0]["content"].lower()
        if "groundedness" in system:
            return (
                f'{{"grounded": {str(grounded).lower()}, '
                f'"contradicted": {str(contradicted).lower()}, "reason": "test"}}'
            )
        return f'{{"unsafe": {str(unsafe).lower()}}}'

    return _c


CONTEXTS = ["Closures are approved within 5 business days.", "Premium plans renew monthly."]


# ── check_grounding unit ──

@pytest.mark.asyncio
async def test_an_answer_with_no_passages_is_not_reported_as_grounded():
    """No passages is the finding, not a reason to skip the check.

    This asserted ``grounded is True`` — "nothing to ground on, so pass". An audit found
    what that costs: a run retrieved nothing, answered by citing a document id that
    exists in no corpus (``DOC-REF-001``), and shipped with the output rail reporting a
    clean pass and the console reading "output checked". The same silence let an answer
    recalled from memory be presented as retrieved context.

    Still cheap and offline — ``_boom`` proves no model call is made on this branch.
    """
    for contexts in ([], None):
        v = await check_grounding("anything", contexts, completer=_boom)
        assert v.grounded is False, "an answer with nothing behind it is not grounded"
        assert "no passages" in v.reason.lower() or "nothing supports" in v.reason.lower()


@pytest.mark.asyncio
async def test_an_empty_answer_with_no_passages_is_still_a_pass():
    """There is no claim to be ungrounded, so there is nothing to report."""
    assert (await check_grounding("   ", [], completer=_boom)).grounded is True


@pytest.mark.asyncio
async def test_blank_passages_count_as_no_passages():
    """Whitespace is not evidence. Passages that hold nothing ground nothing.

    Same reasoning as the empty-list case: this used to pass, which meant a retrieval
    arm returning blank strings produced an answer reported as grounded in them.
    """
    v = await check_grounding("anything", ["   ", ""], completer=_boom)
    assert v.grounded is False


@pytest.mark.asyncio
async def test_no_op_pass_when_no_completer():
    v = await check_grounding("answer", CONTEXTS, completer=None)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_grounded_answer_passes():
    v = await check_grounding(
        "Closures take 5 business days.",
        CONTEXTS,
        completer=completer_returning('{"grounded": true, "reason": "supported"}'),
    )
    assert v.grounded is True


@pytest.mark.asyncio
async def test_ungrounded_answer_flagged():
    v = await check_grounding(
        "Closures take 30 days and cost a fee.",
        CONTEXTS,
        completer=completer_returning('{"grounded": false, "reason": "fee unsupported"}'),
    )
    assert v.grounded is False and "unsupported" in v.reason


@pytest.mark.asyncio
async def test_advisory_fails_open_on_completer_error():
    v = await check_grounding("a", CONTEXTS, completer=_boom, block=False)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_blocking_fails_closed_on_completer_error():
    v = await check_grounding("a", CONTEXTS, completer=_boom, block=True)
    assert v.grounded is False


@pytest.mark.asyncio
async def test_blocking_fails_closed_on_unparseable():
    v = await check_grounding(
        "a", CONTEXTS, completer=completer_returning("nonsense"), block=True
    )
    assert v.grounded is False


# ── pipeline integration ──

@pytest.mark.asyncio
async def test_pipeline_grounding_disabled_by_default():
    """ground_answers off ⇒ contexts are ignored; behaviour unchanged (PASS)."""
    guard = Guardrails(completer=output_completer(grounded=False))
    res = await guard.check_output("some answer", CONTEXTS)
    assert res.verdict is GuardVerdict.PASS


@pytest.mark.asyncio
async def test_pipeline_flags_an_answer_that_retrieved_nothing():
    """The rail must speak in the case it was silent for."""
    guard = Guardrails(completer=output_completer(grounded=False), ground_answers=True)
    res = await guard.check_output("some answer")  # no contexts passed
    assert res.verdict is GuardVerdict.FLAG and res.layer == "grounding"
    assert "retrieved no passages" in res.reason


@pytest.mark.asyncio
async def test_no_contexts_never_blocks_even_under_the_strict_posture():
    """FLAG, never BLOCK — otherwise the strict posture is unusable.

    Plenty of legitimate turns answer with no retrieval: a refusal, a question about the
    conversation itself. Blocking those would teach an operator to switch the rail off,
    which costs more than the flag is worth.
    """
    guard = Guardrails(
        completer=output_completer(grounded=False),
        ground_answers=True,
        grounding_block=True,
    )
    res = await guard.check_output("I cannot answer that.")
    assert res.verdict is GuardVerdict.FLAG, "a missing corpus must not block an answer"


@pytest.mark.asyncio
async def test_pipeline_ungrounded_flags_but_does_not_block():
    guard = Guardrails(completer=output_completer(grounded=False), ground_answers=True)
    res = await guard.check_output("Closures take 30 days.", CONTEXTS)
    assert res.verdict is GuardVerdict.FLAG and res.layer == "grounding"


@pytest.mark.asyncio
async def test_pipeline_grounded_passes():
    guard = Guardrails(completer=output_completer(grounded=True), ground_answers=True)
    res = await guard.check_output("Closures take 5 business days.", CONTEXTS)
    assert res.verdict is GuardVerdict.PASS


@pytest.mark.asyncio
async def test_pipeline_block_mode_stops_ungrounded():
    guard = Guardrails(
        completer=output_completer(grounded=False), ground_answers=True, grounding_block=True
    )
    res = await guard.check_output("Closures take 30 days.", CONTEXTS)
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "grounding"


@pytest.mark.asyncio
async def test_pipeline_content_safety_takes_precedence_over_grounding():
    guard = Guardrails(
        completer=output_completer(unsafe=True, grounded=False), ground_answers=True
    )
    res = await guard.check_output("Here is how to synthesize a bioweapon.", CONTEXTS)
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "content_safety"


# ── contradicted: the one grounding finding that blocks by default ──


@pytest.mark.asyncio
async def test_a_contradicted_answer_blocks_without_the_strict_posture():
    """The case that earns a block, and the reason the rail is not simply advisory.

    Retrieval *found* the fact — five business days — and the answer says thirty. The
    corpus is right there, disagreeing, and there is no legitimate turn of that shape.
    So it blocks with ``grounding_block`` unset, which is what the default deployment
    runs. Remove the ``verdict.contradicted or`` from
    ``Guardrails._screen_grounding`` and this drops back to a FLAG.
    """
    guard = Guardrails(
        completer=output_completer(grounded=False, contradicted=True),
        ground_answers=True,
    )
    res = await guard.check_output("Closures take 30 business days.", CONTEXTS)
    assert res.verdict is GuardVerdict.BLOCK, (
        "an answer the retrieved passages contradict must not be shipped with a citation"
    )
    assert res.layer == "grounding"
    assert "Contradicted answer blocked" in res.reason


@pytest.mark.asyncio
async def test_merely_unsupported_still_only_flags():
    """The other half of the split, and the reason the rail stays usable.

    Extrapolation and framing are the common case, most of them are fine, and a rail
    that blocks them is one an operator switches off. If this ever starts blocking,
    the split has collapsed back into a single boolean.
    """
    guard = Guardrails(
        completer=output_completer(grounded=False, contradicted=False),
        ground_answers=True,
    )
    res = await guard.check_output("Closures are usually quick.", CONTEXTS)
    assert res.verdict is GuardVerdict.FLAG
    assert "Ungrounded answer flagged" in res.reason


@pytest.mark.asyncio
async def test_no_contexts_never_reports_a_contradiction():
    """Nothing retrieved cannot contradict anything, so the block path is unreachable.

    This is what keeps the deliberate FLAG-only branch for the zero-retrieval case
    from being quietly re-armed by the new flag.
    """
    guard = Guardrails(
        completer=output_completer(grounded=False, contradicted=True),
        ground_answers=True,
        grounding_block=True,
    )
    res = await guard.check_output("I cannot answer that.")
    assert res.verdict is GuardVerdict.FLAG


@pytest.mark.asyncio
async def test_a_checker_that_could_not_answer_never_manufactures_a_contradiction():
    """The fail direction, and it is deliberately not symmetric with ``grounded``.

    A blocking rail treats an unparseable reply as ungrounded — fail closed, as before.
    It must never treat one as *contradicted*, because that hard-blocks in every mode:
    one gateway hiccup would take a working deployment's answers offline for a finding
    nobody made.
    """
    for raw in ("", "   ", "Yes and no — hard to say.", "not json at all"):
        for block in (False, True):
            verdict = await check_grounding(
                "Closures take 30 days.", CONTEXTS, completer=completer_returning(raw), block=block
            )
            assert verdict.contradicted is False, raw

    # …and the same when the checker is unreachable entirely.
    for block in (False, True):
        verdict = await check_grounding(
            "Closures take 30 days.", CONTEXTS, completer=_boom, block=block
        )
        assert verdict.contradicted is False


@pytest.mark.asyncio
async def test_a_checker_claiming_both_grounded_and_contradicted_is_not_a_block():
    """A self-contradictory verdict is resolved away from the harsher reading.

    This flag hard-blocks, so an inconsistent reply must not be read as the finding
    that stops an answer. ``grounded=true`` is taken at its word and the conjunction
    dropped.
    """
    verdict = await check_grounding(
        "Closures take 5 business days.",
        CONTEXTS,
        completer=completer_returning('{"grounded": true, "contradicted": true, "reason": "?"}'),
    )
    assert verdict.grounded is True
    assert verdict.contradicted is False


def test_grounding_verdict_is_frozen():
    v = GroundingVerdict(grounded=False, reason="x")
    with pytest.raises(Exception):  # noqa: B017, PT011 - frozen dataclass
        v.grounded = True  # type: ignore[misc]


# ── fail-closed fallback parsing (regression) ──

@pytest.mark.asyncio
async def test_prefix_shaped_reply_is_not_an_ungrounded_verdict():
    """A reply beginning with "no" is ambiguous, so the rail's own direction wins."""
    raw = "No part of this answer appears in the context."
    blocking = await check_grounding(
        "a", CONTEXTS, completer=completer_returning(raw), block=True
    )
    assert blocking.grounded is False  # blocking rail: fail closed
    advisory = await check_grounding(
        "a", CONTEXTS, completer=completer_returning(raw), block=False
    )
    assert advisory.grounded is True  # advisory rail: fail open


@pytest.mark.asyncio
async def test_prefix_shaped_yes_is_not_a_grounded_verdict():
    """The mirror defect: "Yes..." must not read as a clean grounded pass."""
    v = await check_grounding(
        "a",
        CONTEXTS,
        completer=completer_returning("Yes, partially, but claim two is unsupported."),
        block=True,
    )
    assert v.grounded is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "grounded"), [('"grounded": true', True), ('"grounded": false', False), ("no", False)]
)
async def test_unambiguous_grounding_fallback_still_parses(raw, grounded):
    v = await check_grounding("a", CONTEXTS, completer=completer_returning(raw), block=True)
    assert v.grounded is grounded


# ── citation integrity: the deterministic backstop that needs no model ──


#: Built by the real assembler, so this test breaks if the label format the model
#: actually reads ever changes. A hand-written "[source 1]" would keep passing while
#: production stopped matching, which is the failure mode a deterministic check has.
_TWO_SOURCES = build_spotlighted_context(
    ["Closures are approved within 5 business days.", "Premium plans renew monthly."]
)


def test_the_retrieved_labels_are_read_from_the_assembled_context():
    """The permitted set comes off the context the model was actually given."""
    assert retrieved_source_labels([_TWO_SOURCES]) == {1, 2}


def test_raw_passages_are_numbered_the_way_the_assembler_would_have_numbered_them():
    """A caller wiring the rail directly passes bare passages; those imply 1..N."""
    assert retrieved_source_labels(CONTEXTS) == {1, 2}
    assert retrieved_source_labels([]) == set()
    assert retrieved_source_labels(None) == set()


def test_an_answer_that_cites_nothing_is_not_a_citation_failure():
    """This check must never become a rule that answers have to carry citations."""
    verdict = check_citation_integrity("Closures take five business days.", [_TWO_SOURCES])
    assert verdict.ok and verdict.cited == ()


def test_a_citation_beyond_what_was_retrieved_is_found_with_no_model():
    verdict = check_citation_integrity("Closures take 5 days [source 5].", [_TWO_SOURCES])
    assert verdict.fabricated == (5,)
    assert verdict.retrieved == (1, 2)
    assert "[source 5]" in verdict.reason()


@pytest.mark.asyncio
async def test_a_fabricated_citation_is_blocked_with_no_completer_wired_at_all():
    """The load-bearing test: this control has to be worth something with no model.

    ``completer=None`` is the deployment the recorded gap was about — the grounding rail
    is a semantic entailment judgement, so with nothing to judge with it was a no-op and
    the whole control was worth nothing. The answer below cites ``[source 4]`` when the
    run retrieved two passages; that is false, and it is provably false with arithmetic.

    Remove the ``check_citation_integrity`` call from ``Guardrails._screen_grounding``
    and this returns PASS.
    """
    guard = Guardrails(completer=None, ground_answers=True)

    res = await guard.check_output(
        "Closures are approved within five business days [source 4].", [_TWO_SOURCES]
    )

    assert res.verdict is GuardVerdict.BLOCK
    assert res.layer == "grounding"
    assert "Fabricated citation blocked" in res.reason
    assert "[source 4]" in res.reason


@pytest.mark.asyncio
async def test_a_citation_the_run_really_retrieved_is_not_blocked():
    """The positive control: a correct citation must cost nothing.

    Without this the test above is satisfied by a rail that blocks every cited answer,
    which would be a worse deployment than no rail at all.
    """
    guard = Guardrails(completer=None, ground_answers=True)

    res = await guard.check_output(
        "Closures are approved within five business days [source 1].", [_TWO_SOURCES]
    )

    assert res.verdict is not GuardVerdict.BLOCK
    assert "Fabricated citation" not in res.reason


@pytest.mark.asyncio
async def test_a_zero_retrieval_answer_without_a_citation_is_still_only_flagged():
    """The deliberate FLAG-never-BLOCK decision, guarded against this addition.

    Plenty of legitimate turns answer with no passages — a refusal, a question about the
    conversation itself — and blocking them withheld legitimate refusals. Adding a
    citation check must not quietly re-block that branch, so it is asserted here with
    ``grounding_block`` on, which is the strictest posture a deployment can run.
    """
    guard = Guardrails(completer=None, ground_answers=True, grounding_block=True)

    res = await guard.check_output("I cannot help with that.", None)

    assert res.verdict is GuardVerdict.FLAG
    assert res.layer == "grounding"


@pytest.mark.asyncio
async def test_a_zero_retrieval_answer_that_cites_a_source_is_blocked():
    """The audited failure, in the half a machine can check.

    The run that started this retrieved nothing and answered by citing ``DOC-REF-001``,
    a document id in no corpus, under a clean output-rail verdict. Retrieving nothing is
    still only a FLAG (above); *claiming a source while having none* is a different
    sentence and a provable falsehood, and it stops here.
    """
    guard = Guardrails(completer=None, ground_answers=True)

    res = await guard.check_output("The policy is 30 days [source 1].", None)

    assert res.verdict is GuardVerdict.BLOCK
    assert res.layer == "grounding"
    assert "retrieved no passages at all" in res.reason


def test_the_model_is_told_to_cite_exactly_the_labels_this_check_verifies():
    """The prompt and the checker must name one format, or neither of them is a control.

    A deterministic check is only worth something if the thing it checks is something
    the model was asked to produce. ``spotlight_system_instruction`` declares the
    ``[source N]`` labels as the citation vocabulary and forbids citing one that is not
    present; this asserts the two halves still agree, so a reworded prompt cannot quietly
    turn the backstop into a check on a format nobody uses.
    """
    instruction = spotlight_system_instruction()
    assert "[source N]" in instruction
    assert "never cite" in instruction.lower()

    # The label shape the instruction describes is the label shape the checker reads.
    assert check_citation_integrity("as stated in [source 2]", [_TWO_SOURCES]).cited == (2,)
