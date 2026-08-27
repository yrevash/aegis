"""The A2A routing tenant never becomes the database tenant.

This is the most important test in the A2A surface, and the reason is worth stating
plainly rather than leaving to the reader.

A2A's ``tenant`` is, per the specification, an **opaque routing identifier**: the client
copies it from whichever interface it selected. It travels in the request body, it arrives
*before* authentication, and it is entirely attacker-controlled.

Aegis's tenancy is a Postgres GUC set from a verified bearer token, with row-level security
underneath it. If those two were conflated — if the routing string were allowed anywhere
near ``app.tenant_id`` — then the entire isolation model would be defeated by a string in a
request body. Not a subtle bug: a total one.

So the rule is that the routing field selects *which agent is being addressed*, and the
token decides *what data is in scope*, and when they disagree the request is refused rather
than reconciled. Reconciling would mean silently honouring one of them, and whichever one
you honour, the caller has learned something about the other.
"""

from __future__ import annotations

import pytest

from app.a2a.rpc import TenantMismatchError, resolve_addressed_tenant


def test_no_routing_field_defers_to_the_token() -> None:
    """The ordinary case: no preference expressed, the token decides."""
    assert resolve_addressed_tenant(routed=None, authenticated=7) == 7
    assert resolve_addressed_tenant(routed="", authenticated=7) == 7


def test_an_agreeing_routing_field_changes_nothing() -> None:
    """Agreement is not permission — the answer is still the token's own scope.

    Worth asserting explicitly: this function's only outputs are the token's scope or an
    exception. There is no path on which a routing field *widens* anything.
    """
    assert resolve_addressed_tenant(routed="7", authenticated=7) == 7


def test_a_disagreeing_routing_field_is_refused_not_reconciled() -> None:
    """The attack this exists to stop.

    A caller holding tenant 7's credential asks to be routed to tenant 9. There is no
    correct way to serve that request: honouring the token silently ignores what was
    asked, and honouring the routing field is a cross-tenant read.
    """
    with pytest.raises(TenantMismatchError):
        resolve_addressed_tenant(routed="9", authenticated=7)


def test_a_platform_principal_cannot_be_routed_into_a_tenant() -> None:
    """Platform scope is not a master key for the routing field.

    A principal with no bound tenant has the broadest data scope in the product, which
    makes this the most tempting place to let a routing string pick one. It does not:
    a scope that came from a token is not something a request body may narrow, because
    "narrow" and "redirect" are the same operation from the outside.
    """
    with pytest.raises(TenantMismatchError):
        resolve_addressed_tenant(routed="3", authenticated=None)


@pytest.mark.parametrize("garbage", ["abc", "7; DROP TABLE tenants", "٧", "7.0", "-1"])
def test_a_malformed_routing_field_fails_the_same_way_as_a_wrong_one(garbage: str) -> None:
    """Malformed and not-yours must be indistinguishable.

    If a bad id raised a parse error and a wrong id raised a permission error, the shape
    of the failure would tell a caller which tenant ids exist — an oracle assembled from
    error types rather than from data. Same exception, same message, every time.
    """
    with pytest.raises(TenantMismatchError):
        resolve_addressed_tenant(routed=garbage, authenticated=7)


def test_the_refusal_message_names_no_tenant() -> None:
    """The error text must not leak what was asked for or what was held.

    An error that says "you asked for 9 but hold 7" hands back both halves of the thing
    the refusal was protecting.
    """
    for routed, held in (("9", 7), ("abc", 7), ("3", None)):
        try:
            resolve_addressed_tenant(routed=routed, authenticated=held)
        except TenantMismatchError as exc:
            message = str(exc)
            assert "9" not in message
            assert "7" not in message
            assert "3" not in message
        else:  # pragma: no cover - the call above must raise
            pytest.fail(f"routed={routed!r} held={held!r} was not refused")
