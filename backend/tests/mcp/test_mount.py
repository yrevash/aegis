"""The MCP front door is actually reachable on the platform's own ASGI app (§10.4).

``tests/mcp/test_streamable_http.py`` drives the transport on a bare Starlette host, so
this is the one assertion that the *platform* serves it — and, more importantly, that the
composition root enters the mounted app's lifespan. Without that entry the mount answers
a POST and then raises "Task group is not initialized" from inside the SDK's session
manager: a wiring failure no test over the facade can see.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")  # noqa: E402 — skip the whole module without the SDK

import httpx  # noqa: E402
from starlette.routing import Mount  # noqa: E402

from app.core.security import create_access_token  # noqa: E402
from app.mcp.server import (  # noqa: E402
    MCP_MOUNT,
    MCP_PATH,
    McpTransportMount,
)

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "aegis-mount-probe", "version": "1.0"},
    },
}


def test_the_platform_mounts_the_mcp_transport():
    """``create_app`` mounts the MCP app and keeps a handle for the lifespan."""
    from app.main import create_app

    app = create_app()
    mounts = [r for r in app.routes if isinstance(r, Mount) and r.path == MCP_MOUNT]
    assert mounts, f"no MCP mount at {MCP_MOUNT} on the platform app"
    assert isinstance(app.state.mcp_mount, McpTransportMount)
    assert mounts[0].app is app.state.mcp_mount
    # Nothing is running until the lifespan starts it, which is exactly what makes the
    # mount survivable across more than one lifespan.
    assert app.state.mcp_mount.app is None


async def test_the_mounted_transport_handshakes_under_the_platform_lifespan():
    """A real MCP ``initialize`` over the platform app returns a session, not a 500.

    ``initialize`` is served by the SDK's own dispatch loop — the one that runs inside
    the session manager's task group — so a successful handshake is the direct evidence
    that ``app.main.lifespan`` entered the mounted app's lifespan. It reaches no Aegis
    handler and touches no database, which is why it can be asserted with the stores off.
    """
    from app.config import get_settings
    from app.main import create_app

    settings = get_settings()
    restore = settings.stores, settings.db_bootstrap
    # The transport wiring is what is under test; the sweepers, the Temporal worker and
    # the schema bootstrap are not, and each needs infrastructure this test does not
    # want to speak for.
    settings.stores, settings.db_bootstrap = "off", False
    app = create_app()
    try:
        # TWICE, on the SAME app. The SDK's session manager may only be run once, so a
        # mount holding one transport for the life of the process raises on the second
        # entry — which is not hypothetical: this suite enters ``app.main.lifespan``
        # from four other tests, and any host that restarts does the same.
        first, first_served = await _handshake(app)
        second, second_served = await _handshake(app)
    finally:
        settings.stores, settings.db_bootstrap = restore

    for resp in (first, second):
        assert resp.status_code == 200, resp.text
        assert resp.headers.get("mcp-session-id")
    assert first_served is not second_served
    # The URL a Claude Desktop config carries, plus the OAuth protected-resource
    # metadata the SDK publishes beside it.
    assert [getattr(r, "path", None) for r in second_served.routes] == [
        MCP_PATH,
        "/.well-known/oauth-protected-resource",
    ]


async def _handshake(app):
    """Enter ``app``'s lifespan, handshake over the mount, return (response, transport)."""
    async with app.router.lifespan_context(app):
        served = app.state.mcp_mount.app
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http:
            resp = await http.post(
                f"{MCP_MOUNT}{MCP_PATH}",
                json=_INITIALIZE,
                headers={
                    "Authorization": f"Bearer {_probe_token()}",
                    "Accept": "application/json, text/event-stream",
                },
            )
    return resp, served


def _probe_token() -> str:
    """A structurally valid Aegis bearer — enough to pass the transport's auth gate.

    It authenticates the *connection*; it authorises nothing, because every RBAC and
    tenant decision is re-derived per call from the live ``users`` row.
    """
    return create_access_token(
        user_id=1,
        username="mount-probe",
        role="platform_admin",
        coarse_role="admin",
        tenant_id=None,
    )
