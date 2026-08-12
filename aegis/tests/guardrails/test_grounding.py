"""Tests for the output grounding / hallucination self-check (advisory FLAG)."""

from __future__ import annotations

import pytest

from aegis.core.types import GuardVerdict
from aegis.guardrails.grounding import GroundingVerdict, check_grounding
from aegis.guardrails.pipeline import Guardrails


def completer_returning(raw: str):
    """A fake ChatCompleter that always returns ``raw``."""

    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        return raw

    return _c


async def _boom(messages, *, response_format=None):  # noqa: ANN001, ARG001
    raise RuntimeError("gateway down")


def output_completer(*, unsafe=False, grounded=True):
    """A fake answering the output self-checks (content-safety then grounding)."""

    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        system = messages[0]["content"].lower()
        if "groundedness" in system:
            return f'{{"grounded": {str(grounded).lower()}, "reason": "test"}}'
        return f'{{"unsafe": {str(unsafe).lower()}}}'

    return _c


CONTEXTS = ["Refunds are processed within 5 business days.", "Premium plans renew monthly."]


# ── check_grounding unit ──

@pytest.mark.asyncio
async def test_empty_contexts_is_noop_pass():
    v = await check_grounding("anything", [], completer=_boom)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_none_contexts_is_noop_pass():
    v = await check_grounding("anything", None, completer=_boom)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_whitespace_only_contexts_is_noop():
    v = await check_grounding("anything", ["   ", ""], completer=_boom)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_no_op_pass_when_no_completer():
    v = await check_grounding("answer", CONTEXTS, completer=None)
    assert v.grounded is True


@pytest.mark.asyncio
async def test_grounded_answer_passes():
    v = await check_grounding(
        "Refunds take 5 business days.",
        CONTEXTS,
        completer=completer_returning('{"grounded": true, "reason": "supported"}'),
    )
    assert v.grounded is True


@pytest.mark.asyncio
async def test_ungrounded_answer_flagged():
    v = await check_grounding(
        "Refunds take 30 days and cost a fee.",
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
async def test_pipeline_no_contexts_is_noop():
    guard = Guardrails(completer=output_completer(grounded=False), ground_answers=True)
    res = await guard.check_output("some answer")  # no contexts passed
    assert res.verdict is GuardVerdict.PASS


@pytest.mark.asyncio
async def test_pipeline_ungrounded_flags_but_does_not_block():
    guard = Guardrails(completer=output_completer(grounded=False), ground_answers=True)
    res = await guard.check_output("Refunds take 30 days.", CONTEXTS)
    assert res.verdict is GuardVerdict.FLAG and res.layer == "grounding"


@pytest.mark.asyncio
async def test_pipeline_grounded_passes():
    guard = Guardrails(completer=output_completer(grounded=True), ground_answers=True)
    res = await guard.check_output("Refunds take 5 business days.", CONTEXTS)
    assert res.verdict is GuardVerdict.PASS


@pytest.mark.asyncio
async def test_pipeline_block_mode_stops_ungrounded():
    guard = Guardrails(
        completer=output_completer(grounded=False), ground_answers=True, grounding_block=True
    )
    res = await guard.check_output("Refunds take 30 days.", CONTEXTS)
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "grounding"


@pytest.mark.asyncio
async def test_pipeline_content_safety_takes_precedence_over_grounding():
    guard = Guardrails(
        completer=output_completer(unsafe=True, grounded=False), ground_answers=True
    )
    res = await guard.check_output("Here is how to synthesize a bioweapon.", CONTEXTS)
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "content_safety"


def test_grounding_verdict_is_frozen():
    v = GroundingVerdict(grounded=False, reason="x")
    with pytest.raises(Exception):  # noqa: B017, PT011 - frozen dataclass
        v.grounded = True  # type: ignore[misc]
