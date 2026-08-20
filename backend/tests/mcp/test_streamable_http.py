"""The MCP server driven over its **real** Streamable HTTP transport (§10.4/10.5).

Everything here goes through a live socket: a uvicorn server on an ephemeral port, the
SDK's own ``streamable_http_client``, a real ``ClientSession`` handshake, and a real
PostgreSQL behind it. A unit test over a protocol is not evidence the protocol works —
the failure modes this phase exists to prevent (authenticate the connection, trust every
call) live in the transport, not in the facade.

The server is hosted here as a bare Starlette mount rather than the whole FastAPI app so
a transport test does not drag in Temporal, the sweepers and the ML warm-up; that the
platform actually mounts it is asserted separately in :mod:`tests.mcp.test_mount`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator

import pytest

pytest.importorskip("mcp")  # noqa: E402 — skip the whole module without the SDK

import httpx  # noqa: E402
import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from aegis.governance.types import Role  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from sqlalchemy import text  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.mcp.server import MCP_MOUNT, MCP_PATH, build_http_app  # noqa: E402

# ─────────────────────────────────────────────────────────────────────────────
# Harness: a real server on a real port, in this test's own event loop
# ─────────────────────────────────────────────────────────────────────────────


@contextlib.asynccontextmanager
async def serve_mcp() -> AsyncIterator[str]:
    """Run the MCP Streamable HTTP app on an ephemeral port; yield its URL.

    In **this** event loop deliberately: the ``db`` fixture binds an asyncpg pool to the
    running loop, and a server on a second loop would hand those connections across
    loops and hang.
    """
    inner = build_http_app()
    # A mounted ASGI app never receives lifespan events from its host, so the host has
    # to enter the inner one — the same three lines ``app.main.create_app`` needs, and
    # the reason they are there: without this the mount accepts a POST and then raises
    # "Task group is not initialized" from inside the session manager.
    app = Starlette(
        routes=[Mount(MCP_MOUNT, app=inner)],
        lifespan=lambda _host: inner.router.lifespan_context(inner),
    )
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        yield f"http://127.0.0.1:{port}{MCP_MOUNT}{MCP_PATH}"
    finally:
        server.should_exit = True
        await task


@contextlib.asynccontextmanager
async def mcp_session(url: str, token: str) -> AsyncIterator[ClientSession]:
    """Open one initialised MCP session over Streamable HTTP as ``token``'s principal.

    The bearer rides on the httpx client, so **every** request in the session carries
    it — which is exactly the property the server refuses to take for granted.
    """
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        httpx2.AsyncClient(headers=headers, timeout=30.0) as http,
        streamable_http_client(url, http_client=http) as (read, write),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        yield session


def payload(result) -> dict:
    """Return an MCP tool result's structured payload (or parse its text content)."""
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(result.content[0].text)


def token_for(user_id: int, username: str, role: Role, tenant_id: int | None) -> str:
    """Mint the same access token ``POST /v1/auth/login`` mints for this principal."""
    from aegis.governance.security import principal_role

    return create_access_token(
        user_id=user_id,
        username=username,
        role=principal_role(role, tenant_id),
        coarse_role=role.value,
        tenant_id=tenant_id,
    )


async def make_tenant_user(name: str, role: Role) -> tuple[int, int, str, str]:
    """Create a tenant and one user in it; return ``(tenant_id, user_id, name, token)``."""
    from app.data import create_tenant, create_user

    tenant = await create_tenant(name, usd_cap=10.0)
    username = f"{name}-{role.value}"
    user = await create_user(username, role=role, tenant_id=tenant.id)
    return tenant.id, user.id, username, token_for(user.id, username, role, tenant.id)


async def write_audit(tenant_id: int, action: str) -> None:
    """Append one audit row owned by ``tenant_id`` (the rows the isolation test reads)."""
    from app.data import record_audit

    await record_audit(
        action=action, actor="seed", model=None, trace_id=None, payload={},
        tenant_id=tenant_id,
    )


async def update_user(user_id: int, **columns) -> None:
    """Apply a live change to a ``users`` row, as an administrator would.

    Bound at the platform scope (``None``) on the owner connection, which is how every
    admin write in this codebase reaches a row it does not share a tenant with.
    """
    from aegis.governance.rls import set_tenant_scope
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.data.session import get_admin_engine

    assignments = ", ".join(f"{k} = :{k}" for k in columns)
    async with AsyncSession(get_admin_engine()) as session:
        await set_tenant_scope(session, None)
        await session.execute(
            text(f"UPDATE users SET {assignments} WHERE id = :user_id"),  # noqa: S608
            {**columns, "user_id": user_id},
        )
        await session.commit()


