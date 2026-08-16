"""Graph-level proof that the output rail now receives the retrieved contexts.

The output grounding self-check (``aegis.guardrails.grounding``) is unit-tested in
``tests/guardrails/test_grounding.py``. These tests prove the *wiring*: the
``guard_output`` node threads ``state["context"]`` (the same spotlighted passages the
answer was generated from) into ``deps.check_output(text, contexts=...)``, and an
ungrounded FLAG streams as a non-blocking advisory guardrail event without withholding
the answer. Fakes only — no gateway, no network.
"""

from __future__ import annotations

import pytest

from aegis.agent import run_agent
from aegis.core.types import GuardResult, GuardVerdict, RunStatus


@pytest.mark.asyncio
async def test_guard_output_receives_retrieved_contexts(make_deps):
    """The output rail is called with the retrieval context as a ``list[str]``."""
    deps = make_deps(propose_tool=False)
    seen: dict[str, object] = {}

    async def recording_check_output(
        text: str, contexts: list[str] | None = None
    ) -> GuardResult:
        seen["text"] = text
        seen["contexts"] = contexts
        return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)

    deps.check_output = recording_check_output

    async for _ in run_agent("what is the escalation policy?", deps=deps):
        pass

    # The retrieve fake sets answer_context="Spotlighted context about request R1.";
    # guard_output must forward exactly that, as a single-element list.
    assert seen["contexts"] == ["Spotlighted context about request R1."]
    assert seen["text"]  # the generated answer was screened


@pytest.mark.asyncio
async def test_ungrounded_answer_flags_but_does_not_block(make_deps):
    """An ungrounded FLAG streams as an advisory; the answer is still delivered."""
    deps = make_deps(propose_tool=False)

    async def ungrounded_check_output(
        text: str, contexts: list[str] | None = None
    ) -> GuardResult:
        return GuardResult(
            verdict=GuardVerdict.FLAG,
            reason="Ungrounded answer flagged: claim not in the retrieved sources.",
            text=text,
            layer="grounding",
        )

    deps.check_output = ungrounded_check_output

    events = [e async for e in run_agent("what is the escalation policy?", deps=deps)]
    types = [e["type"] for e in events]

    grounding_flags = [
        e
        for e in events
        if e["type"] == "guardrail"
        and e.get("layer") == "grounding"
        and e["verdict"] == "flag"
    ]
    assert len(grounding_flags) == 1  # the advisory reached the stream

    # Non-blocking: the answer is still streamed and the run completes normally.
    assert "token" in types
    assert events[-1]["status"] == RunStatus.COMPLETED.value


@pytest.mark.asyncio
async def test_grounding_flag_does_not_withhold_answer(make_deps):
    """A FLAG (unlike a BLOCK) leaves the answer text untouched, not withheld."""
    deps = make_deps(propose_tool=False)

    async def ungrounded_check_output(
        text: str, contexts: list[str] | None = None
    ) -> GuardResult:
        return GuardResult(
            verdict=GuardVerdict.FLAG, reason="ungrounded", text=text, layer="grounding"
        )

    deps.check_output = ungrounded_check_output

    tokens = [
        e["text"]
        async for e in run_agent("what is the escalation policy?", deps=deps)
        if e["type"] == "token"
    ]
    answer = "".join(tokens).strip()
    assert answer and "[response withheld" not in answer
