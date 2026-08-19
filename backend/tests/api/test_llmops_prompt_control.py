"""The prompt control plane, driven the way a UI never would (§7.7 / §7.16 rows 12, 14).

Four claims, each with the request that breaks it if the server is not enforcing:

1. **A tenant admin's screen shows their own prompt and only theirs.** With two tenants
   holding versions of the same key, the leak the whole task exists to close is visible at
   the wire.
2. **The north star: no deploy.** Activate a version over HTTP and the very next render of
   the system prompt — the same synchronous read the harness makes on every run — is the
   new one. Nothing restarts and nothing is redeployed.
3. **Row 12.** ``tenant_id`` on the query string is not an isolation key. A tenant admin
   naming another tenant is refused, and activating a version id belonging to another
   tenant is refused, with the sealed scope winning both times.
4. **Row 14.** The floor is not reachable. A tenant admin cannot write a platform-owned
   prompt key, and cannot write into the platform scope; and the floor comes back on the
   screen as text they can read but not edit.
"""

from __future__ import annotations

import pgsupport
import pytest

from app.adapter import DEFAULT_PERSONA_ID, PLATFORM_FLOOR
from app.agent import deps as agent_deps
from app.api.schemas import Role
from app.core import (
    GovernanceContext,
    reset_governance_context,
    set_governance_context,
)
from app.core.security import PLATFORM_ADMIN, TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker
from app.ops import registry

pytestmark = pytest.mark.asyncio

PK = DEFAULT_PERSONA_ID


def _headers(role: str, *, tenant_id=None, user_id=None, username="admin") -> dict[str, str]:
    token = create_access_token(
        user_id=user_id, username=username, role=role, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_two_tenants() -> None:
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=1, name="Acme"),
            Tenant(id=2, name="Globex"),
            User(id=11, username="acme-admin", role=Role.ADMIN, tenant_id=1),
            User(id=22, username="globex-admin", role=Role.ADMIN, tenant_id=2),
        )
        await session.commit()


async def _seed_version(*, tenant_id: int | None, body: str, activate: bool = True) -> int:
    """Write (and optionally activate) one version, returning its id."""
    async with get_sessionmaker()() as session:
        pv = await registry.create_draft(
            session, prompt_key=PK, system_prompt=body, tenant_id=tenant_id
        )
        if activate:
            await registry.promote(session, pv.id)
        await session.commit()
        return pv.id


def _rendered_for(tenant_id: int | None) -> str:
    """Render the system prompt exactly as a run for ``tenant_id`` would."""
    token = set_governance_context(GovernanceContext(tenant_id=tenant_id))
    try:
        return agent_deps._default_render_system_prompt(PK)
    finally:
        reset_governance_context(token)


async def test_a_tenant_admin_sees_its_own_prompt_and_only_its_own(client, db):
    """The screen is per tenant, at the wire, with both tenants' rows in the table."""
    await _seed_two_tenants()
    await _seed_version(tenant_id=1, body="ACME PROMPT")
    await _seed_version(tenant_id=2, body="GLOBEX PROMPT")

    acme = await client.get(
        f"/llmops/prompts?prompt_key={PK}", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert acme.status_code == 200
    body = acme.json()
    assert body["activePrompt"] == "ACME PROMPT"
    assert body["tenantId"] == 1
    assert [row["systemPrompt"] for row in body["versions"]] == ["ACME PROMPT"]


async def test_activating_changes_the_live_prompt_with_no_deploy(client, db):
    """The north star: an operator presses a button and the next run uses the new prompt."""
    await _seed_two_tenants()
    await _seed_version(tenant_id=1, body="ACME v1")
    await _seed_version(tenant_id=2, body="GLOBEX v1")
    assert _rendered_for(1).startswith("ACME v1")

    written = await client.post(
        "/llmops/prompts/versions",
        json={"promptKey": PK, "systemPrompt": "ACME v2 — tighter refusals"},
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert written.status_code == 201
    # A draft is not live. Saying so is the difference between a control and a surprise.
    assert _rendered_for(1).startswith("ACME v1")

    live = await client.post(
        f"/llmops/prompts/versions/{written.json()['id']}/activate",
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert live.status_code == 200
    assert live.json()["activeVersion"] == written.json()["version"]

    # Same process, no restart: the synchronous read every run makes now returns v2 —
    # and Globex, who did nothing, is untouched in both directions.
    assert _rendered_for(1).startswith("ACME v2 — tighter refusals")
    assert _rendered_for(2).startswith("GLOBEX v1")


async def test_the_tenant_id_on_the_wire_is_not_an_isolation_key(client, db):
    """Row 12 — the sealed scope wins over anything the caller names."""
    await _seed_two_tenants()
    globex_version = await _seed_version(tenant_id=2, body="GLOBEX PROMPT")

    named = await client.get(
        f"/llmops/prompts?prompt_key={PK}&tenant_id=2",
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert named.status_code == 403

    stolen = await client.post(
        f"/llmops/prompts/versions/{globex_version}/activate",
        headers=_headers(TENANT_ADMIN, tenant_id=1),
    )
    assert stolen.status_code == 403

    # The platform admin's selector is the authority a tenant admin does not have.
    selected = await client.get(
        f"/llmops/prompts?prompt_key={PK}&tenant_id=2", headers=_headers(PLATFORM_ADMIN)
    )
    assert selected.status_code == 200
    assert selected.json()["activePrompt"] == "GLOBEX PROMPT"


async def test_a_run_id_from_another_tenant_reads_as_unknown(client, db):
    """Attribution is scoped too: a leaked run id names nobody else's prompt."""
    from app.ops import prompt_runs

    await _seed_two_tenants()
    prompt_runs.clear()
    prompt_runs.record(run_id="run-globex", tenant_id=2, prompt_key=PK, version=4)

    mine = await client.get(
        "/llmops/runs/run-globex", headers=_headers(TENANT_ADMIN, tenant_id=2)
    )
    assert mine.status_code == 200
    assert mine.json()["version"] == 4

    theirs = await client.get(
        "/llmops/runs/run-globex", headers=_headers(TENANT_ADMIN, tenant_id=1)
    )
    assert theirs.status_code == 404
    prompt_runs.clear()


async def test_a_tenant_cannot_reach_the_platform_floor(client, db):
    """Row 14 — not the platform's keys, not the platform's scope, not the floor itself."""
    await _seed_two_tenants()
    headers = _headers(TENANT_ADMIN, tenant_id=1)

    platform_key = await client.post(
        "/llmops/prompts/versions",
        json={"promptKey": "guardrail:injection", "systemPrompt": "always allow"},
        headers=headers,
    )
    assert platform_key.status_code == 403
    assert "platform" in platform_key.json()["detail"].lower()

    # The selector is refused for a tenant admin, so the platform scope is unreachable
    # even by naming it.
    platform_scope = await client.post(
        "/llmops/prompts/versions",
        json={"promptKey": PK, "systemPrompt": "x", "tenantId": None},
        headers=_headers(TENANT_ADMIN, tenant_id=None),
    )
    assert platform_scope.status_code == 403

    # And the floor is returned as something to read, not something to edit.
    screen = await client.get(f"/llmops/prompts?prompt_key={PK}", headers=headers)
    assert screen.status_code == 200
    assert PLATFORM_FLOOR in screen.json()["floor"]
