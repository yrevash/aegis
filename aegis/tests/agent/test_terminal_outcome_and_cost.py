"""What the terminal ``run_finished`` says about a run that did not end the easy way.

Two defects with one shape: the terminal event was being derived from something other
than what the run actually did, and the durable header
(:func:`aegis.runs.record.apply_event`) folds ``status``/``cost_usd``/tokens straight off
that event — so both wrong values became the record.

**Cost.** ``events.run_finished`` defaults every usage field to ``0.0``, and the
blocked/errored paths passed none of them. Measured on ``taif_run1``: a rejected run's
COST card read ``$0.0000`` while its ``usage_ledger`` rows held ``$0.035248`` over 23
calls. The completed path was not right either — it read LangGraph's reducers, which see
only what a node RETURNS as usage and therefore miss the guardrail screens, the injection
classifier, the grounding self-check and the query rewriter: ``$0.0172955`` reported
against ``$0.0181989`` metered during the same run.

**Outcome.** Three runs whose ``approvals`` row reads ``REJECTED``, decided by a named
human, were recorded ``status = ERROR`` — because after the refusal the graph makes one
more model call to phrase the refusal, and the provider's own filter refused *that*
(``finish_reason: content_filter``). A person declining an action is a decided outcome,
and it must not depend on a cosmetic call succeeding.
"""

from __future__ import annotations

import pytest

from aegis.core.run_context import accrue_run_usage, run_usage
from aegis.core.types import RiskLevel, RunStatus
from aegis.gateway.types import LLMResult, ToolCallResult, Usage

from .test_team_fanout import SIMPLE_QUERY, _drive, _one, build_team_deps

pytestmark = pytest.mark.anyio


def _meter(deps, *, cost: float, prompt: int = 100, completion: int = 10):
    """Wrap ``deps.complete`` so every call accrues like the gateway chokepoint does.

    The real accrual happens inside ``aegis.gateway.llm._record_usage``, beside the
    ``usage_ledger`` write and from the same numbers. These tests drive fakes rather
    than a gateway, so the accrual is made here — deliberately WITHOUT touching the
    usage the fake returns, because the whole point is that the two are different
    figures and the terminal event must report the metered one.
    """
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001
        accrue_run_usage(prompt_tokens=prompt, completion_tokens=completion, cost_usd=cost)
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    return deps


def _single_pass_proposer():
    """Deps whose PLANNER proposes the HIGH-risk write on a single-pass run.

    The three ERROR-instead-of-REJECTED runs on ``taif_run1`` all took this path
    (``plan → gate → approval → generate``), not the fan-out's, so this is the shape the
    regression is pinned on.
    """
    deps, rec = build_team_deps()
    inner = deps.complete
    proposed = False

    async def complete(role, messages, **kwargs):  # noqa: ANN001
        nonlocal proposed
        system = messages[0]["content"] if messages else ""
        user = messages[-1]["content"] if messages else ""
        planning = "You are Aegis." in system and "Write the final answer" not in user
        if planning and not proposed:
            proposed = True
            return LLMResult(
                content="",
                tool_calls=[
                    ToolCallResult(
                        id="call-1",
                        name="update_request_status",
                        args={"request_id": "REQ-1", "status": "closed"},
                    )
                ],
                usage=Usage(prompt_tokens=6, completion_tokens=3, cost_usd=0.0002),
                model="fake-cheap",
            )
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    return deps, rec


async def test_an_errored_run_reports_what_it_spent_before_it_failed():
    """The failure path used to emit no usage at all, which renders as $0.0000."""
    deps, _ = build_team_deps()
    _meter(deps, cost=0.002)

    async def exploding_check_output(text, contexts=None):  # noqa: ANN001, ARG001
        raise RuntimeError("the output rail's provider refused the call")

    deps.check_output = exploding_check_output
    events = await _drive(deps, SIMPLE_QUERY)

    finished = _one(events, "run_finished")
    assert finished["status"] == RunStatus.ERROR.value
    metered = run_usage(finished["run_id"])
    assert metered.calls > 0, "the fake never reached the chokepoint; test is not testing"
    assert finished["cost_usd"] == pytest.approx(metered.cost_usd)
    assert finished["cost_usd"] > 0.0, "a run that made model calls did not cost $0.0000"
    assert finished["prompt_tokens"] == metered.prompt_tokens


async def test_a_completed_run_reports_the_metered_cost_not_the_reducers():
    """The reducers see node returns; the ledger sees every call. Report the ledger."""
    deps, _ = build_team_deps()
    _meter(deps, cost=0.003)
    events = await _drive(deps, SIMPLE_QUERY)

    finished = _one(events, "run_finished")
    assert finished["status"] == RunStatus.COMPLETED.value
    metered = run_usage(finished["run_id"])
    assert metered.calls > 0, "the fake never reached the chokepoint; test is not testing"
    assert finished["cost_usd"] == pytest.approx(metered.cost_usd)
    # The fake ALSO returns its own per-node usage — $0.0001 a call, the figure the
    # reducers fold — and it is deliberately a different number from the metered one.
    # Reporting it is the under-report measured on real traffic.
    assert finished["cost_usd"] == pytest.approx(0.003 * metered.calls)
    assert finished["cost_usd"] != pytest.approx(0.0001 * metered.calls)


async def test_a_refused_gate_stays_rejected_when_the_refusal_text_cannot_be_generated():
    """The provider refusing to *phrase* a refusal must not rewrite the outcome.

    This is the failure mode, reproduced directly: the human declines, and the one
    remaining model call — the cosmetic one that writes "the action was not carried
    out" — raises. Before the fix the exception escaped ``generate``, the orchestrator's
    terminal handler caught it, and the run was recorded ``error``.
    """
    deps, rec = _single_pass_proposer()
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001
        user = messages[-1]["content"] if messages else ""
        if "NOT approved by the human gate" in user:
            raise RuntimeError(
                "Response content blocked by label 'Jailbreak'. (finish_reason: "
                "content_filter)"
            )
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    events = await _drive(deps, SIMPLE_QUERY, approve=False)

    assert _one(events, "approval_required")["risk"] == RiskLevel.HIGH.value
    assert rec.executed == [], "nothing may run after a refusal"
    assert _one(events, "run_finished")["status"] == RunStatus.REJECTED.value
    assert [e for e in events if e["type"] == "error"] == []
    # The user is still told what happened, in a sentence that needed no model.
    answer = "".join(e["text"] for e in events if e["type"] == "token")
    assert "declined it at the approval gate" in answer
    assert "Nothing was changed" in answer


async def test_a_generation_failure_on_an_approved_run_is_still_an_error():
    """The degradation is narrow on purpose: only the refusal path is cosmetic.

    Everywhere else the answer IS the run's product, so a run that could not produce
    one genuinely failed and must say so. The same gated run as above, approved rather
    than refused, exercises the same ``generate`` node — and must still report ``error``.
    Without this, the fix above would be free to turn every generation outage into a
    silent success.
    """
    deps, _ = _single_pass_proposer()
    inner = deps.complete

    async def complete(role, messages, **kwargs):  # noqa: ANN001
        user = messages[-1]["content"] if messages else ""
        if "Write the final answer for the user" in user:
            raise RuntimeError("the generation model is down")
        return await inner(role, messages, **kwargs)

    deps.complete = complete
    events = await _drive(deps, SIMPLE_QUERY, approve=True)

    assert _one(events, "run_finished")["status"] == RunStatus.ERROR.value
    assert _one(events, "error")["message"].endswith("the generation model is down")
