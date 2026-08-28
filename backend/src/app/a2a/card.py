"""The A2A Agent Card, built from what this platform actually exposes.

## Why this is hand-written rather than taken from the SDK

`a2a-sdk` 1.1.2 exists and resolves into this venv, at the cost of downgrading protobuf
from 7.35.1 to 6.33.6 — underneath Temporal, which every ingest depends on, and ONNX
Runtime. That downgrade was gated and the import check passed, so it is *survivable*; it
is still a dependency change with a blast radius far larger than the surface it buys,
which is one JSON document and two RPC methods. The field names below were verified
against the 1.0 specification directly, which is the part that actually decides
interoperability.

## The two cards, and why there are two

A2A discovery is unauthenticated: anything served at `/.well-known/agent-card.json` is
public. Aegis's skills are **per persona** — the tool registry is filtered by an
allowlist, and which tools exist is itself information about the deployment.

So the public card advertises what Aegis *is* and what interfaces it speaks, and lists
skills only at the granularity a stranger may know. The full, persona-filtered skill
catalogue belongs behind authentication, which is what the spec's `extendedAgentCard`
capability is for.

## The one thing in here that is a security property, not a field

`tenant` in A2A is, per the spec, an **opaque routing identifier** that the client copies
from the interface it selected. It arrives *before* authentication and is entirely
attacker-controlled.

Aegis's tenancy is a Postgres GUC set from a verified bearer token. These are not the same
thing and must never be conflated: `tenant` selects *which agent is being addressed*, and
it never, under any circumstance, sets `app.tenant_id`. If the two disagree the request is
refused rather than reconciled — see `app.a2a.rpc`.
"""

from __future__ import annotations

from typing import Any

from app.capabilities import PRODUCT_NAME, PRODUCT_TAGLINE, PRODUCT_VERSION

__all__ = ["build_public_card", "card_skills"]

#: The protocol revision this card claims. `Major.Minor` only — the spec says patch
#: numbers SHOULD NOT appear in requests, responses or cards.
A2A_PROTOCOL_VERSION = "1.0"


def card_skills() -> list[dict[str, Any]]:
    """The skills a stranger may be told about.

    Deliberately **not** the tool registry. A2A skills are things a caller can ask for,
    and the registry is per persona — publishing it unauthenticated would tell an
    anonymous reader which tools this deployment holds and therefore what it can be asked
    to do. These are the capabilities the product already describes publicly.
    """
    return [
        {
            "id": "answer-with-provenance",
            "name": "Answer with provenance",
            "description": (
                "Answer a question against the caller's own tenant corpus and return the "
                "sources the answer stands on, with the retrieval path that found them."
            ),
            "tags": ["rag", "retrieval", "provenance"],
            "examples": ["What does our refund policy say about damaged goods?"],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        },
        {
            "id": "governed-action",
            "name": "Take a governed action",
            "description": (
                "Perform a consequential action under the platform's risk gate. Anything "
                "at or above the configured threshold stops for a named human before it "
                "runs, and every call is audited with its actor and approver."
            ),
            "tags": ["tools", "approval", "audit"],
            "examples": ["Resolve request R-1042 and note why."],
            "inputModes": ["text/plain"],
            "outputModes": ["text/plain"],
        },
    ]


def build_public_card(*, base_url: str) -> dict[str, Any]:
    """The unauthenticated Agent Card served at the well-known path.

    Pure: no I/O, no settings lookup beyond the base URL it is told. That makes it
    testable without a running server, and it makes the signing step — which must
    canonicalise exactly these bytes — reproducible.

    Args:
        base_url: The externally reachable origin, e.g. ``https://aegis.example``. Used
            to build the interface URLs; no trailing slash.

    Returns:
        The card as a plain dict, **without** ``signatures``. Signing adds that field, and
        the canonical form that gets signed is defined as the card without it.
    """
    origin = base_url.rstrip("/")
    return {
        "name": PRODUCT_NAME,
        "description": PRODUCT_TAGLINE,
        "version": PRODUCT_VERSION,
        "protocolVersion": A2A_PROTOCOL_VERSION,
        # `supportedInterfaces`, not the 0.x `url` + `additionalInterfaces` pair — the
        # 1.0 migration notes call that reshaping a breaking change.
        "supportedInterfaces": [
            {
                "url": f"{origin}/v1/a2a",
                "protocolBinding": "JSONRPC",
                "protocolVersion": A2A_PROTOCOL_VERSION,
            }
        ],
        "provider": {
            "organization": PRODUCT_NAME,
            "url": origin,
        },
        "documentationUrl": f"{origin}/docs",
        "capabilities": {
            # All three false, and two of them used to be true here for no reason the
            # server could back up.
            #
            # `streaming` claimed that `SendStreamingMessage` "maps onto" the SSE run.
            # It does not — the method is not in `A2A_METHODS` and a peer calling it
            # gets `-32601 method not found`. `app/a2a/routes.py` says so in its own
            # docstring ("which this surface does not implement and does not
            # advertise"), so the two modules were contradicting each other and the
            # card was the one telling peers the wrong thing.
            #
            # `extendedAgentCard` claimed a `GetAuthenticatedExtendedCard` method that
            # is likewise absent; `?extended=true` returns bytes identical to the
            # public card.
            #
            # A peer chooses its call based on these flags, so an unearned `true` here
            # is worse than a `false`: it routes a working client into a method that
            # cannot answer. This module's own standard — "an advertised capability
            # that cannot be exercised" is worse than shipping fewer — decides it.
            "streaming": False,
            "pushNotifications": False,
            "extendedAgentCard": False,
        },
        "securitySchemes": {
            "bearer": {
                "type": "http",
                "scheme": "bearer",
                "bearerFormat": "JWT",
                "description": (
                    "An Aegis access token. The tenant scope is derived from this token "
                    "and from nothing else — in particular, never from the request's "
                    "`tenant` routing field."
                ),
            }
        },
        "securityRequirements": [{"bearer": []}],
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": card_skills(),
    }
