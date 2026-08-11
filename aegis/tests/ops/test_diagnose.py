"""Diagnose tests: cluster failing evals → write an improved-prompt DRAFT (offline).

Covers the contract:

* seeded failing ``EvalResult`` rows → a DRAFT ``PromptVersion`` parented to the active
  version, with the failure ``metric_breakdown`` tallied correctly;
* no failures → ``draft_version_id=None`` and no registry write;
* no active version → the injected floor is the base and the draft has no parent;
* a malformed optimizer response → no draft, no crash (defensive parse).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import func, select

from aegis.core.models import ModelRole
from aegis.ops import registry
from aegis.ops.diagnose import diagnose
from aegis.ops.models import EvalResult, PromptStatus, PromptVersion

from .conftest import DEFAULT_PERSONA_ID

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID


@dataclass
class _FakeResult:
    content: str


def _fake_complete(content: str, *, calls: list | None = None):
    async def complete(role, messages, *, response_format=None):  # noqa: ANN001
        if calls is not None:
            calls.append((role, response_format))
        return _FakeResult(content)

    return complete


async def _seed_failures(session, counts: dict[str, int]) -> None:
    """Add ``counts[metric]`` failing rows (+ one passing control that must be ignored)."""
    for metric, n in counts.items():
        for i in range(n):
            session.add(
                EvalResult(
                    run_id=f"run-{metric}-{i}",
                    prompt_key=PK,
                    metric=metric,
                    score=0.2,
                    passed=False,
                    detail={"critique": f"{metric} was weak on run {i}"},
                )
            )
    session.add(
        EvalResult(run_id="ok", prompt_key=PK, metric="answer", score=0.9, passed=True, detail={})
    )
    await session.flush()


async def test_diagnose_writes_draft_parented_to_active_with_breakdown(db):
    calls: list = []
    counts = {"answer": 3, "step:retrieval": 2, "step:tool": 1}
    async with db() as s:
        # Establish an active version so the draft parents to it.
        active = await registry.create_draft(s, prompt_key=PK, system_prompt="BASE PROMPT")
        await registry.promote(s, active.id)
        await _seed_failures(s, counts)
        await s.commit()

    async with db() as s:
        result = await diagnose(
            s,
            prompt_key=PK,
            complete=_fake_complete(
                '{"system_prompt": "IMPROVED PROMPT", "rationale": "fixes grounding"}',
                calls=calls,
            ),
        )
        await s.commit()

    # Optimizer was called on the REASONING role in JSON mode.
    assert calls and calls[0][0] is ModelRole.REASONING
    assert calls[0][1] == {"type": "json_object"}

    # Breakdown tallies only the failing rows.
    assert result.metric_breakdown == counts
    assert result.failures_considered == sum(counts.values())
    assert result.draft_version_id is not None

    async with db() as s:
        draft = await s.get(PromptVersion, result.draft_version_id)
        assert draft.status is PromptStatus.DRAFT
        assert draft.system_prompt == "IMPROVED PROMPT"
        assert draft.created_by == "diagnose"
        assert draft.parent_version == 1  # parented to the active version's number
        assert draft.version == 2


async def test_diagnose_uses_injected_floor_when_no_active(db):
    async with db() as s:
        await _seed_failures(s, {"answer": 2})
        await s.commit()
    async with db() as s:
        result = await diagnose(
            s, prompt_key=PK, complete=_fake_complete('{"system_prompt": "NEW"}')
        )
        await s.commit()
    assert result.draft_version_id is not None
    async with db() as s:
        draft = await s.get(PromptVersion, result.draft_version_id)
        assert draft.parent_version is None  # no active ⇒ floor ⇒ no parent


async def test_diagnose_no_failures_writes_no_draft(db):
    async with db() as s:
        # Only a passing row — nothing to fix.
        s.add(EvalResult(run_id="ok", metric="answer", score=0.9, passed=True, detail={}))
        await s.commit()
    called = False

    async def complete(role, messages, *, response_format=None):  # noqa: ANN001
        nonlocal called
        called = True
        return _FakeResult("{}")

    async with db() as s:
        result = await diagnose(s, prompt_key=PK, complete=complete)
        await s.commit()

    assert result.draft_version_id is None
    assert result.failures_considered == 0
    assert result.metric_breakdown == {}
    assert called is False  # never bothers the optimizer when there is nothing to fix
    async with db() as s:
        n = (await s.execute(select(func.count()).select_from(PromptVersion))).scalar()
    assert n == 0


async def test_diagnose_bad_optimizer_response_writes_no_draft(db):
    async with db() as s:
        await _seed_failures(s, {"answer": 2})
        await s.commit()
    async with db() as s:
        result = await diagnose(
            s, prompt_key=PK, complete=_fake_complete("this is not json at all")
        )
        await s.commit()
    assert result.draft_version_id is None
    assert result.failures_considered == 2  # failures were still counted
    async with db() as s:
        n = (await s.execute(select(func.count()).select_from(PromptVersion))).scalar()
    assert n == 0  # no draft written on a garbage response
