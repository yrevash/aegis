"""The release gate must FAIL CLOSED — it may never promote on a measurement it lacks.

The bug these cover: :func:`aegis.evals.judge._parse_verdict` used to swallow every
JSON/type/key error and return ``JudgeVerdict(0.0, 0.0)``. :func:`aegis.ops.gate.make_eval_fn`
then averaged those to ``0.0`` for the draft *and* the baseline, and
:func:`aegis.ops.release.release` tests ``draft < baseline + margin`` with the default
``eval_margin=0.0`` — so ``0.0 < 0.0`` is False, the gate PASSED, and a ``low``-risk draft
under ``tiered`` autonomy was auto-promoted **live**. A total judge outage promoted every
candidate prompt.

The judge runs on ``ModelRole.REASONING``, whose models routinely wrap their JSON in prose
or a ``<think>`` preamble — so this is the expected failure mode, not an exotic one.
"""

from __future__ import annotations

import json

import pytest

from aegis.core.models import ModelRole
from aegis.evals.judge import JudgeUnavailableError, _parse_verdict, judge_answer
from aegis.gateway import LLMResult, Usage
from aegis.ops import gate, registry
from aegis.ops.models import PromptStatus
from aegis.ops.release import release

from .conftest import DEFAULT_PERSONA_ID

PK = DEFAULT_PERSONA_ID
BASE = "\n".join(f"instruction line {i}" for i in range(1, 9))
LOW_DRAFT = BASE.replace("instruction line 2", "instruction line two")


def _judge_replying(content: str):
    """A fake gateway whose GENERATION answers and whose REASONING judge replies ``content``."""

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if role is ModelRole.GENERATION:
            return LLMResult(content="an answer", usage=Usage())
        return LLMResult(content=content, usage=Usage())

    return complete


# ── 1. The parser distinguishes "judge failed" from "scored zero" ─────────────


def test_unparseable_judge_reply_raises_instead_of_scoring_zero():
    """REGRESSION: prose is a judge FAILURE, not a 0.0 verdict."""
    with pytest.raises(JudgeUnavailableError):
        _parse_verdict("I think the answer is quite good, honestly.")


def test_a_genuine_zero_is_still_a_zero():
    """A real ``0.0`` verdict must stay parseable — the fix must not swallow real zeros."""
    verdict = _parse_verdict('{"groundedness": 0.0, "relevance": 0.0}')
    assert (verdict.groundedness, verdict.relevance) == (0.0, 0.0)


@pytest.mark.parametrize(
    "reply",
    [
        '<think>Let me weigh this up…</think>{"groundedness": 0.8, "relevance": 0.9}',
        '```json\n{"groundedness": 0.8, "relevance": 0.9}\n```',
        'Here is my verdict: {"groundedness": 0.8, "relevance": 0.9}. Hope that helps!',
    ],
    ids=["think-preamble", "markdown-fence", "prose-wrapped"],
)
def test_reasoning_model_formatting_drift_still_parses(reply: str):
    """The routine REASONING-model wrappers are drift to tolerate, not a judge outage."""
    verdict = _parse_verdict(reply)
    assert verdict.groundedness == pytest.approx(0.8)
    assert verdict.relevance == pytest.approx(0.9)


@pytest.mark.parametrize("reply", ["", "null", "{}", '{"groundedness": "high"}',
                                   '{"groundedness": NaN, "relevance": NaN}'])
def test_unusable_replies_all_fail_closed(reply: str):
    with pytest.raises(JudgeUnavailableError):
        _parse_verdict(reply)


async def test_judge_answer_propagates_the_failure():
    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        return LLMResult(content="<think>hmm, I am not sure at all", usage=Usage())

    with pytest.raises(JudgeUnavailableError):
        await judge_answer("q", "ctx", "a", complete=complete)


# ── 2. The gate cannot pass on a judge outage ─────────────────────────────────


async def test_eval_fn_does_not_return_zero_on_a_judge_outage(db):
    """``make_eval_fn`` must raise, not hand the gate an equal-and-fabricated 0.0."""
    eval_fn = gate.make_eval_fn(_judge_replying("total gibberish, no JSON here"), limit=1)
    with pytest.raises(JudgeUnavailableError):
        await eval_fn(BASE)


async def test_judge_outage_cannot_auto_promote_a_low_risk_draft(db):
    """THE bug: a broken judge scored draft and baseline 0.0, so ``0.0 < 0.0`` PASSED.

    A low-risk change under the default ``tiered`` autonomy was then promoted live with
    no working quality measurement behind it.
    """
    async with db() as s:
        active = await registry.create_draft(s, prompt_key=PK, system_prompt=BASE)
        await registry.promote(s, active.id)
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=LOW_DRAFT)
        await s.commit()
        draft_id, active_id = draft.id, active.id

    eval_fn = gate.make_eval_fn(_judge_replying("the judge is down"), limit=1)
    staged: list = []

    async def approval_enqueue(**kwargs):  # noqa: ANN003
        staged.append(kwargs)
        return "appr-x"

    async with db() as s:
        with pytest.raises(JudgeUnavailableError):
            await release(
                s,
                draft_version_id=draft_id,
                eval_fn=eval_fn,
                approval_enqueue=approval_enqueue,
                autonomy="tiered",
            )

    # Nothing moved: the draft is still a DRAFT and the old version is still live.
    async with db() as s:
        assert (await s.get(type(draft), draft_id)).status is PromptStatus.DRAFT
        still_active = await registry.get_active(s, PK)
        assert still_active is not None and still_active.id == active_id
    assert staged == []


async def test_a_non_finite_eval_score_cannot_pass_the_gate(db):
    """``NaN < NaN + margin`` is False — a NaN score must be refused, not promoted."""
    async with db() as s:
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=LOW_DRAFT)
        await s.commit()
        draft_id = draft.id

    async def nan_eval(system_prompt: str) -> float:
        return float("nan")

    async def approval_enqueue(**kwargs):  # noqa: ANN003
        raise AssertionError("must not stage")

    async with db() as s:
        with pytest.raises(ValueError, match="non-finite"):
            await release(
                s,
                draft_version_id=draft_id,
                eval_fn=nan_eval,
                approval_enqueue=approval_enqueue,
            )

    async with db() as s:
        assert (await registry.get_active(s, PK)) is None


async def test_a_working_judge_still_promotes(db):
    """Control: the fail-closed change must not break the happy path."""

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if role is ModelRole.GENERATION:
            system = messages[0]["content"]
            return LLMResult(content=f"ANSWER::{system}", usage=Usage())
        user = messages[-1]["content"]
        score = 0.9 if f"ANSWER::{LOW_DRAFT}" in user else 0.3
        return LLMResult(
            content=json.dumps({"groundedness": score, "relevance": score}), usage=Usage()
        )

    async with db() as s:
        active = await registry.create_draft(s, prompt_key=PK, system_prompt=BASE)
        await registry.promote(s, active.id)
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=LOW_DRAFT)
        await s.commit()
        draft_id = draft.id

    async def approval_enqueue(**kwargs):  # noqa: ANN003
        raise AssertionError("low risk should auto-promote, not stage")

    async with db() as s:
        result = await release(
            s,
            draft_version_id=draft_id,
            eval_fn=gate.make_eval_fn(complete, limit=1),
            approval_enqueue=approval_enqueue,
        )
        await s.commit()
    assert result.outcome == "promoted"
    assert result.eval_score > result.baseline_score
