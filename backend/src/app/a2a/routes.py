"""The A2A HTTP surface: discovery at the well-known path, and the JSON-RPC endpoint.

Discovery is served at the root rather than under ``/v1`` because the specification fixes
the path and registers it with IANA. A well-known URI that is not where the standard says
it is has not been served.
"""

from __future__ import annotations

import hashlib
import json
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


def _origin() -> str:
    """The origin this deployment publishes as its own identity — from configuration only.

    **Never from the request.** An earlier version read ``request.base_url``, which
    honours the ``Host`` header: a request carrying ``Host: evil.com`` came back with a
    card, signed by this platform's real key, whose interface URL and whose ``jku``
    inside the *signed* protected header both pointed at the attacker. Aegis's own
    signature then certified a document telling peers to send bearer tokens elsewhere,
    and the response was cacheable for five minutes. Attacker-controlled input must never
    reach the inside of a signature.

    Returns:
        The configured origin without a trailing slash, or ``""`` when none is set.
    """
    return str(getattr(get_settings(), "a2a_public_origin", "") or "").rstrip("/")


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
    origin = _origin()
    if not origin:
        # No configured identity, so nothing here can be signed: a signature over a
        # guessed origin is worth less than no signature, because it looks authoritative.
        # The card still describes the agent honestly; it simply carries relative
        # interface URLs and no `signatures` array.
        card = build_public_card(base_url="")
        response.headers["Cache-Control"] = "no-store"
        return card

    card = build_public_card(base_url=origin)
    signed = sign_card(card, jku=f"{origin}/.well-known/jwks.json")
    body = json.dumps(signed, sort_keys=True, separators=(",", ":")).encode()
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

    # SendMessage — and it runs the agent.
    #
    # An earlier version returned TASK_STATE_SUBMITTED and did nothing, while the signed
    # public card advertised two concrete skills. A peer running any A2A client got a
    # task id and silence, and `GetTask` sent them to a stream that does not exist. That
    # is worse than not shipping the surface: it is an advertised capability that cannot
    # be exercised, which is exactly the class of claim this platform refuses everywhere
    # else.
    text = _text_of(params.get("message"))
    if not text:
        return rpc_error(-32602, "message must carry at least one text part", rpc_id=rpc_id)

    task = task_id()
    try:
        answer = await _run(text, auth=auth)
    except Exception as exc:  # noqa: BLE001 - a failed run is a task state, not a 500
        return rpc_result(
            as_task(
                task=task,
                state="TASK_STATE_FAILED",
                text=f"the run did not complete: {exc}",
                context=str(getattr(auth, "tenant_id", "") or ""),
            ),
            rpc_id=rpc_id,
        )

    return rpc_result(
        as_task(
            task=task,
            state="TASK_STATE_COMPLETED",
            text=answer,
            context=str(getattr(auth, "tenant_id", "") or ""),
        ),
        rpc_id=rpc_id,
    )


def _text_of(message: Any) -> str:
    """Pull the text out of an A2A `Message`, tolerating shapes we do not use."""
    if not isinstance(message, dict):
        return ""
    parts = message.get("parts")
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            out.append(part["text"])
    return " ".join(out).strip()


async def _run(text: str, *, auth: Any) -> str:  # noqa: ANN401 - AuthContext
    """Drive one agent run to its answer, scoped by the caller's own token.

    Non-streaming: A2A's `SendMessage` is the unary call, and `SendStreamingMessage` —
    which this surface does not implement and does not advertise — is the streaming one.
    Collecting tokens here rather than inventing a task store keeps the honest property
    that a task's result is returned to the caller who asked for it.
    """
    from aegis.governance.context import (
        reset_governance_context,
        set_governance_context,
    )

    from app.adapter import persona_for_role
    from app.agent import get_approval_registry, run_agent
    from app.api.routes import _resolve_governance, get_agent_deps

    deps = get_agent_deps()
    chunks: list[str] = []

    # Bind the caller's tenant/user + caps, exactly as `/v1/query` does.
    #
    # Without this the run is unattributed and free: measured side by side on one
    # deployment, an A2A run spending $0.0107 wrote **zero** usage_ledger rows and
    # recorded `tenant=None user=None`, while an equivalent /v1/query run wrote 14 rows
    # with the right tenant. Authenticated, guardrailed — and invisible to the cost
    # surface.
    #
    # This is the same defect the evals route carried, which `routes.py` itself calls
    # "the one place this platform's metering claim is false". Fixing it there and
    # leaving it here would have meant the claim was still false, just somewhere else.
    governance = await _resolve_governance(auth)
    token = set_governance_context(governance)
    try:
        async for event in run_agent(
            text,
            persona=persona_for_role(auth.role),
            role=auth.role.value,
            deps=deps,
            registry=get_approval_registry(),
        ):
            if getattr(event, "type", "") == "token":
                chunks.append(getattr(event, "text", ""))
    finally:
        reset_governance_context(token)
    return "".join(chunks).strip()
