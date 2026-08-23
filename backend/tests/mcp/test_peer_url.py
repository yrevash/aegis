"""A declared MCP peer is an outbound request Aegis makes — from inside its network.

Why this file exists. ``POST /mcp/servers`` took ``url: str`` with no scheme, host or
range validation, and :func:`app.mcp.client.connect` dialed whatever it was given. So a
platform admin — or anything holding a platform-admin token — could declare a peer at
``http://169.254.169.254/latest/meta-data/``, press *test connection*, and have the
deployment fetch cloud instance metadata from a network position the caller does not
have and render the answer in the console. ``http://localhost:5432`` is the same move
aimed at this host's own database. That is a server-side request forgery whose whole
value is that the *server* makes the request; being admin-only bounds who can trigger
it, not what it reaches.

What is pinned here is the load-bearing claim (the two URLs above are refused, at both
the declare and the edit door) and its failure modes: the empty URL of an in-process
peer must still be accepted, a public peer must still be declarable, and the
deployment's explicit opt-in must actually opt in — a control that cannot be turned off
for a real sidecar gets turned off by deleting it.

What this does NOT claim, and is asserted here so the limit is visible rather than
assumed: no DNS is resolved, so a hostname whose answer is a private address passes.
Closing that needs resolution at connect time with the address pinned, which is a
transport change; anything done at declare time is a TOCTOU window against rebinding.
"""

from __future__ import annotations

import pytest

from app.mcp.client import (
    ExternalServerSpec,
    ExternalToolRegistry,
    PeerUrlRefused,
    validate_peer_url,
)


@pytest.mark.parametrize(
    "url",
    [
        # The two from the finding, verbatim.
        "http://169.254.169.254/latest/meta-data/",
        "http://localhost:5432",
        # The rest of the address space that means "inside".
        "http://127.0.0.1:8110/mcp",
        "http://10.0.0.5/mcp",
        "http://192.168.1.10/mcp",
        "http://172.16.0.1/mcp",
        "http://[::1]:9000/mcp",
        "http://0.0.0.0:8000/mcp",
        # IPv4-mapped IPv6: the same link-local address, spelled so a prefix denylist
        # written against dotted quads would miss it.
        "http://[::ffff:169.254.169.254]/mcp",
        # RFC 6761 reserves the whole .localhost tree to the loopback interface.
        "http://api.localhost/mcp",
    ],
)
def test_a_peer_pointed_inside_the_network_is_refused(url: str) -> None:
    with pytest.raises(PeerUrlRefused) as exc:
        validate_peer_url(url, allow_private=False)
    # The refusal names the opt-in, because an operator with a genuine sidecar needs to
    # know there is a supported way through rather than a wall.
    assert "AEGIS_MCP_ALLOW_PRIVATE_PEERS" in str(exc.value)


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "gopher://127.0.0.1:6379/_INFO",
        "ftp://example.com/",
        # No scheme at all. Refused explicitly rather than defaulted to http, because
        # this parses with an empty host and would otherwise pass every later check.
        "169.254.169.254",
        "example.com/mcp",
    ],
)
def test_a_url_that_is_not_dialable_http_is_refused(url: str) -> None:
    with pytest.raises(PeerUrlRefused):
        validate_peer_url(url, allow_private=False)


@pytest.mark.parametrize(
    "url",
    [
        # The in-process peer: no URL means nothing is dialed, so there is no
        # destination to validate. Refusing this would break the test seam and any
        # deployment that composes a peer in process.
        "",
        "https://mcp.example.com/mcp",
        "http://peer.internal.example.com:8080/mcp",
        # A public IP literal is fine — the rule is about address *space*, not literals.
        "https://93.184.216.34/mcp",
    ],
)
def test_a_peer_aegis_will_dial_is_accepted_unchanged(url: str) -> None:
    assert validate_peer_url(url, allow_private=False) == url


def test_the_deployment_opt_in_actually_opts_in() -> None:
    """A sidecar MCP server on the same host is a real arrangement, not an attack.

    The control has to have a supported way through or it gets removed rather than
    configured — but it is a *deployment* setting, so a request body cannot assert it.
    """
    url = "http://127.0.0.1:9000/mcp"
    with pytest.raises(PeerUrlRefused):
        validate_peer_url(url, allow_private=False)
    assert validate_peer_url(url, allow_private=True) == url


def test_a_hostname_resolving_inside_is_not_caught_and_this_is_deliberate() -> None:
    """The stated limit of this control, pinned so it cannot be quietly overclaimed.

    ``validate_peer_url`` resolves nothing. A peer declared at a name whose DNS answer
    is 169.254.169.254 is accepted and dialed. Checking at declare time would be a
    TOCTOU window against rebinding rather than a control; the real fix is resolving at
    connect time and pinning the address, and until that exists the residual risk is
    bounded by network egress policy around the deployment.
    """
    assert validate_peer_url("http://metadata.attacker.example/", allow_private=False)


def test_both_doors_are_checked_not_only_the_declare_one() -> None:
    """Re-pointing an existing peer reaches exactly what declaring one there would.

    The registry is the chokepoint rather than the route, so the console write, the
    ``AEGIS_MCP_CLIENT_SERVERS`` parse and ``load_servers`` re-hydrating an old row all
    go through the same check — a validation that lives in one handler is one that the
    next caller forgets.
    """
    registry = ExternalToolRegistry()
    registry.register_server(ExternalServerSpec(server_id="acme", url="https://a.example/mcp"))
    with pytest.raises(PeerUrlRefused):
        registry.update_server("acme", url="http://169.254.169.254/")
    with pytest.raises(PeerUrlRefused):
        registry.register_server(
            ExternalServerSpec(server_id="beta", url="http://localhost:5432")
        )
    # Refused means not written: the peer still points where it did, and the second
    # server was never declared.
    assert registry.server("acme").url == "https://a.example/mcp"
    assert [s.server_id for s in registry.servers()] == ["acme"]


def test_the_registry_honours_its_own_opt_in_flag() -> None:
    registry = ExternalToolRegistry(allow_private_peers=True)
    spec = registry.register_server(
        ExternalServerSpec(server_id="sidecar", url="http://127.0.0.1:9000/mcp")
    )
    assert spec.url == "http://127.0.0.1:9000/mcp"
