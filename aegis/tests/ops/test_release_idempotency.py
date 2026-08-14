"""A release decision must apply exactly once, however many times it arrives.

``decide_release`` used to apply the decision without ever checking that the durable
approval row was still ``PENDING``, and ``apply_release_decision`` had no status check
either (unlike ``release()``, which correctly refuses a non-DRAFT). So:

* a double-clicked approve called ``registry.promote`` twice; and
* a reject replayed *after* an approve archived the version that was by then ACTIVE,
  leaving the prompt key with **no active version at all** — every run silently
  falling back to the floor prompt.
"""

from __future__ import annotations

import pytest

from aegis.ops import gate, registry
from aegis.ops.models import PromptStatus, PromptVersion
from aegis.ops.release import apply_release_decision

from .conftest import DEFAULT_PERSONA_ID, FakeApproval, FakeApprovalStatus

PK = DEFAULT_PERSONA_ID


async def _staged_draft(db, *, with_active: bool = False) -> tuple[int, str]:
    """Create a STAGED draft (optionally over a live version) + its pending approval."""
    async with db() as s:
        if with_active:
            active = await registry.create_draft(s, prompt_key=PK, system_prompt="live")
            await registry.promote(s, active.id)
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt="candidate")
        draft.status = PromptStatus.STAGED
        await s.commit()
        draft_id = draft.id
    approval_id = await gate.enqueue_release_approval(
        prompt_key=PK, draft_version_id=draft_id, risk="high", reason="stage it"
    )
    return draft_id, approval_id


async def test_double_approve_promotes_once(db):
    """REGRESSION: a double-clicked approve must not re-run the promotion."""
    draft_id, approval_id = await _staged_draft(db)

    first = await gate.decide_release(approval_id=approval_id, approved=True, decided_by="a")
    second = await gate.decide_release(approval_id=approval_id, approved=True, decided_by="b")

    assert first is not None and first.outcome == "promoted"
    assert second is not None and second.outcome == "already_decided"
    assert second.approved is True  # the recorded decision, replayed back
    assert second.active_version == first.active_version

    async with db() as s:
        active = await registry.get_active(s, PK)
        assert active is not None and active.id == draft_id
        row = await s.get(FakeApproval, approval_id)
        # The first decider is recorded; the replay did not overwrite it.
        assert row.status is FakeApprovalStatus.APPROVED and row.decided_by == "a"


async def test_late_reject_after_approve_does_not_strand_the_prompt_key(db):
    """REGRESSION: the worst shape — a replayed reject archiving the now-ACTIVE version.

    That left the key with no active version, so every run silently dropped to the
    floor prompt.
    """
    draft_id, approval_id = await _staged_draft(db, with_active=True)

    approved = await gate.decide_release(approval_id=approval_id, approved=True)
    assert approved is not None and approved.outcome == "promoted"

    late = await gate.decide_release(approval_id=approval_id, approved=False)
    assert late is not None and late.outcome == "already_decided"
    assert late.approved is True

    async with db() as s:
        active = await registry.get_active(s, PK)
        assert active is not None, "the prompt key was stranded with no active version"
        assert active.id == draft_id and active.status is PromptStatus.ACTIVE
        row = await s.get(FakeApproval, approval_id)
        assert row.status is FakeApprovalStatus.APPROVED


async def test_double_reject_archives_once(db):
    draft_id, approval_id = await _staged_draft(db, with_active=True)

    first = await gate.decide_release(approval_id=approval_id, approved=False)
    second = await gate.decide_release(approval_id=approval_id, approved=False)

    assert first is not None and first.outcome == "archived"
    assert second is not None and second.outcome == "already_decided"
    assert second.approved is False

    async with db() as s:
        active = await registry.get_active(s, PK)
        assert active is not None and active.id != draft_id  # the old version is still live


async def test_apply_release_decision_refuses_a_non_staged_version(db):
    """Defence in depth: the underlying primitive guards on status too."""
    async with db() as s:
        active = await registry.create_draft(s, prompt_key=PK, system_prompt="live")
        await registry.promote(s, active.id)
        await s.commit()
        active_id = active.id

    async with db() as s:
        with pytest.raises(ValueError, match="not staged"):
            await apply_release_decision(s, draft_version_id=active_id, approved=False)
        # Still live: the refused decision changed nothing.
        assert (await s.get(PromptVersion, active_id)).status is PromptStatus.ACTIVE


async def test_a_refused_decision_leaves_the_approval_decidable(db):
    """If the version cannot be decided, the durable row must stay PENDING (fail closed)."""
    async with db() as s:
        draft = await registry.create_draft(s, prompt_key=PK, system_prompt="candidate")
        await s.commit()  # left as DRAFT — never staged
        draft_id = draft.id
    approval_id = await gate.enqueue_release_approval(
        prompt_key=PK, draft_version_id=draft_id, risk="high", reason="stage it"
    )

    with pytest.raises(ValueError, match="not staged"):
        await gate.decide_release(approval_id=approval_id, approved=True)

    async with db() as s:
        row = await s.get(FakeApproval, approval_id)
        assert row.status is FakeApprovalStatus.PENDING
        assert (await s.get(PromptVersion, draft_id)).status is PromptStatus.DRAFT
