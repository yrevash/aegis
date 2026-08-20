"""The MCP control plane (§10.6/10.7): platform-admin only, and it moves the real tier.

The console is a client surface over the registry in :mod:`app.mcp.client`, so the
tests that matter here are the two a screen can get wrong: **who may reach it**, and
whether a write on it changes what the *gate* reads — as opposed to changing a number
the page then renders back to the operator who typed it.

The peer is a real in-process ``MCPServer`` spoken to by the SDK's own client, so no
test dials an external MCP server.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # noqa: E402 - the SDK is an optional extra

from aegis.core.types import GuardResult, GuardVerdict  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from tests.conftest import login_as  # noqa: E402 - the shared login helper

from app.adapter.tools import TOOL_REGISTRY  # noqa: E402
from app.api.schemas import RiskLevel  # noqa: E402
from app.mcp.client import (  # noqa: E402
    ExternalServerSpec,
    ExternalToolRegistry,
    UnknownExternalServerError,
    credential_fingerprint,
    merged_tool_risk,
    reset_registry,
)

TOOL = "mcp__acme__search"


async def _pass(text: str, _tool: str) -> GuardResult:
    return GuardResult(verdict=GuardVerdict.PASS, reason="clean", text=text)


@pytest.fixture
async def peer_registry():
    """Install a process registry holding one discovered in-process peer."""
    server = MCPServer("acme-peer")

    @server.tool(description="Search the acme corpus.")
    def search(q: str) -> str:  # noqa: ANN202 - the SDK reads the annotations
        return f"answer for {q}"

    registry = ExternalToolRegistry(
        client_factory=lambda _spec, _cred: Client(server), screen=_pass
    )
    registry.register_server(ExternalServerSpec(server_id="acme", label="Acme tools"))
    await registry.discover("acme")
    reset_registry(registry)
    yield registry
    reset_registry(None)


async def test_only_a_platform_admin_reaches_the_mcp_console(
    client, db, admin_headers, user_headers, peer_registry
):
    """Which third party's code an agent may reach is a platform decision, not a tenant's."""
    del peer_registry
    assert (await client.get("/mcp/console", headers=admin_headers)).status_code == 200
    assert (await client.get("/mcp/console", headers=user_headers)).status_code == 403
    assert (await client.get("/mcp/console")).status_code == 401

    devops = await login_as(client, "devops")
    assert (await client.get("/mcp/console", headers=devops)).status_code == 403
    assert (
        await client.put(
            f"/mcp/tools/{TOOL}/grant",
            headers=devops,
            json={"personas": ["operations_lead"], "risk": "low", "reason": "nope"},
        )
    ).status_code == 403
    assert (
        await client.post(
            "/mcp/servers",
            headers=devops,
            json={"serverId": "sneaky", "url": "http://example.invalid/mcp"},
        )
    ).status_code == 403


async def test_the_console_shows_a_discovered_tool_as_high_and_ungranted(
    client, db, admin_headers, peer_registry
):
    """A declared, discovered peer grants nothing: the honest default is HIGH and nobody."""
    del peer_registry
    body = (await client.get("/mcp/console", headers=admin_headers)).json()

    assert [s["serverId"] for s in body["servers"]] == ["acme"]
    assert body["servers"][0]["discoveredTools"] == 1
    assert body["servers"][0]["grantedTools"] == 0

    (row,) = body["tools"]
    assert row["name"] == TOOL
    assert row["remoteName"] == "search"
    assert row["risk"] == RiskLevel.HIGH.value
    assert row["riskIsDefault"] is True
    assert row["personas"] == []
    assert body["gateRisk"] == RiskLevel.HIGH.value
    # Nothing is asserted about ``selfEndpoint`` beyond it being present: an
    # unconfigured deployment reports ``null``, and that is the honest answer rather
    # than a guessed URL the console would render as a live address.
    assert "selfEndpoint" in body


async def test_lowering_a_tier_through_the_console_moves_what_the_gate_reads(
    client, db, admin_headers, peer_registry
):
    """The write is only real if ``merged_tool_risk`` — the gate's input — changes.

    Asserting the response body alone would pass against a console that stored the
    operator's decision somewhere nothing consults, which is the failure mode §7.16
    row 14 and the tenant gate-floor defect are both instances of.
    """
    del peer_registry
    assert merged_tool_risk(TOOL) is RiskLevel.HIGH

    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant",
        headers=admin_headers,
        json={"personas": ["operations_lead"], "risk": "high", "reason": "admitted"},
    )
    assert resp.status_code == 200, resp.text
    assert merged_tool_risk(TOOL) is RiskLevel.HIGH, "admission is not a tier change"

    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant",
        headers=admin_headers,
        json={
            "personas": ["operations_lead"],
            "risk": "low",
            "reason": "read-only corpus search, reviewed 2026-08-20",
        },
    )
    assert resp.status_code == 200, resp.text
    (row,) = resp.json()["tools"]
    assert row["risk"] == RiskLevel.LOW.value
    assert row["riskIsDefault"] is False
    assert row["reason"].startswith("read-only corpus search")
    assert merged_tool_risk(TOOL) is RiskLevel.LOW

    # An empty persona list is the revocation, and it puts the tier back at HIGH.
    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant", headers=admin_headers, json={"personas": []}
    )
    assert resp.status_code == 200, resp.text
    assert merged_tool_risk(TOOL) is RiskLevel.HIGH


async def test_the_console_refuses_a_grant_over_a_native_tool(
    client, db, admin_headers, peer_registry
):
    """This plane governs the external namespace only.

    A native tool's tier is declared on its ``ToolSpec``, in code, where it is reviewed
    — a runtime API that could lower ``update_request_status`` to LOW would be a way to
    walk the domain's own HIGH-risk write out of the gate over HTTP.
    """
    del peer_registry
    resp = await client.put(
        "/mcp/tools/update_request_status/grant",
        headers=admin_headers,
        json={"personas": ["operations_lead"], "risk": "low", "reason": "no"},
    )
    assert resp.status_code == 400
    assert "not an external MCP tool" in resp.json()["detail"]
    assert merged_tool_risk("update_request_status") is RiskLevel.HIGH


async def test_the_console_refuses_an_unknown_server_and_an_unknown_persona(
    client, db, admin_headers, peer_registry
):
    """Both refusals name what was wrong, because both are things an operator typed."""
    del peer_registry
    resp = await client.post("/mcp/servers/nope/test", headers=admin_headers)
    assert resp.status_code == 404
    assert "AEGIS_MCP_CLIENT_SERVERS" in resp.json()["detail"]

    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant",
        headers=admin_headers,
        json={"personas": ["not_a_persona"], "risk": "high"},
    )
    assert resp.status_code == 400
    assert "No such persona" in resp.json()["detail"]

    # An unknown field in the body is a 422, never a silent drop: a misspelled ``risk``
    # must not answer 200 and leave HIGH in place while the operator believes otherwise.
    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant",
        headers=admin_headers,
        json={"personas": ["operations_lead"], "rsk": "low"},
    )
    assert resp.status_code == 422


async def test_discovery_is_an_explicit_act_that_refreshes_the_tool_list(
    client, db, admin_headers, peer_registry
):
    """A tool the peer has withdrawn stops being offered, and its grant authorises nothing."""
    peer_registry.grant(TOOL, {"operations_lead"}, risk=RiskLevel.LOW, reason="reviewed")
    assert merged_tool_risk(TOOL) is RiskLevel.LOW

    peer_registry.client_factory = lambda _spec, _cred: Client(MCPServer("acme-peer"))
    resp = await client.post("/mcp/servers/acme/test", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert resp.json()["tools"] == []
    assert merged_tool_risk(TOOL) is RiskLevel.HIGH, (
        "a lowered tier survived the tool disappearing from the peer — a grant must "
        "not keep authorising a name nothing advertises"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The operator surface: connections, control, and the decision trail
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_connection_is_added_tested_and_survives_the_process(
    client, db, admin_headers, peer_registry
):
    """Add a peer, prove it answers, and prove the row outlives this registry.

    The durable half is the point of the table: a connection an operator added that
    vanishes when the process restarts is not a connection anybody can rely on. The
    test drops the whole registry and hydrates from Postgres, which is exactly what the
    app lifespan does.
    """
    from app.api.routes_mcp import load_servers
    from app.mcp.client import ExternalToolRegistry, get_registry

    factory = peer_registry.client_factory
    resp = await client.post(
        "/mcp/servers",
        headers=admin_headers,
        json={
            "serverId": "beta",
            "label": "Beta tools",
            "url": "https://beta.example/mcp",
            "authHeader": "X-API-Key",
            "credential": "beta-secret",
        },
    )
    assert resp.status_code == 201, resp.text
    row = next(s for s in resp.json()["servers"] if s["serverId"] == "beta")
    assert row["authHeader"] == "X-API-Key"
    assert row["hasCredential"] is True
    assert row["credentialFingerprint"] == credential_fingerprint("beta-secret")

    # The peer answers, and the probe says what it is.
    peer_registry.update_server("beta", url="")  # the in-process factory needs no URL
    resp = await client.post("/mcp/servers/beta/test", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    probe = resp.json()["probe"]
    assert probe["reachable"] is True
    assert probe["tools"] == ["search"]

    # A restart: a brand-new registry, hydrated from the table alone.
    fresh = ExternalToolRegistry(client_factory=factory, screen=_pass)
    reset_registry(fresh)
    assert await load_servers() >= 1
    hydrated = get_registry().server("beta")
    assert hydrated.url == "https://beta.example/mcp"
    assert hydrated.auth_header == "X-API-Key"
    # …and the secret did NOT survive, because Aegis never wrote it down.
    assert get_registry().has_credential("beta") is False


async def test_a_credential_is_never_readable_back_out(
    client, db, admin_headers, peer_registry
):
    """Write-only by construction: no response body anywhere carries the secret.

    Asserted over the *whole* serialised payload of every route that can see a server,
    rather than over the one field somebody remembered to check — the failure mode is
    a field added later that quietly starts echoing it.
    """
    del peer_registry
    secret = "sk-do-not-echo-me-1234567890"
    bodies = []
    resp = await client.post(
        "/mcp/servers",
        headers=admin_headers,
        json={"serverId": "gamma", "url": "https://g.example/mcp", "credential": secret},
    )
    assert resp.status_code == 201, resp.text
    bodies.append(resp.text)
    bodies.append((await client.get("/mcp/console", headers=admin_headers)).text)
    resp = await client.put(
        "/mcp/servers/gamma", headers=admin_headers, json={"credential": secret}
    )
    assert resp.status_code == 200, resp.text
    bodies.append(resp.text)

    for body in bodies:
        assert secret not in body, "an MCP response echoed a peer credential back"
    # The fingerprint is there instead, and it does not reveal the secret.
    console = (await client.get("/mcp/console", headers=admin_headers)).json()
    row = next(s for s in console["servers"] if s["serverId"] == "gamma")
    assert row["credentialFingerprint"] == credential_fingerprint(secret)
    assert row["credentialSetBy"] == "admin"


async def test_disabling_a_server_takes_its_tools_off_the_agent_payload(
    client, db, admin_headers, peer_registry
):
    """Disable is a control, not a soft delete — and the console still shows what it had."""
    from app.mcp.client import merged_tool_definitions_for

    peer_registry.grant(TOOL, {"operations_lead"})
    assert TOOL in [d["function"]["name"] for d in merged_tool_definitions_for("operations_lead")]

    resp = await client.put(
        "/mcp/servers/acme", headers=admin_headers, json={"enabled": False}
    )
    assert resp.status_code == 200, resp.text
    assert TOOL not in [
        d["function"]["name"] for d in merged_tool_definitions_for("operations_lead")
    ]

    # The console still lists it, flagged — an operator has to be able to see what a
    # server they just switched off was exposing.
    (row,) = resp.json()["tools"]
    assert row["name"] == TOOL
    assert row["callableNow"] is False
    assert merged_tool_risk(TOOL) is RiskLevel.HIGH

    # …and it comes back exactly as it was.
    resp = await client.put(
        "/mcp/servers/acme", headers=admin_headers, json={"enabled": True}
    )
    assert resp.json()["tools"][0]["callableNow"] is True


async def test_a_tier_decision_is_audited_with_the_before_and_the_after(
    client, db, admin_headers, peer_registry
):
    """Lowering a gate is what a post-incident review reads first.

    "It is HIGH now" is not an answer to "who lowered it, when, from what, and what did
    they say" — so the row carries both tiers, both persona sets, the actor and the
    reason, and the console hands them back.
    """
    del peer_registry
    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant",
        headers=admin_headers,
        json={
            "personas": ["operations_lead"],
            "risk": "low",
            "reason": "read-only corpus search, reviewed 2026-08-20",
        },
    )
    assert resp.status_code == 200, resp.text

    (decision,) = resp.json()["decisions"]
    assert decision["actor"] == "admin"
    assert decision["tool"] == TOOL
    assert decision["riskBefore"] == "high"
    assert decision["riskAfter"] == "low"
    assert decision["personasBefore"] == []
    assert decision["personasAfter"] == ["operations_lead"]
    assert decision["reason"].startswith("read-only corpus search")

    # A revocation is a decision too, and it is recorded as a return to HIGH.
    resp = await client.put(
        f"/mcp/tools/{TOOL}/grant", headers=admin_headers, json={"personas": []}
    )
    latest = resp.json()["decisions"][0]
    assert latest["riskBefore"] == "low"
    assert latest["riskAfter"] == "high"
    assert latest["personasAfter"] == []


async def test_aegis_own_tools_are_shown_read_only_with_where_they_are_declared(
    client, db, admin_headers, peer_registry
):
    """A native tier is code, not runtime state — the console shows it and cannot move it."""
    del peer_registry
    body = (await client.get("/mcp/console", headers=admin_headers)).json()
    names = {row["name"] for row in body["aegisTools"]}
    assert names == set(TOOL_REGISTRY)
    for row in body["aegisTools"]:
        assert row["risk"] == TOOL_REGISTRY[row["name"]].risk.value
        assert row["declaredIn"] == "app.adapter.tools.TOOL_REGISTRY"
    # There is no route that could change one: the grant plane refuses a native name.
    native = sorted(TOOL_REGISTRY)[0]
    resp = await client.put(
        f"/mcp/tools/{native}/grant",
        headers=admin_headers,
        json={"personas": ["operations_lead"], "risk": "low"},
    )
    assert resp.status_code == 400


async def test_removing_a_connection_forgets_the_row_too(
    client, db, admin_headers, peer_registry
):
    """Delete is durable: a removed peer must not come back at the next hydration."""
    from app.api.routes_mcp import load_servers
    from app.mcp.client import ExternalToolRegistry, get_registry

    factory = peer_registry.client_factory
    resp = await client.post(
        "/mcp/servers",
        headers=admin_headers,
        json={"serverId": "delta", "url": "https://d.example/mcp"},
    )
    assert resp.status_code == 201, resp.text

    resp = await client.delete("/mcp/servers/delta", headers=admin_headers)
    assert resp.status_code == 200, resp.text
    assert "delta" not in {s["serverId"] for s in resp.json()["servers"]}

    reset_registry(ExternalToolRegistry(client_factory=factory, screen=_pass))
    await load_servers()
    with pytest.raises(UnknownExternalServerError):
        get_registry().server("delta")


async def test_the_test_button_answers_even_when_the_rail_never_does(
    client, db, admin_headers, peer_registry
):
    """A control that spins forever is worse than one that fails, so this one fails.

    The reproduced defect: ``POST /mcp/servers/{id}/test`` against this deployment's own
    MCP server handshook in 0.2 s, listed its tools, and then never returned, because
    admitting those tools meant twenty-eight sequential ``TOOL_RESULT`` screenings —
    329 seconds — inside an unbounded ``discover``. The peer here is reachable and its
    ``tools/list`` answers at once; it is the rail that hangs.

    MUTATION: drop the ``ExternalDiscoveryTimeoutError`` handler from ``test_server``
    and this fails with a 500; drop the budget inside ``discover`` and it hangs, which
    is the bug itself.
    """
    import asyncio
    import time

    async def _never(text: str, _tool: str) -> GuardResult:  # noqa: ARG001
        await asyncio.sleep(30.0)
        raise AssertionError("unreachable")

    peer_registry.screen = _never
    peer_registry.discovery_timeout_seconds = 0.2

    started = time.monotonic()
    resp = await client.post("/mcp/servers/acme/test", headers=admin_headers)
    elapsed = time.monotonic() - started

    assert resp.status_code == 200, resp.text
    assert elapsed < 10.0, f"the test button took {elapsed:.1f}s to answer"
    probe = resp.json()["probe"]
    assert probe["reachable"] is False, "a catalogue nothing admitted got a green tick"
    assert "answered the handshake as 'acme-peer'" in probe["detail"], probe["detail"]
    assert "TOOL_RESULT" in probe["detail"], probe["detail"]
    assert "0.2s" in probe["detail"], probe["detail"]
