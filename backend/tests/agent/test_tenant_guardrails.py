"""Do the ``guardrails.*`` settings actually reach the rails a request runs through?

The whole defect was that they did not. Four keys — ``guardrails.grounding.block``,
``guardrails.topical.block``, ``guardrails.denylist.terms`` and
``guardrails.pii.entities`` — were in the catalogue, writable by a tenant admin, saved,
audited and badged "Your setting" on the settings screen, and **no rail read any of
them**. The two toggles were host-wired from ``app.config.Settings``, which no tenant
can see; the two collections had no consumer anywhere in the codebase.

The cause is the same one ``agent.gate_min_risk`` had: :data:`app.guardrails._guard` is
a process-wide pipeline built once and *synchronously* at import, while settings
resolution is per tenant and *async*. So the policy is folded on per request, in
:func:`app.guardrails._request_guard`.

This test is deliberately the whole vertical — a real settings row through the real
resolver, the real governance context a request binds, and the real
:func:`app.guardrails.check_input` the agent graph calls — and it asserts on **the
rail's verdict**, not on a resolved value. Asserting that a policy object holds a term
would pass just as happily against a rail that never looks at it, which is the bug.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.core.types import GuardVerdict
from aegis.governance.context import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from aegis.governance.rls import set_tenant_scope
from aegis.settings import SettingScope, write_setting

import app.core.llm as llm_module
from app.api.schemas import Role
from app.core.llm import LLMResult
from app.data import Tenant, User, get_sessionmaker

pytestmark = pytest.mark.asyncio

_TENANT = 7821
_USER = 78211
_OTHER_TENANT = 7822
_OTHER_USER = 78221

#: The question. Innocuous to the platform's own rails; confidential to one tenant.
_QUESTION = "Summarise the Project-Zephyr launch plan."


async def _seed_tenants() -> None:
    """Two tenants with one admin each — the FKs a tenant-scoped settings row needs."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT, name="Denylist tenant"),
            Tenant(id=_OTHER_TENANT, name="Bystander tenant"),
            User(id=_USER, username="denylist-admin", role=Role.ADMIN, tenant_id=_TENANT),
            User(
                id=_OTHER_USER,
                username="bystander-admin",
                role=Role.ADMIN,
                tenant_id=_OTHER_TENANT,
            ),
        )
        await session.commit()


async def _tenant_admin_denies(term: str) -> None:
    """Add one denied term the way the tenant admin's settings request would."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        await write_setting(
            session,
            "guardrails.denylist.terms",
            [term],
            scope=SettingScope.TENANT,
            actor_role="tenant_admin",
            tenant_id=_TENANT,
            actor_user_id=_USER,
        )
        await session.commit()


def _offline_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the one network seam so the model-backed layers all return a clean verdict.

    The same seam ``backend/tests/guardrails/test_rails.py`` uses. Without it the
    injection screen is unavailable and the rail fails closed on *every* input, which
    would make a BLOCK say nothing about the tenant's denylist.
    """

    async def _fake(  # noqa: ANN202, PLR0913
        role, messages, *, tools=None, temperature=0.0, response_format=None, max_tokens=None
    ):
        return LLMResult(
            content='{"injection": false, "unsafe": false, "on_topic": true, "reason": "ok"}'
        )

    monkeypatch.setattr(llm_module, "complete", _fake)


async def _screen_as(tenant_id: int, user_id: int):  # noqa: ANN201 - GuardResult
    """Run the real input rail inside the governance context a request binds."""
    from app.guardrails import check_input

    token = set_governance_context(
        GovernanceContext(tenant_id=tenant_id, user_id=user_id, role=Role.ADMIN)
    )
    try:
        return await check_input(_QUESTION)
    finally:
        reset_governance_context(token)


async def test_a_tenants_denied_term_blocks_that_tenants_request_and_nobody_elses(
    db, monkeypatch
):
    """The same question: allowed everywhere, then refused for exactly one tenant.

    The control half matters as much as the claim. Without the "before", a test that
    only asserted the block could pass on a pipeline that blocks everything and would
    say nothing about whether the tenant's setting caused it. Without the bystander
    half, it could pass on a resolution memoised onto the process-wide singleton — which
    would be the same defect the other way round, one tenant's denylist refusing another
    tenant's legitimate question about their own project.
    """
    _offline_gateway(monkeypatch)
    await _seed_tenants()

    before = await _screen_as(_TENANT, _USER)
    assert before.verdict is not GuardVerdict.BLOCK, before

    await _tenant_admin_denies("Project-Zephyr")

    after = await _screen_as(_TENANT, _USER)
    assert after.verdict is GuardVerdict.BLOCK, (
        "the tenant admin added a denied term and the input rail still let it through "
        "— guardrails.denylist.terms is writable, auditable, badged 'Your setting' and "
        f"binds nothing: {after!r}"
    )
    assert after.layer == "denylist", after
    assert "Project-Zephyr" in after.reason, after

    bystander = await _screen_as(_OTHER_TENANT, _OTHER_USER)
    assert bystander.verdict is not GuardVerdict.BLOCK, (
        f"tenant {_OTHER_TENANT} was refused by tenant {_TENANT}'s denylist, so the "
        f"resolution leaked across the process-wide pipeline: {bystander!r}"
    )
