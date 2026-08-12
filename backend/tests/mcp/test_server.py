"""Offline tests for the MCP tool-server facade.

These call the server's registered functions directly (no live stdio client):
the ``AdapterToolServer`` methods are exactly what ``build_server`` wires to the
MCP ``tools/list`` / ``tools/call`` handlers. The suite skips cleanly if the
``mcp`` SDK is absent.
"""

import pytest

pytest.importorskip("mcp")  # noqa: E402 — skip the whole module without the SDK

from app.adapter import (  # noqa: E402
    DEFAULT_PERSONA_ID,
    InMemoryRecordStore,
    generate_synthetic_sync,
    tool_definitions_for,
)
from app.adapter.schema import RequestStatus  # noqa: E402
from app.mcp.server import AdapterToolServer, build_server  # noqa: E402


class CapturingAudit:
    """A fake audit sink that records every row in memory (no Postgres)."""

    def __init__(self):
        self.rows = []

    async def __call__(self, **kwargs):
        self.rows.append(kwargs)


def _store():
    return InMemoryRecordStore.from_dataset(generate_synthetic_sync())


def test_list_tools_matches_allowlist_definitions():
    """The MCP tool list mirrors ``tool_definitions_for`` name-for-name + schema."""
    srv = AdapterToolServer(store=_store(), audit=CapturingAudit())
    tools = srv.list_tools()
    expected = tool_definitions_for(DEFAULT_PERSONA_ID)

    assert [t.name for t in tools] == [d["function"]["name"] for d in expected]
    for tool, definition in zip(tools, expected, strict=True):
        fn = definition["function"]
        assert tool.description == fn["description"]
        assert tool.input_schema == fn["parameters"]


async def test_low_risk_tool_executes_and_audits():
    """A LOW-risk tool routes through run_tool, mutates state, and writes audit."""
    store = _store()
    audit = CapturingAudit()
    request = store.list_requests()[0]
    srv = AdapterToolServer(store=store, audit=audit)

    result = await srv.call_tool(
        "add_case_note", {"request_id": request.id, "body": "note from mcp"}
    )

    assert result.is_error is False
    payload = result.structured_content
    assert payload["ok"] is True
    assert payload["changed"] is True

    # Real side effect happened in the store.
    updated = store.get_request(request.id)
    assert any(note.body == "note from mcp" for note in updated.notes)

    # Audit row written, attributed to the MCP actor.
    assert [r["action"] for r in audit.rows] == ["add_case_note"]
    assert audit.rows[0]["actor"] == "mcp"


async def test_high_risk_tool_is_gated_not_executed():
    """A HIGH-risk write returns 'requires approval' and performs no side effect."""
    store = _store()
    audit = CapturingAudit()
    request = store.list_requests()[0]
    before_status = request.status
    target = next(s for s in RequestStatus if s != before_status)
    srv = AdapterToolServer(store=store, audit=audit)

    result = await srv.call_tool(
        "update_request_status", {"request_id": request.id, "status": target.value}
    )

    payload = result.structured_content
    assert result.is_error is False
    assert payload["status"] == "requires_approval"
    assert payload["executed"] is False

    # No side effect (the write never executed) ...
    assert store.get_request(request.id).status == before_status
    # ... but the HIGH-risk PROPOSAL is recorded in the audit log (not silent):
    # a probe of a gated tool must leave an auditable trail.
    assert [r["action"] for r in audit.rows] == ["mcp.high_risk_proposal:update_request_status"]
    assert audit.rows[0]["payload"]["executed"] is False


async def test_unknown_tool_is_rejected():
    """An unregistered tool name yields an error result, no execution."""
    audit = CapturingAudit()
    srv = AdapterToolServer(store=_store(), audit=audit)

    result = await srv.call_tool("does_not_exist", {})

    assert result.is_error is True
    assert audit.rows == []


async def test_not_allowlisted_tool_is_rejected():
    """A tool outside the persona's allowlist is rejected before any side effect."""
    store = _store()
    audit = CapturingAudit()
    # The 'client' persona may only call add_case_note.
    srv = AdapterToolServer(persona_id="client", store=store, audit=audit)

    result = await srv.call_tool(
        "assign_request", {"request_id": store.list_requests()[0].id, "agent_id": "a1"}
    )

    assert result.is_error is True
    assert audit.rows == []
    # And 'assign_request' is not even listed for this persona.
    assert "assign_request" not in {t.name for t in srv.list_tools()}