# ─────────────────────────────────────────────────────────────────────────────
# 10.4 — the transport carries identity, and refuses a call without one
# ─────────────────────────────────────────────────────────────────────────────


async def test_unauthenticated_request_never_reaches_a_handler(db):
    """No bearer → 401 with a challenge, from the SDK's own auth middleware.

    The MCP endpoint is a second front door to the same data. If it answered anything
    at all unauthenticated, every control behind it would be decoration.
    """
    async with serve_mcp() as url, httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
        )
    assert resp.status_code == 401
    assert "www-authenticate" in {k.lower() for k in resp.headers}


async def test_tools_follow_the_callers_role_not_an_env_var(db):
    """Two roles connect to the SAME server and are offered different tools.

    This is what replaced ``MCP_PERSONA_ID``: the persona comes from the caller's live
    RBAC role via the platform's own ``PERSONA_BY_ROLE`` seam, so one process serves
    every persona instead of one.
    """
    _t1, _u1, _n1, devops = await make_tenant_user("mcp-roles-ops", Role.DEVOPS)
    _t2, _u2, _n2, client = await make_tenant_user("mcp-roles-client", Role.CLIENT)

    async with serve_mcp() as url:
        async with mcp_session(url, devops) as session:
            operator_tools = {t.name for t in (await session.list_tools()).tools}
        async with mcp_session(url, client) as session:
            client_tools = {t.name for t in (await session.list_tools()).tools}

    # The operator persona reaches every domain tool, plus the platform audit read.
    assert {"add_case_note", "assign_request", "update_request_status"} <= operator_tools
    assert "aegis_audit_recent" in operator_tools
    # The client persona's allowlist is one tool, and it holds no role the platform
    # audit trail admits — so it is not even offered.
    assert client_tools == {"add_case_note"}


# ─────────────────────────────────────────────────────────────────────────────
# 10.5 — RBAC and tenant scope, resolved per call
# ─────────────────────────────────────────────────────────────────────────────


async def test_two_tenants_see_different_data_over_the_same_server(db):
    """The definition-of-done isolation case, over the live transport.

    Two MCP callers from different tenants read the platform's audit trail through one
    server process and each sees only its own tenant's rows.
    """
    tenant_a, _ua, _na, token_a = await make_tenant_user("mcp-iso-a", Role.DEVOPS)
    tenant_b, _ub, _nb, token_b = await make_tenant_user("mcp-iso-b", Role.DEVOPS)
    await write_audit(tenant_a, "seed.only_in_tenant_a")
    await write_audit(tenant_b, "seed.only_in_tenant_b")

    async with serve_mcp() as url:
        async with mcp_session(url, token_a) as session:
            seen_a = payload(await session.call_tool("aegis_audit_recent", {"limit": 50}))
        async with mcp_session(url, token_b) as session:
            seen_b = payload(await session.call_tool("aegis_audit_recent", {"limit": 50}))

    assert seen_a["tenant_id"] == tenant_a
    assert seen_b["tenant_id"] == tenant_b
    actions_a = {row["action"] for row in seen_a["rows"]}
    actions_b = {row["action"] for row in seen_b["rows"]}
    assert "seed.only_in_tenant_a" in actions_a
    assert "seed.only_in_tenant_b" not in actions_a
    assert "seed.only_in_tenant_b" in actions_b
    assert "seed.only_in_tenant_a" not in actions_b


async def test_scope_is_resolved_per_call_not_per_connection(db):
    """A connection opened as tenant A must not read tenant A after the caller moves.

    **The mutation this test exists for.** One MCP session, two calls. Between them the
    principal is moved to another tenant, exactly as an administrator would move them.
    A server that resolved identity once — at the handshake, from the token's claims, or
    from a value cached on the session — answers the second call with tenant A's rows
    and passes every other test in this file. Resolving the caller from *this call's*
    request and re-reading the live ``users`` row is what makes the second answer change.
    """
    tenant_a, user_id, _name, token = await make_tenant_user("mcp-move-a", Role.DEVOPS)
    from app.data import create_tenant

    tenant_b = (await create_tenant("mcp-move-b", usd_cap=10.0)).id
    await write_audit(tenant_a, "seed.only_in_tenant_a")
    await write_audit(tenant_b, "seed.only_in_tenant_b")

    async with serve_mcp() as url, mcp_session(url, token) as session:
        before = payload(await session.call_tool("aegis_audit_recent", {"limit": 50}))

        # The authority changes underneath a live connection. The bearer is unchanged
        # and still valid — it still says ``tenant_id: <tenant_a>``.
        await update_user(user_id, tenant_id=tenant_b)

        after = payload(await session.call_tool("aegis_audit_recent", {"limit": 50}))

    assert before["tenant_id"] == tenant_a
    assert {r["action"] for r in before["rows"]} == {"seed.only_in_tenant_a"}
    assert after["tenant_id"] == tenant_b
    assert {r["action"] for r in after["rows"]} == {"seed.only_in_tenant_b"}


