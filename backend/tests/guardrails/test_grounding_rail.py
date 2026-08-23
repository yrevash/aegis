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

CONTEXTS = ["Closures are approved within 5 business days."]


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
    result = await check_output("Closures take 30 days and cost a fee.", CONTEXTS)
    assert result.verdict is GuardVerdict.FLAG
    assert result.layer == "grounding"
    # The flag is advisory — the answer text is carried through, not withheld.
    assert result.text == "Closures take 30 days and cost a fee."


async def test_grounded_answer_passes(monkeypatch):
    _mock_output_completer(monkeypatch, grounded=True)
    result = await check_output("Closures take 5 business days.", CONTEXTS)
    assert result.verdict is GuardVerdict.PASS


async def test_an_answer_with_no_contexts_is_flagged_not_passed(monkeypatch):
    """No retrieval is the finding, not a reason to skip the check.

    This asserted PASS on the reasoning that "with no contexts the grounding rail never
    runs". An audit found the cost: a run retrieved nothing, answered by citing a
    document id that exists in no corpus, and shipped with the output rail reporting a
    clean pass and the console reading "output checked".

    Advisory, and deliberately never a block — the answer is carried through, because
    plenty of legitimate turns answer with no retrieval (a refusal, a question about the
    conversation itself) and blocking those would make the rail unusable.
    """
    _mock_output_completer(monkeypatch, grounded=False)
    answer = "Closures take 30 days and cost a fee."
    for contexts in (None, []):
        result = (
            await check_output(answer)
            if contexts is None
            else await check_output(answer, contexts)
        )
        assert result.verdict is GuardVerdict.FLAG
        assert result.layer == "grounding"
        assert result.text == answer, "advisory: the answer is not withheld"


async def test_an_empty_answer_with_no_contexts_still_passes(monkeypatch):
    """There is no claim to be ungrounded, so there is nothing to report."""
    _mock_output_completer(monkeypatch, grounded=False)
    assert (await check_output("   ")).verdict is GuardVerdict.PASS
