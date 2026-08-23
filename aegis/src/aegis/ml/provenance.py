"""Training-data provenance for the ML spine: a content digest of the fitted frame.

**What this is.** A stable SHA-256 over the *content* of the training frame,
computed at fit time and carried on the fitted model and its
:class:`~aegis.ml.types.ModelCard`. It answers one question that had no answer
before: *which data produced this model?* Two spines fitted from different frames
were previously indistinguishable after the fact — same members, same weights,
same split sizes, nothing on the card that moved when the data moved.

**What this is not.** This is **tamper-evidence and provenance, not
tamper-prevention.** Nothing here inspects, screens, or refuses a training frame.
A caller who supplies a poisoned frame gets a fitted model exactly as before; what
changes is that the poisoned frame now leaves a fingerprint on the artifact, so
the poisoning is *attributable* (this model, that data) and *detectable* (rehash
the frame you believe was used; a mismatch proves it was not). Detection is
after-the-fact and requires a reference digest someone recorded when the data was
still trusted. Prevention of poisoned corpus writes is a different control and
lives in :mod:`aegis.retrieval.validation`.

Determinism — what the digest *does* and *does not* move on
-----------------------------------------------------------
The rule is that the digest is invariant to exactly the transformations the *fit*
is invariant to, and to nothing else. Concretely:

**The digest is the SAME when:**

* **Columns are reordered.** Column names are sorted before hashing. The spine
  selects its columns by name (``data[spec.features]``), so column order cannot
  change the fitted model, and a digest that moved on it would be noise.
* **The index changes.** The index is not hashed. ``train_test_split`` partitions
  positionally, so index *labels* never reach the estimator; a ``reset_index()``
  or a re-read that renumbers rows is not a change in data.
* **The same frame is digested twice, in another process, on another machine.**
  The encoding is byte-level and endian-explicit — no ``repr``, no float
  formatting, no locale, no hash randomisation, no dict iteration order.

**The digest is DIFFERENT when:**

* **Any cell value changes** — including a single flipped label, which is the
  whole point.
* **Rows are reordered.** The fit is *not* row-order invariant (the train /
  calibration / test partition is positional), so a reordered frame produces a
  different model and must therefore produce a different digest. Making the
  digest order-insensitive would let it claim two genuinely different models came
  from the same data.
* **A column is added, removed or renamed**, within the digested projection.
* **A column's dtype changes**, even with equal values (``int64`` → ``float64``,
  a numeric column read back as strings). The declared dtype is part of what the
  preprocessor sees.
* **NaN appears or disappears.** The null mask is hashed separately from the
  values, so a NaN and a 0.0 in the same cell are never confused.

**The one honest hazard.** Columns that are neither bool, integer nor float are
digested through ``str(value)``. For strings, dates and categories that is stable.
For a column of arbitrary Python objects relying on the default ``__repr__``
(``<Foo object at 0x10a3b2f90>``) it is *not* — the address changes per process,
so the digest would change when nothing did. Training frames hold scalars in
practice; a frame that does not is outside what this can honestly fingerprint.
"""

from __future__ import annotations

import hashlib
import struct
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DIGEST_PREFIX", "DIGEST_VERSION", "frame_digest"]

DIGEST_VERSION = "aegis-frame-digest/1"
"""Version tag mixed into every digest.

Hashed first, so a future change to the encoding produces wholesale different
digests rather than silently colliding with the old scheme's output.
"""

DIGEST_PREFIX = "sha256:"
"""Algorithm prefix on the returned string — the digest names its own algorithm."""

#: Field separators. Distinct, non-UTF-8-representable byte markers so a column
#: named ``"a\x00dtype\x00b"`` cannot forge a field boundary.
_COL = b"\xff\x01col"
_DTYPE = b"\xff\x02dtype"
_NULLS = b"\xff\x03nulls"
_VALUES = b"\xff\x04values"


