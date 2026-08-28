"""Signing the Agent Card — this platform's first asymmetric key.

Aegis authenticates with `HS256` and a symmetric `jwt_secret`. That secret mints every
access token in the product, so it cannot sign a public artefact: verifying the signature
would require handing every peer the key that issues credentials. Card signing therefore
introduces the first keypair, and it is deliberately a *different* key with a *different*
job — this one proves "this card came from the domain that claims it", and it can never
authenticate anybody.

## The canonical form

A signature is only checkable if the verifier reconstructs exactly the bytes that were
signed. The spec's rule is RFC 8785 (JCS) over the card **excluding `signatures`** — which
matters, because the signature cannot cover itself. Key order is lexicographic, there is no
insignificant whitespace, and unset fields are omitted rather than emitted as null.

## What happens without a configured key

The card is served **unsigned**, and says so by simply having no `signatures` array. It does
not get a fabricated signature, a self-signed placeholder, or a `"signed": false` field that
a careless reader would skim past. An unsigned card is a legible state; a card that looks
signed and is not is a lie with a cryptographic costume on.
"""

from __future__ import annotations

import base64
import json
import logging
from typing import Any

__all__ = ["canonical_card", "jwks", "sign_card"]

logger = logging.getLogger(__name__)

#: Set once, lazily, the first time a key is needed. A process-lifetime ephemeral key is
#: the honest default: it signs consistently for as long as this process serves the card,
#: and it does not pretend to be an identity that survives a restart.
_KEY: Any = None
_KID: str = ""


def _b64(raw: bytes) -> str:
    """Base64url, unpadded — the JOSE encoding."""
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def canonical_card(card: dict[str, Any]) -> bytes:
    """RFC 8785 canonical JSON of a card, with `signatures` excluded.

    Excluded rather than emptied: a signature that covered an empty `signatures` array
    would break the moment one was added, so the signed form is the card *without* the
    field at all.
    """
    body = {k: v for k, v in card.items() if k != "signatures"}
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _key() -> tuple[Any, str]:
    """The ES256 signing key and its id, generated once per process.

    Persisting this belongs with `jwt_secret` and its production strength check; until it
    is configured there, an ephemeral key is what an honest deployment has. The log line
    exists so nobody discovers by surprise that peer-cached keys stopped verifying after a
    restart.
    """
    global _KEY, _KID  # noqa: PLW0603 - a process-lifetime singleton, by design
    if _KEY is None:
        from cryptography.hazmat.primitives.asymmetric import ec

        _KEY = ec.generate_private_key(ec.SECP256R1())
        raw = _KEY.public_key().public_numbers()
        _KID = _b64(
            raw.x.to_bytes(32, "big")[:8] + raw.y.to_bytes(32, "big")[:8]
        )
        logger.warning(
            "A2A card signing key generated for this process only (kid=%s). Peers that "
            "cache the JWKS will fail to verify after a restart. Configure a persistent "
            "key before anyone relies on the signature.",
            _KID,
        )
    return _KEY, _KID


def jwks() -> dict[str, Any]:
    """The public half, as a JWKS a peer can fetch to verify the card."""
    from cryptography.hazmat.primitives.asymmetric import ec

    key, kid = _key()
    numbers = key.public_key().public_numbers()
    assert isinstance(key.curve, ec.SECP256R1)  # noqa: S101 - narrows the type for readers
    return {
        "keys": [
            {
                "kty": "EC",
                "crv": "P-256",
                "use": "sig",
                "alg": "ES256",
                "kid": kid,
                "x": _b64(numbers.x.to_bytes(32, "big")),
                "y": _b64(numbers.y.to_bytes(32, "big")),
            }
        ]
    }


def sign_card(card: dict[str, Any], *, jku: str) -> dict[str, Any]:
    """Return the card with an `AgentCardSignature` attached.

    Args:
        card: The unsigned card.
        jku: URL of the JWKS a verifier should fetch. Carried in the protected header so
            a peer can find the key without being told out of band.

    Returns:
        A new dict — the input is not mutated — carrying `signatures`.
    """
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    key, kid = _key()
    protected = {"alg": "ES256", "typ": "JOSE", "kid": kid, "jku": jku}
    protected_b64 = _b64(json.dumps(protected, sort_keys=True, separators=(",", ":")).encode())
    payload_b64 = _b64(canonical_card(card))
    signing_input = f"{protected_b64}.{payload_b64}".encode("ascii")

    der = key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
    # JOSE wants the raw r||s pair, not the DER envelope OpenSSL hands back.
    r, s = utils.decode_dss_signature(der)
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")

    return {
        **card,
        "signatures": [{"protected": protected_b64, "signature": _b64(raw)}],
    }
