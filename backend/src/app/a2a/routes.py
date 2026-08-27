"""The A2A HTTP surface: discovery at the well-known path, and the JSON-RPC endpoint.

Discovery is served at the root rather than under ``/v1`` because the specification fixes
the path and registers it with IANA. A well-known URI that is not where the standard says
it is has not been served.
"""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, Depends, Request, Response

from app.a2a.card import build_public_card
from app.a2a.rpc import (
    A2A_METHODS,
    TenantMismatchError,
    as_task,
    resolve_addressed_tenant,
    rpc_error,
    rpc_result,
    task_id,
)
from app.a2a.signing import jwks as build_jwks
from app.a2a.signing import sign_card
# Imported inside the module body rather than at the top of `app.api.routes`'s import
# graph: `routes.py` is the composition root for every product endpoint and importing it
# from here at module scope would close a cycle through `main.py`.
from app.api.routes import AuthContext, require_auth
from app.config import get_settings

router = APIRouter(tags=["a2a"])


def _origin(request: Request) -> str:
    """The externally reachable origin for this request.

    Taken from the request rather than from configuration so the card advertises URLs a
    caller can actually reach — a card served on localhost that advertises a production
    hostname is a card that tells peers to go somewhere they cannot.
    """
    configured = getattr(get_settings(), "public_base_url", "") or ""
    if configured:
        return str(configured).rstrip("/")
    return str(request.base_url).rstrip("/")


@router.get("/.well-known/agent-card.json", include_in_schema=False)
async def agent_card(request: Request, response: Response) -> dict[str, Any]:
    """The public, signed Agent Card.

    Unauthenticated by design: discovery is how a peer finds out what this agent is, and
    it carries nothing a stranger may not know. The persona-filtered skill catalogue is
    deliberately *not* here — see ``app.a2a.card``.

    An ``ETag`` over the signed bytes lets a peer revalidate cheaply, which the spec asks
    for. ``max-age`` is short on purpose: the signing key is per process today, so a long
    cache would leave peers holding a key that no longer verifies.
    """
    origin = _origin(request)
    card = build_public_card(base_url=origin)
    signed = sign_card(card, jku=f"{origin}/.well-known/jwks.json")
    body = repr(signed).encode()
    response.headers["Cache-Control"] = "public, max-age=300"
    response.headers["ETag"] = f'"{hashlib.sha256(body).hexdigest()[:32]}"'
    return signed


@router.get("/.well-known/jwks.json", include_in_schema=False)
async def agent_jwks() -> dict[str, Any]:
    """The public key a peer uses to verify the card's signature.

    Only ever the public half. This key signs one artefact and authenticates nobody — it
    is deliberately not the symmetric secret that mints access tokens.
    """
    return build_jwks()


@router.post("/v1/a2a")
async def a2a_rpc(
    request: Request,
    auth: AuthContext = Depends(require_auth),
) -> dict[str, Any]:
    """The A2A JSON-RPC endpoint — `SendMessage` and `GetTask`.

    **Authenticated, and scoped by the token alone.** The `tenant` routing field in the
    request body is attacker-controlled and arrives before authentication; it selects
    which agent is addressed and never sets the database tenant scope. When it disagrees
    with the token, this refuses rather than reconciling — reconciling would mean
    silently honouring one of them, and whichever one you honour, a caller has learned
    something about the other.
    """
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 - a malformed body is a protocol error, not a 500
        return rpc_error(-32700, "parse error")

    rpc_id = body.get("id") if isinstance(body, dict) else None
    method = body.get("method") if isinstance(body, dict) else None
    params = body.get("params") or {} if isinstance(body, dict) else {}

    if method not in A2A_METHODS:
        return rpc_error(-32601, f"method not found: {method!r}", rpc_id=rpc_id)

    try:
        resolve_addressed_tenant(
            routed=params.get("tenant"),
            authenticated=getattr(auth, "tenant_id", None),
        )
    except TenantMismatchError as exc:
        # -32602 (invalid params) rather than 403: the request is well-formed and
        # authenticated, and it addresses something this credential does not cover. The
        # message is identical whether the tenant is wrong, malformed or absent, so the
        # error cannot be used to enumerate tenants.
        return rpc_error(-32602, str(exc), rpc_id=rpc_id)

    if method == "GetTask":
        wanted = str(params.get("id") or "")
        if not wanted:
            return rpc_error(-32602, "id is required", rpc_id=rpc_id)
        # Task persistence is not built. Saying so is better than inventing a store:
        # a peer that polls a task this server never kept needs to know that now.
        return rpc_error(
            -32004,
            "task history is not retained by this agent; a task is observable only on "
            "the stream that created it",
            rpc_id=rpc_id,
        )

    # SendMessage. The run itself is deliberately not wired here yet — this returns a
    # task in SUBMITTED rather than pretending to have completed work it did not do.
    return rpc_result(
        as_task(
            task=task_id(),
            state="TASK_STATE_SUBMITTED",
            context=str(getattr(auth, "tenant_id", "") or ""),
        ),
        rpc_id=rpc_id,
    )