def _numeric_bytes(column: pd.Series) -> bytes | None:
    """Return byte-exact IEEE-754 / two's-complement bytes, or ``None`` if not numeric.

    Nulls are filled with a fixed sentinel before conversion; the null *mask* is
    hashed separately by :func:`frame_digest`, so the sentinel is unambiguous and
    a filled null can never be read back as a real zero.

    Negative zero is canonicalised to positive zero: ``-0.0`` and ``0.0`` compare
    equal and train identically, so a digest that separated them would move when
    the data did not.

    Args:
        column: The column to encode.

    Returns:
        The column's raw little-endian value bytes, or ``None`` when the dtype is
        not one of bool / integer / float (or a conversion overflowed), in which
        case the caller falls back to the string encoding.
    """
    dtype = column.dtype
    try:
        if pd.api.types.is_bool_dtype(dtype):
            return np.ascontiguousarray(
                column.fillna(value=False).astype("bool").to_numpy(dtype=np.bool_)
            ).tobytes()
        if pd.api.types.is_integer_dtype(dtype):
            return np.ascontiguousarray(
                column.fillna(value=0).astype("int64").to_numpy(dtype=np.int64)
            ).astype("<i8").tobytes()
        if pd.api.types.is_float_dtype(dtype):
            values = np.ascontiguousarray(
                column.fillna(value=0.0).astype("float64").to_numpy(dtype=np.float64)
            ).astype("<f8")
            # -0.0 == 0.0 but their bit patterns differ; pick one.
            values = np.where(values == 0.0, 0.0, values)
            return values.tobytes()
    except (TypeError, ValueError, OverflowError):
        return None
    return None


def _text_bytes(column: pd.Series) -> bytes:
    """Encode any other column as length-prefixed UTF-8 of each value's ``str``.

    The length prefix is what makes the encoding injective: without it the pair
    ``("ab", "c")`` and ``("a", "bc")`` would hash identically. Nulls encode as a
    zero-length value, which is unambiguous because the null mask is hashed too.

    Args:
        column: The column to encode.

    Returns:
        The concatenated length-prefixed UTF-8 encoding of the column's values.
    """
    out = bytearray()
    null = column.isna().to_numpy(dtype=bool)
    for position, value in enumerate(column.to_numpy(dtype=object)):
        raw = b"" if null[position] else str(value).encode("utf-8")
        out += struct.pack("<Q", len(raw))
        out += raw
    return bytes(out)


def frame_digest(
    frame: pd.DataFrame, *, columns: Sequence[str] | None = None
) -> str:
    """Return a stable content digest of ``frame`` as ``"sha256:<hex>"``.

    See the module docstring for the exact determinism contract — what the digest
    moves on and what it deliberately does not — and for the plain statement that
    this is provenance and tamper-*evidence*, never tamper-*prevention*.

    Args:
        frame: The training frame to fingerprint.
        columns: Restrict the digest to these columns, in any order (they are
            sorted). Use it to fingerprint exactly the columns the model consumed,
            so an unrelated extra column in the source frame does not change the
            identity of data that produced an identical model. ``None`` digests
            every column.

    Returns:
        ``"sha256:"`` followed by 64 lowercase hex characters.

    Raises:
        KeyError: If ``columns`` names a column the frame does not have — a
            silently-skipped column would mean the digest covered less than it
            claims to.
    """
    if columns is None:
        selected = list(frame.columns)
    else:
        missing = [c for c in columns if c not in frame.columns]
        if missing:
            msg = f"Cannot digest columns absent from the frame: {missing}"
            raise KeyError(msg)
        selected = list(columns)

    digest = hashlib.sha256()
    digest.update(DIGEST_VERSION.encode("utf-8"))
    digest.update(struct.pack("<QQ", len(frame.index), len(selected)))

    # Sorted by name: column *order* cannot change the fitted model, so it must
    # not change the digest either. Row order is deliberately preserved.
    for name in sorted(selected, key=str):
        column = frame[name]
        digest.update(_COL)
        digest.update(str(name).encode("utf-8"))
        digest.update(_DTYPE)
        digest.update(str(column.dtype).encode("utf-8"))
        digest.update(_NULLS)
        digest.update(column.isna().to_numpy(dtype=bool).tobytes())
        digest.update(_VALUES)
        digest.update(_numeric_bytes(column) or _text_bytes(column))

    return f"{DIGEST_PREFIX}{digest.hexdigest()}"
