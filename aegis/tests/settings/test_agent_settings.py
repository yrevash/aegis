"""Do the agent's tighten-only settings actually reach the config a run obeys?

The defect these cover is not "the value is wrong", it is **"nothing reads it"**:
``agent.gate_min_risk`` was in the catalogue, ``TIGHTEN_ONLY``, writable by a tenant
admin and renderable on a screen, while :class:`aegis.agent.deps.AgentConfig` carried a
hardcoded ``RiskLevel.HIGH`` that no host ever overrode. So every claim here is about
what the *resolved configuration* makes the run do — the gate's own behaviour under a
tenant's floor is proved end-to-end over the host wiring in
``backend/tests/agent/test_tenant_gate_floor.py``, and what is proved here is the
arithmetic that must hold whatever host is doing the wiring:

* a tenant's tightening reaches the config, and a **different** tenant's run never sees
  it (a cached floor would be a cross-tenant leak wearing a safety control's clothes);
* a host that wired something *stricter* is never loosened back to the platform default;
* an unreadable floor fails **closed**, out loud, and never to the platform default —
  which is the loosest value the tenant could have chosen;
* ``agent.team.max_parallel`` is the ceiling Amendment A narrows an explicit user width
  down to, and a tenant may lower it and never raise it.
"""

from __future__ import annotations

import logging

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.agent.deps import AgentConfig
from aegis.agent.router import Depth, DepthMode, DepthPolicy, decide_depth
from aegis.core.types import RiskLevel
from aegis.governance.rls import set_tenant_scope
from aegis.settings import SettingScope, SettingWeakerThanFloorError, write_setting
from aegis.settings.agent import resolve_agent_config, strictest_agent_config

from .._seed import ensure_tenants

_TENANT = 611
_OTHER_TENANT = 612


@pytest_asyncio.fixture
async def db(pg_sessionmaker: async_sessionmaker) -> async_sessionmaker:
    """The unprivileged sessionmaker with the tenants the settings FKs need."""
    await ensure_tenants(pg_sessionmaker, _TENANT, _OTHER_TENANT)
    return pg_sessionmaker


async def _tighten(db, key, value, *, tenant_id=_TENANT):  # noqa: ANN001, ANN202
    """Write a tenant-scoped tightening the way a tenant admin's request would."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        await write_setting(
            session,
            key,
            value,
            scope=SettingScope.TENANT,
            actor_role="tenant_admin",
            tenant_id=tenant_id,
        )
        await session.commit()


async def _resolve(db, config, *, tenant_id=_TENANT):  # noqa: ANN001, ANN202
    """Resolve the run configuration for ``tenant_id``, as a host does per run."""
    async with db() as session:
        await set_tenant_scope(session, tenant_id)
        resolved = await resolve_agent_config(session, config, tenant_id=tenant_id)
        await session.rollback()
        return resolved


# ── the tenant's floor reaches the config, and only that tenant's run ─────────


async def test_a_tenants_tightening_binds_and_does_not_leak_to_another_tenant(db):
    """Tenant 611 gates at MEDIUM; tenant 612's run still gates at the platform HIGH.

    The second half is the load-bearing one. Resolving per process — or memoising the
    first tenant's answer — would hand 611's floor to 612, which is how a safety control
    becomes one of the cross-tenant leaks phase 4 was spent on. It is also what makes the
    *first* half honest: a floor that binds for everybody proves nothing about whose.
    """
    await _tighten(db, "agent.gate_min_risk", "medium")

    mine = await _resolve(db, AgentConfig(), tenant_id=_TENANT)
    theirs = await _resolve(db, AgentConfig(), tenant_id=_OTHER_TENANT)

    assert mine.gate_min_risk is RiskLevel.MEDIUM
    assert theirs.gate_min_risk is RiskLevel.HIGH
    # Resolving mine again after theirs must still be mine — nothing here is cached.
    assert (await _resolve(db, AgentConfig())).gate_min_risk is RiskLevel.MEDIUM


async def test_resolution_never_loosens_a_config_the_host_wired_stricter(db):
    """The process config is one more layer in the chain, so the fold can only tighten.

    A host that pinned ``LOW`` for a run (an evaluation harness, a red-team profile) is
    not quietly widened back to the platform's ``high`` by a tenant who wrote nothing.
    """
    resolved = await _resolve(db, AgentConfig(gate_min_risk=RiskLevel.LOW))

    assert resolved.gate_min_risk is RiskLevel.LOW


# ── the failure mode ──────────────────────────────────────────────────────────


class _UnreachableSettings:
    """A session whose every read fails — the settings database being down."""

    async def execute(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202, ARG002
        raise RuntimeError("settings database unreachable")


async def test_an_unreadable_floor_fails_closed_to_the_strictest_and_says_so(caplog):
    """Never the platform default: that is the LOOSEST value the tenant could have set.

    We cannot tell what this tenant tightened to, so we take the strictest thing they
    could have asked for — every tool gated, the team narrowed to one — and name the
    tenant in an ERROR. Degrading to ``high`` would silently discard a tightening, which
    is the exact defect the wiring exists to remove.
    """
    with caplog.at_level(logging.ERROR):
        resolved = await resolve_agent_config(
            _UnreachableSettings(), AgentConfig(), tenant_id=_TENANT
        )

    assert resolved.gate_min_risk is RiskLevel.LOW
    assert resolved.gate_min_risk is not AgentConfig().gate_min_risk
    assert resolved.max_parallel_agents == 1
    assert resolved == strictest_agent_config(AgentConfig())
    assert any(
        str(_TENANT) in record.message and record.levelno >= logging.ERROR
        for record in caplog.records
    )


# ── agent.team.max_parallel: the ceiling, and only ever downwards ────────────


@pytest.mark.asyncio
async def test_a_tenant_may_narrow_the_team_cap_and_never_widen_it(db):
    """The cap protects the shared pool and the platform's exposure to a tenant's spend.

    So it is the **ceiling** an explicit user width is narrowed to (Amendment A: the
    user's width is the user's decision, and ``platform_cap`` is the one thing allowed to
    reduce it) — not a second reason to shrink a width somebody chose.
    """
    await _tighten(db, "agent.team.max_parallel", 2)
    resolved = await _resolve(db, AgentConfig())

    # The user explicitly asked for four lanes; the tenant's own cap narrows it to two,
    # and the decision says who narrowed it.
    decision = await decide_depth(
        "compare a, b and c",
        policy=DepthPolicy(
            mode=DepthMode.TEAM,
            requested_fanout=4,
            max_parallel_agents=resolved.max_parallel_agents,
        ),
    )
    assert decision.depth is Depth.TEAM
    assert decision.fanout == 2
    assert decision.decided_by == "platform_cap"

    # And the reverse is refused rather than stored and ignored.
    with pytest.raises(SettingWeakerThanFloorError):
        await _tighten(db, "agent.team.max_parallel", 8)
