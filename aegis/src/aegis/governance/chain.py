"""Canonical serialisation for the tamper-evident audit chain.

A hash is only evidence if the verifier can reconstruct, byte for byte, exactly what the
writer hashed. That sounds trivial and is not: four things about this schema break it,
and each one is handled here rather than left to whoever writes the verifier next.

**1. Length-prefixed framing, not a delimiter.** ``actor``, ``model``, ``trace_id`` and
``approved_by`` are all nullable. If ``None`` and ``""`` serialise the same way, a field
can be blanked without breaking the hash — which is precisely the edit an attacker would
want. And a delimiter can be impersonated: an ``action`` containing the separator could
forge a field boundary. ``len:value`` framing makes both impossible, and it is why this
is not a ``"|".join(...)``.

**2. Fixed timestamp precision.** ``isoformat()`` drops the fractional part when it is
exactly zero, so a row landing on a whole second hashes differently on the writer and the
verifier. That reproduces roughly once in a million rows — the worst possible frequency,
common enough to happen in production and rare enough to be dismissed as a fluke. Always
six digits.

**3. ``id`` is excluded, ``ts`` is included.** ``id`` is a serial the database assigns
after the app must hash, and it carries no evidentiary content. ``ts`` is included and
must therefore be supplied by the writer rather than by ``func.now()``. The honest cost of
that is written down in the model: ``ts`` stops meaning "the database's clock" and starts
meaning "the writing process's clock", so on a multi-host deployment audit timestamps can
disagree between hosts. The chain does not care — its order is ``prev_hash`` — but the
read path's ``ORDER BY ts DESC`` does.

**4. ``jsonb`` is not a byte-preserving store.** This is the deepest trap. PostgreSQL's
``jsonb`` discards key order, drops duplicate keys, and normalises numeric formatting — so
``H(what the app sent)`` and ``H(what the verifier reads back)`` are not the same function
of the same data. The fix is in two parts and the second is the one that is easy to skip
and fatal to skip: canonicalise the payload *and store the canonical form*, so the value
in the column is already a fixed point. Without it a payload containing ``1.0`` comes back
as ``1`` and a row nobody touched fails verification. **A verifier that cries wolf is a
verifier that gets turned off.**
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

__all__ = [
    "GENESIS",
    "canonical_payload",
    "chain_hash",
    "row_fingerprint",
]

#: The ``prev_hash`` of the first row in a chain. A literal marker rather than NULL, so
#: "this is where the chain starts" is distinguishable from "this row predates the chain
#: and nobody hashed it" — which is a claim we must never blur, because we cannot prove
#: anything about history that was written before the hashing existed.
GENESIS = "genesis"


def _frame(value: str | None) -> str:
    """Length-prefix one field so it cannot be confused with any other framing.

    ``None`` frames as ``-``; a string frames as ``<byte-length>:<value>``. The length is
    in **UTF-8 bytes**, not characters, so a multi-byte value cannot be padded to
    impersonate a different one.
    """
    if value is None:
        return "-"
    encoded = value.encode("utf-8")
    return f"{len(encoded)}:{value}"


def canonical_payload(payload: dict[str, Any] | None) -> str:
    """Render a payload the one way both writer and verifier will render it.

    Sorted keys, no insignificant whitespace, and ``ensure_ascii=False`` so a non-ASCII
    value has exactly one representation rather than two that differ only in escaping.

    The caller **must store the round-tripped form** — ``json.loads(canonical_payload(d))``
    — not the dict it started with. See this module's docstring, point 4.
    """
    if not payload:
        return "{}"
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def row_fingerprint(
    *,
    tenant_id: int | None,
    ts: datetime,
    action: str,
    actor: str | None,
    model: str | None,
    trace_id: str | None,
    payload: dict[str, Any] | None,
    approved_by: str | None,
) -> str:
    """The canonical serialisation of one audit row, before chaining.

    Field order is fixed here and nowhere else. It is not alphabetical and it is not the
    model's declaration order by accident — it is pinned so that a future reordering of
    the ORM columns cannot silently invalidate every hash ever written.
    """
    return "\x1e".join(
        (
            _frame(None if tenant_id is None else str(tenant_id)),
            # Always six fractional digits. See point 2.
            _frame(ts.strftime("%Y-%m-%dT%H:%M:%S.%f")),
            _frame(action),
            _frame(actor),
            _frame(model),
            _frame(trace_id),
            _frame(canonical_payload(payload)),
            _frame(approved_by),
        )
    )


def chain_hash(previous: str | None, fingerprint: str) -> str:
    """``H(prev_hash || row)`` — the link that makes the trail a chain.

    Chaining is what turns per-row hashes into evidence. Row hashes alone prove a row was
    not edited; they say nothing about a row being *deleted*, because the remaining rows
    still verify individually. Including the predecessor's hash means removing any row
    breaks every row after it.

    Args:
        previous: The predecessor's ``row_hash``, or :data:`GENESIS` for the first row.
        fingerprint: This row's canonical serialisation, from :func:`row_fingerprint`.

    Returns:
        Lowercase hex SHA-256.
    """
    seed = previous if previous is not None else GENESIS
    return hashlib.sha256(f"{_frame(seed)}\x1e{fingerprint}".encode()).hexdigest()