def test_build_server_registers_mcp_handlers():
    """build_server returns a real MCP server with tools/list + tools/call wired."""
    server = build_server(store=_store(), audit=CapturingAudit())

    assert server.get_request_handler("tools/list") is not None
    assert server.get_request_handler("tools/call") is not None
    assert [t.name for t in server.facade.list_tools()] == [
        d["function"]["name"] for d in tool_definitions_for(DEFAULT_PERSONA_ID)
    ]


# ─────────────────────────────────────────────────────────────────────────────
# Tool annotations — the MCP-level safety hints
# ─────────────────────────────────────────────────────────────────────────────


def test_list_tools_carries_honest_risk_annotations():
    """Every listed tool carries MCP annotations matching its registry risk tier.

    The hints are asserted per tool rather than derived from risk, because risk does
    not imply idempotency — so this guards the two cases a risk→annotation shortcut
    would get wrong.
    """
    facade = AdapterToolServer(store=InMemoryRecordStore.from_dataset(generate_synthetic_sync()))
    by_name = {t.name: t for t in facade.list_tools()}

    # No adapter tool is a read; every one of them mutates the record store.
    assert all(t.annotations.read_only_hint is False for t in by_name.values())

    # LOW risk but NOT idempotent: each call appends another note.
    note = by_name["add_case_note"]
    assert note.annotations.idempotent_hint is False
    assert note.annotations.destructive_hint is False
    assert "low risk" in note.annotations.title

    # MEDIUM risk, reversible, converges on re-run → idempotent, not destructive.
    assign = by_name["assign_request"]
    assert assign.annotations.idempotent_hint is True
    assert assign.annotations.destructive_hint is False

    # HIGH risk: the customer-visible write, flagged destructive to the client.
    status = by_name["update_request_status"]
    assert status.annotations.destructive_hint is True
    assert "high risk" in status.annotations.title

    # Closed domain — no tool reaches an open world.
    assert all(t.annotations.open_world_hint is False for t in by_name.values())


def test_unknown_tool_falls_back_to_conservative_annotations():
    """A tool with no explicit annotation row is advertised cautiously."""
    from app.api.schemas import RiskLevel
    from app.mcp.server import _conservative_annotations

    low = _conservative_annotations(RiskLevel.LOW)
    assert low["read_only_hint"] is False
    assert low["idempotent_hint"] is False
    assert low["destructive_hint"] is False

    # An unclassified HIGH-risk tool must never look safer than it is.
    assert _conservative_annotations(RiskLevel.HIGH)["destructive_hint"] is True


# ─────────────────────────────────────────────────────────────────────────────
# Resources — the read-only context surface
# ─────────────────────────────────────────────────────────────────────────────


def test_list_resources_publishes_capabilities_and_policy():
    """The server publishes exactly the two documented resource URIs."""
    from app.mcp.server import CAPABILITIES_URI, TOOL_POLICY_URI

    facade = AdapterToolServer()
    uris = {str(r.uri) for r in facade.list_resources()}
    assert uris == {CAPABILITIES_URI, TOOL_POLICY_URI}
    assert all(r.mime_type == "application/json" for r in facade.list_resources())


def test_capabilities_resource_is_the_real_manifest():
    """The capabilities resource mirrors app.capabilities, not a hand-written copy."""
    import json

    from app.capabilities import AEGIS_MODULES, PRODUCT_NAME
    from app.mcp.server import CAPABILITIES_URI

    body = json.loads(AdapterToolServer().read_resource(CAPABILITIES_URI))
    assert body["product"] == PRODUCT_NAME
    assert body["module_count"] == len(AEGIS_MODULES)
    assert len(body["modules"]) == len(AEGIS_MODULES)


def test_tool_policy_resource_marks_high_risk_as_gated():
    """The policy resource tells a client which tools are gated BEFORE it calls one."""
    import json

    from app.mcp.server import TOOL_POLICY_URI

    body = json.loads(AdapterToolServer().read_resource(TOOL_POLICY_URI))
    by_name = {t["name"]: t for t in body["tools"]}

    # The policy must cover exactly the persona's allowlisted tools.
    expected = {d["function"]["name"] for d in tool_definitions_for(DEFAULT_PERSONA_ID)}
    assert set(by_name) == expected

    assert by_name["update_request_status"]["risk"] == "high"
    assert by_name["update_request_status"]["auto_executed"] is False
    assert by_name["add_case_note"]["auto_executed"] is True


def test_unknown_resource_uri_is_rejected():
    """An unpublished URI raises rather than returning empty content."""
    with pytest.raises(ValueError, match="Unknown resource URI"):
        AdapterToolServer().read_resource("aegis://nope")
