"""Custom-rail extension seam: import, construct, and stream a domain rail.

This is the hackathon ergonomic — `from aegis.guardrails import Guardrails, Rail`,
write a rail, plug it in, and it runs in the chain AND streams to the console
with its own layer label, no pipeline fork.
"""

from __future__ import annotations

import pytest

from aegis.core.events import GuardrailEvent
from aegis.core.types import GuardResult, GuardVerdict
from aegis.guardrails import Guardrails, Rail


def block_competitor(text: str) -> GuardResult | None:
    """A tiny custom rail: block mentions of a competitor. Sync + returns None to abstain."""
    if "acme rival corp" in text.lower():
        return GuardResult(
            verdict=GuardVerdict.BLOCK,
            reason="Mentioned a blocked competitor.",
            text=text,
            layer="competitor_policy",
        )
    return None


async def async_no_medical(text: str) -> GuardResult | None:
    """An async custom rail (rails may be sync or async)."""
    if "prescribe" in text.lower():
        return GuardResult(
            verdict=GuardVerdict.BLOCK, reason="No medical advice.", text=text, layer="no_medical"
        )
    return None


def _benign():
    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        return '{"injection": false, "unsafe": false}'

    return _c


@pytest.mark.asyncio
async def test_custom_input_rail_blocks_and_tags_its_own_layer():
    guard = Guardrails(completer=_benign(), input_rails=[block_competitor])
    res = await guard.check_input("How do we beat ACME Rival Corp on pricing?")
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "competitor_policy"


@pytest.mark.asyncio
async def test_async_custom_rail_runs():
    guard = Guardrails(completer=_benign(), input_rails=[async_no_medical])
    res = await guard.check_input("Can you prescribe me antibiotics?")
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "no_medical"


@pytest.mark.asyncio
async def test_custom_rail_abstains_lets_clean_input_pass():
    guard = Guardrails(completer=_benign(), input_rails=[block_competitor])
    res = await guard.check_input("What is our standard closure window?")
    assert res.verdict is GuardVerdict.PASS


@pytest.mark.asyncio
async def test_custom_output_rail_blocks():
    guard = Guardrails(completer=_benign(), output_rails=[block_competitor])
    res = await guard.check_output("Our deal is better than ACME Rival Corp's.")
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "competitor_policy"


@pytest.mark.asyncio
async def test_custom_rail_verdict_streams_to_frontend():
    """The custom layer flows through the same GuardrailEvent the console renders."""
    guard = Guardrails(completer=_benign(), input_rails=[block_competitor])
    events = [e async for e in guard.stream_check_input("beat ACME Rival Corp")]
    verdicts = [e for e in events if isinstance(e, GuardrailEvent)]
    assert verdicts and verdicts[0].verdict == "block"
    assert verdicts[0].rules == ["competitor_policy"]


def test_rail_type_is_exported():
    assert Rail is not None
