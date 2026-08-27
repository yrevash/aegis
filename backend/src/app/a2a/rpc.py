"""The A2A JSON-RPC surface, and the one refusal that makes it safe.

Two methods, in the 1.0 spelling — **PascalCase**, `SendMessage` and `GetTask`, not the
`message/send` form the 0.x drafts used. Getting that wrong produces a server that looks
finished and interoperates with nothing.

## The security property

`tenant` in A2A is an **opaque routing identifier**. The client copies it from whichever
`AgentInterface` it selected, it travels *before* authentication, and it is entirely
attacker-controlled.

Aegis's tenancy is a Postgres GUC derived from a verified bearer token. Conflating the two
would hand any caller a tenant selector — the whole isolation model, defeated by a string
in a request body.

So: **`tenant` selects which agent is being addressed. It never sets `app.tenant_id`.** The
RLS scope comes from the token and from nothing else, exactly as `/v1/query` already works.
When the routing field disagrees with what the token resolves to, the request is **refused**
rather than reconciled — reconciling would mean silently honouring one of them, and
whichever one you pick, an attacker has learned which.

That refusal is the most important behaviour in this module, and it has the test to match.
"""

from __future__ import annotations

import uuid
from typing import Any

__all__ = ["A2A_METHODS", "TenantMismatchError", "resolve_addressed_tenant"]

#: The methods this surface answers. Named here so the card, the router and the tests
#: cannot drift apart — an interface that advertises a method it does not serve is worse
#: than one that advertises fewer.
A2A_METHODS = ("SendMessage", "GetTask")


class TenantMismatchError(ValueError):
    """The routing tenant and the authenticated tenant are not the same.

    Raised rather than resolved. Picking either value would be a decision made on an
    attacker's behalf, and the difference between "wrong tenant" and "no such tenant" is
    itself an oracle.
    """


def resolve_addressed_tenant(
    *, routed: str | None, authenticated: int | None
) -> int | None:
    """Decide which tenant a request is for, refusing rather than reconciling.

    Args:
        routed: The `tenant` routing field from the request. Untrusted.
        authenticated: The tenant the bearer token resolves to, or ``None`` for a
            platform-scoped principal.

    Returns:
        The authenticated tenant — always, when it agrees. This function never *widens*
        a scope; its only outputs are the token's own scope or an exception.

    Raises:
        TenantMismatchError: The routing field names a tenant the token does not.
    """
    if routed is None or routed == "":
        # No routing preference expressed. The token decides, which is the default and
        # the safe case.
        return authenticated

    # `str.isdigit()` and `int()` both accept non-ASCII digits — `int("٧")` is 7, and
    # so are a dozen other scripts' sevens. A routing identifier that can be written in
    # forms that differ on the wire but compare equal after parsing is a filter-evasion
    # primitive: whatever logs, rate-limits or blocklists this value sees one string and
    # the comparison sees another. ASCII digits only, checked before parsing.
    if not routed.isascii() or not routed.isdecimal():
        raise TenantMismatchError(
            "the addressed tenant is not the tenant this credential belongs to"
        )

    try:
        wanted = int(routed)
    except (TypeError, ValueError):
        # A non-numeric routing id cannot match any Aegis tenant. Refused with the same
        # error as a mismatch, deliberately: "malformed" and "not yours" must be
        # indistinguishable, or the shape of the error becomes a probe.
        raise TenantMismatchError(
            "the addressed tenant is not the tenant this credential belongs to"
        ) from None

    if authenticated is None or wanted != authenticated:
        raise TenantMismatchError(
            "the addressed tenant is not the tenant this credential belongs to"
        )
    return authenticated


def task_id() -> str:
    """A fresh A2A task id."""
    return uuid.uuid4().hex


def as_task(
    *, task: str, state: str, text: str = "", context: str | None = None
) -> dict[str, Any]:
    """Shape one A2A `Task` result.

    States are the spec's own: ``TASK_STATE_SUBMITTED``, ``TASK_STATE_WORKING``,
    ``TASK_STATE_COMPLETED``, ``TASK_STATE_FAILED``, and the rest. They are strings here
    rather than an enum because the wire is the contract and a typo should fail a test,
    not be silently accepted by a lenient constructor.
    """
    result: dict[str, Any] = {"id": task, "status": {"state": state}}
    if context is not None:
        result["contextId"] = context
    if text:
        result["artifacts"] = [
            {"artifactId": f"{task}-answer", "parts": [{"kind": "text", "text": text}]}
        ]
    return result


def rpc_error(code: int, message: str, *, rpc_id: Any = None) -> dict[str, Any]:
    """A JSON-RPC 2.0 error object, in an envelope."""
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def rpc_result(result: Any, *, rpc_id: Any = None) -> dict[str, Any]:  # noqa: ANN401
    """A JSON-RPC 2.0 success envelope."""
    return {"jsonrpc": "2.0", "id": rpc_id, "result": result}
