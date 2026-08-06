"""Release-gate wiring — the REAL eval_fn + durable approval_enqueue seams (offline).

Covers :mod:`app.ops.gate`:

* ``make_eval_fn`` returns a genuinely prompt-DEPENDENT scorer (a better prompt yields a
  better-graded answer), driven through an injected fake ``complete``;
* ``enqueue_release_approval`` writes a durable ``prompt_release`` Approval row and
  ``list_pending_releases`` reads it back;
* ``decide_release`` approve → promotes the draft (and flips the row APPROVED); reject →
  archives the draft (row REJECTED).
"""

from __future__ import annotations

import json

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine

from app.adapter import DEFAULT_PERSONA_ID
from app.core.llm import LLMResult, Usage
from app.core.models import ModelRole
from app.data.models import Approval, ApprovalStatus, PromptStatus
from app.data.session import bootstrap, configure_engine, get_sessionmaker
from app.ops import gate, registry

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID
GOOD = "You are a precise, grounded assistant. Cite the context for every claim."
BASE = "You are an assistant."


@pytest_asyncio.fixture
async def db(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'gate.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    registry.clear_cache()
    yield get_sessionmaker()
    registry.clear_cache()
    await engine.dispose()


def _prompt_scoring_complete(scores: dict[str, float]):
    """A fake gateway whose GENERATION echoes the system prompt and whose judge scores it.

    The generated answer embeds the system prompt, and the judge returns a per-prompt
    score keyed off it — so the eval genuinely varies with ``system_prompt``.
    """

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if role is ModelRole.GENERATION:
            system = messages[0]["content"]
            return LLMResult(content=f"ANSWER::{system}", usage=Usage())
        # REASONING judge: recover the prompt tag the generation embedded and score it.
        user = messages[-1]["content"]
        score = 0.0
        for prompt, value in scores.items():
            if f"ANSWER::{prompt}" in user:
                score = value
                break
        return LLMResult(
            content=json.dumps({"groundedness": score, "relevance": score}), usage=Usage()
        )

    return complete


async def test_make_eval_fn_is_prompt_dependent(db):
    eval_fn = gate.make_eval_fn(
        _prompt_scoring_complete({GOOD: 0.9, BASE: 0.3}), limit=2
    )
    good_score = await eval_fn(GOOD)
    base_score = await eval_fn(BASE)
    # A real, prompt-dependent signal: the better prompt scores strictly higher.
    assert good_score == pytest.approx(0.9)
    assert base_score == pytest.approx(0.3)
    assert good_score > base_score


async def test_enqueue_and_list_pending_releases(db):
    async with db() as s:
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=GOOD)
        await s.commit()
        draft_id = draft.id

    approval_id = await gate.enqueue_release_approval(
        prompt_key=PK, draft_version_id=draft_id, risk="high", reason="risky rewrite"
    )
    # A durable prompt_release row exists (decoupled synthetic run id).
    async with db() as s:
        row = await s.get(Approval, approval_id)
        assert row is not None
        assert row.action == "prompt_release"
        assert row.status is ApprovalStatus.PENDING
        assert row.run_id == f"prompt_release:{draft_id}"
        assert row.args["draft_version_id"] == draft_id

    pending = await gate.list_pending_releases(limit=50)
    assert any(p.approval_id == approval_id and p.draft_version_id == draft_id for p in pending)


async def test_decide_release_approve_promotes(db):
    async with db() as s:
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=GOOD)
        draft.status = PromptStatus.STAGED
        await s.commit()
        draft_id = draft.id
    approval_id = await gate.enqueue_release_approval(
        prompt_key=PK, draft_version_id=draft_id, risk="high", reason="stage it"
    )

    decision = await gate.decide_release(
        approval_id=approval_id, approved=True, decided_by="alice"
    )
    assert decision is not None
    assert decision.outcome == "promoted" and decision.prompt_key == PK
    assert decision.active_version == 1

    async with db() as s:
        active = await registry.get_active(s, PK)
        assert active is not None and active.id == draft_id
        assert active.status is PromptStatus.ACTIVE
        row = await s.get(Approval, approval_id)
        assert row.status is ApprovalStatus.APPROVED and row.decided_by == "alice"


async def test_decide_release_reject_archives(db):
    async with db() as s:
        active = await registry.create_draft(s, prompt_key=PK, system_prompt=BASE)
        await registry.promote(s, active.id)
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt=GOOD)
        draft.status = PromptStatus.STAGED
        await s.commit()
        active_id, draft_id = active.id, draft.id
    approval_id = await gate.enqueue_release_approval(
        prompt_key=PK, draft_version_id=draft_id, risk="high", reason="stage it"
    )

    decision = await gate.decide_release(approval_id=approval_id, approved=False)
    assert decision is not None and decision.outcome == "archived"

    async with db() as s:
        row = await s.get(Approval, approval_id)
        assert row.status is ApprovalStatus.REJECTED
        # The base version is still the live one; the draft was archived, not promoted.
        still_active = await registry.get_active(s, PK)
        assert still_active.id == active_id


async def test_decide_release_unknown_returns_none(db):
    assert await gate.decide_release(approval_id="nope", approved=True) is None

