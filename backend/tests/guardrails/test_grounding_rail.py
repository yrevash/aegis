"""Backend output-grounding rail wiring (OWASP LLM09).

The process-wide ``app.guardrails._guard`` is now built with ``ground_answers=True``,
and ``app.guardrails.check_output`` forwards an optional ``contexts`` list to it. These
tests prove the end-to-end behaviour through the backend front door: given the retrieved
contexts, an ungrounded answer produces a non-blocking FLAG; a grounded answer passes;
and — crucially for every non-graph call site — omitting ``contexts`` is a grounding
no-op. The single network seam (``app.core.llm.complete``) is monkeypatched, so these run
with no gateway, no API key, and no NeMo install. The completer routes on the self-check
system prompt (content-safety vs. groundedness), mirroring ``aegis`` test_grounding.py.
"""

from __future__ import annotations

import pytest

import app.core.llm as llm_module
from app.api.schemas import GuardVerdict
from app.core.llm import LLMResult
from app.guardrails import check_output

CONTEXTS = ["Refunds are processed within 5 business days."]


def _mock_output_completer(monkeypatch: pytest.MonkeyPatch, *, grounded: bool) -> None:
    """Patch the gateway to answer both output self-checks (safety, then grounding)."""

    async def _fake(
        role, messages, *, tools=None, temperature=0.0, response_format=None, max_tokens=None
    ):
        system = messages[0]["content"].lower()
        if "groundedness" in system:
            return LLMResult(content=f'{{"grounded": {str(grounded).lower()}, "reason": "test"}}')
        return LLMResult(content='{"unsafe": false, "reason": "benign"}')

    monkeypatch.setattr(llm_module, "complete", _fake)


async def test_ungrounded_answer_flags_but_does_not_block(monkeypatch):
    _mock_output_completer(monkeypatch, grounded=False)
    result = await check_output("Refunds take 30 days and cost a fee.", CONTEXTS)
    assert result.verdict is GuardVerdict.FLAG
    assert result.layer == "grounding"
    # The flag is advisory — the answer text is carried through, not withheld.
    assert result.text == "Refunds take 30 days and cost a fee."


async def test_grounded_answer_passes(monkeypatch):
    _mock_output_completer(monkeypatch, grounded=True)
    result = await check_output("Refunds take 5 business days.", CONTEXTS)
    assert result.verdict is GuardVerdict.PASS


async def test_no_contexts_is_grounding_noop(monkeypatch):
    """Every non-graph call site (no contexts) must be a grounding no-op — unaffected."""
    # grounded=False, but with no contexts the grounding rail never runs, so PASS.
    _mock_output_completer(monkeypatch, grounded=False)
    result = await check_output("Refunds take 30 days and cost a fee.")
    assert result.verdict is GuardVerdict.PASS


async def test_empty_contexts_is_grounding_noop(monkeypatch):
    _mock_output_completer(monkeypatch, grounded=False)
    result = await check_output("Refunds take 30 days.", [])
    assert result.verdict is GuardVerdict.PASS
