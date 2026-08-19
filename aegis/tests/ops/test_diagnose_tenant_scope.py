"""Diagnose runs in ONE tenant's scope — its failures, its prompt, its draft.

``diagnose`` called ``registry.get_active(session, prompt_key)`` with no tenant and read
``eval_results`` with no tenant predicate. After §7.7 the first of those means *the
platform row explicitly* — strictly safer than the old "whichever tenant's row came back
first", but still wrong in the way that matters: the self-improvement loop reasoned about
the **platform** prompt while the tenant's runs had been served the tenant's own. The
second was worse than wrong: a tenant's draft was written from judge critiques quoting
every other tenant's failing runs, and those critiques go verbatim into the optimizer
prompt.

Two tests, one per half of the claim, each written so it fails on the pre-fix code.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from sqlalchemy import select

from aegis.ops import registry
from aegis.ops.diagnose import diagnose
from aegis.ops.models import EvalResult, PromptVersion

from .conftest import DEFAULT_PERSONA_ID

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID
_TENANT = 7101
_OTHER = 7102


@dataclass
class _FakeResult:
    content: str


def _capturing_complete(seen: list[str]):
    """A fake optimizer that records the prompt it was shown and returns a usable draft."""

    async def complete(role, messages, *, response_format=None):  # noqa: ANN001, ARG001
        seen.append(messages[-1]["content"])
        return _FakeResult('{"system_prompt": "IMPROVED", "rationale": "r"}')

    return complete


def _failing(metric: str, *, tenant_id: int | None, critique: str) -> EvalResult:
    return EvalResult(
        run_id=f"run-{tenant_id}-{critique}",
        prompt_key=PK,
        tenant_id=tenant_id,
        metric=metric,
        score=0.2,
        passed=False,
        detail={"critique": critique},
    )


async def test_the_base_prompt_is_the_tenants_own_active_version(db):
    """The prompt being improved is the one that actually ran for this tenant.

    Both scopes hold an active version of the same key. Unscoped, ``get_active`` reads
    the platform row and the optimizer is asked to improve a prompt this tenant never
    ran — and the draft lands in the platform scope, where every *other* tenant without
    a version of its own would inherit it.
    """
    async with db() as s:
        platform = await registry.create_draft(
            s, prompt_key=PK, system_prompt="PLATFORM BASE", tenant_id=None
        )
        await registry.promote(s, platform.id)
        mine = await registry.create_draft(
            s, prompt_key=PK, system_prompt="TENANT BASE", tenant_id=_TENANT
        )
        await registry.promote(s, mine.id)
        s.add(_failing("answer", tenant_id=_TENANT, critique="mine"))
        await s.commit()

    seen: list[str] = []
    async with db() as s:
        result = await diagnose(
            s, prompt_key=PK, complete=_capturing_complete(seen), tenant_id=_TENANT
        )
        await s.commit()

    assert "TENANT BASE" in seen[0]
    assert "PLATFORM BASE" not in seen[0], (
        "the loop improved a prompt this tenant's runs were never served"
    )

    async with db() as s:
        draft = await s.get(PromptVersion, result.draft_version_id)
        assert draft.tenant_id == _TENANT, "the draft must belong to the tenant it is for"
        assert draft.parent_version == mine.version


async def test_another_tenants_failures_never_reach_the_optimizer(db):
    """A tenant's diagnose reads its own failing rows and nobody else's.

    The critique text is quoted verbatim into the optimizer prompt, so an unscoped read
    is a cross-tenant content leak, not only a miscount.
    """
    async with db() as s:
        active = await registry.create_draft(
            s, prompt_key=PK, system_prompt="BASE", tenant_id=_TENANT
        )
        await registry.promote(s, active.id)
        s.add(_failing("answer", tenant_id=_TENANT, critique="MY-SECRET-CASE"))
        s.add(_failing("answer", tenant_id=_OTHER, critique="THEIR-SECRET-CASE"))
        s.add(_failing("answer", tenant_id=None, critique="PLATFORM-CASE"))
        await s.commit()

    seen: list[str] = []
    async with db() as s:
        result = await diagnose(
            s, prompt_key=PK, complete=_capturing_complete(seen), tenant_id=_TENANT
        )
        await s.commit()

    assert result.failures_considered == 1
    assert "MY-SECRET-CASE" in seen[0]
    assert "THEIR-SECRET-CASE" not in seen[0]
    assert "PLATFORM-CASE" not in seen[0]
    # The denominator is scoped too: a rate of 1/3 would be this tenant's one failure
    # measured against everybody's graded rows.
    assert result.metric_totals["answer"] == 1
    assert result.metric_rates["answer"] == 1.0


async def test_the_platform_scope_is_the_platform_rows_not_every_row(db):
    """``tenant_id=None`` means ``tenant_id IS NULL``, never "any tenant"."""
    async with db() as s:
        s.add(_failing("answer", tenant_id=_TENANT, critique="TENANT-CASE"))
        s.add(_failing("answer", tenant_id=None, critique="PLATFORM-CASE"))
        await s.commit()

    seen: list[str] = []
    async with db() as s:
        await diagnose(s, prompt_key=PK, complete=_capturing_complete(seen), tenant_id=None)
        await s.commit()

    assert "PLATFORM-CASE" in seen[0]
    assert "TENANT-CASE" not in seen[0]

    async with db() as s:
        drafts = (
            await s.execute(select(PromptVersion).where(PromptVersion.prompt_key == PK))
        ).scalars().all()
    assert [d.tenant_id for d in drafts] == [None]