async def test_a_deactivated_principal_loses_authority_mid_connection(db):
    """Revocation takes effect on the next call, not at the next login.

    The other half of "authority is not the token": an account switched off while its
    session is open cannot go on using it until the token happens to expire.
    """
    tenant, user_id, _name, token = await make_tenant_user("mcp-revoke", Role.DEVOPS)
    await write_audit(tenant, "seed.before_revocation")

    async with serve_mcp() as url, mcp_session(url, token) as session:
        allowed = await session.call_tool("aegis_audit_recent", {"limit": 50})
        await update_user(user_id, is_active=False)
        refused = await session.call_tool("aegis_audit_recent", {"limit": 50})

    assert allowed.is_error is False
    assert refused.is_error is True
    assert "deactivated" in refused.content[0].text


async def test_a_role_may_not_call_a_tool_its_http_sibling_refuses(db):
    """RBAC is enforced on the call, not merely on the listing.

    A client-role caller is never *offered* ``aegis_audit_recent``, but a hostile client
    can name any tool it likes. The refusal has to be at the call.
    """
    _tenant, _user, _name, token = await make_tenant_user("mcp-rbac", Role.CLIENT)

    async with serve_mcp() as url, mcp_session(url, token) as session:
        result = await session.call_tool("aegis_audit_recent", {"limit": 5})

    assert result.is_error is True
    assert "requires one of the roles: admin, devops" in result.content[0].text


# ─────────────────────────────────────────────────────────────────────────────
# The human gate — the protocol gets no back door
# ─────────────────────────────────────────────────────────────────────────────


async def test_high_risk_call_lands_in_the_human_approval_gate(db):
    """A HIGH-risk MCP call executes nothing and files a real PENDING inbox row.

    Not an audit line claiming it was "routed to the approval inbox": the row is in the
    ``approvals`` table the console reads, scoped to the caller's tenant, attributed to
    the caller, carrying the tool name and its arguments.
    """
    from app.data import list_approvals

    tenant, user_id, username, token = await make_tenant_user("mcp-gate", Role.DEVOPS)

    async with serve_mcp() as url, mcp_session(url, token) as session:
        listed = {t.name for t in (await session.list_tools()).tools}
        result = await session.call_tool(
            "update_request_status", {"request_id": "REQ-1", "status": "resolved"}
        )

    body = payload(result)
    # Discoverable, and honestly labelled, BEFORE the client calls it.
    assert "update_request_status" in listed
    assert result.is_error is False
    assert body["status"] == "requires_approval"
    assert body["executed"] is False

    rows = await list_approvals(tenant_id=tenant)
    filed = [r for r in rows if r.id == body["approval_id"]]
    assert len(filed) == 1, f"no approvals row for {body['approval_id']}: {rows}"
    gate = filed[0]
    assert gate.status == "pending"
    assert gate.action == "update_request_status"
    assert gate.args == {"request_id": "REQ-1", "status": "resolved"}
    assert gate.risk.value == "high"
    assert gate.tenant_id == tenant
    assert gate.requested_by == user_id
    assert username in (gate.rationale or "")

    # And the trail. This is the direct evidence that the governance context the HTTP
    # API builds is the one bound around an MCP call: ``record_audit`` takes the tenant
    # off the bound context when none is passed, so a row landing under this tenant
    # could only have come from a context bound for this caller. The actor names the
    # principal, not merely the protocol.
    from app.data import list_recent_audit

    trail = await list_recent_audit(limit=50, tenant_id=tenant)
    proposals = [
        r for r in trail
        if r.action == "mcp.high_risk_proposal:update_request_status"
    ]
    assert len(proposals) == 1
    assert proposals[0].actor == f"mcp:{username}"


async def test_a_gate_filed_over_mcp_is_not_visible_to_another_tenant(db):
    """The gate is tenant-scoped like every other row: a neighbour cannot see it."""
    from app.data import list_approvals

    tenant, _user, _name, token = await make_tenant_user("mcp-gate-scope", Role.DEVOPS)
    other, _u2, _n2, _t2 = await make_tenant_user("mcp-gate-neighbour", Role.DEVOPS)

    async with serve_mcp() as url, mcp_session(url, token) as session:
        body = payload(
            await session.call_tool(
                "update_request_status", {"request_id": "REQ-1", "status": "resolved"}
            )
        )

    assert tenant != other
    assert [r.id for r in await list_approvals(tenant_id=tenant)] == [body["approval_id"]]
    assert await list_approvals(tenant_id=other) == []
