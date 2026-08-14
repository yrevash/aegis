"""Unicode normalisation + confusable folding for the deterministic rails.

Deterministic signature matching is only as good as the text it matches against.
An attacker who cannot say ``ignore all previous instructions`` in plain ASCII can
say it with a zero-width space wedged into the verb, with a Cyrillic ``і`` standing
in for the Latin ``i``, in a fullwidth or mathematical-bold font, or hidden entirely
in the Unicode **Tag block** (U+E0000–U+E007F) — codepoints that render as nothing
at all yet which frontier models read as the ASCII letters they mirror.

This module supplies the two primitives the rails need:

- :func:`fold_for_matching` — a *comparison-only* view of a string: invisible and
  format characters removed, NFKC-normalised, diacritics stripped, whitespace
  collapsed. Case is preserved so a rail may still match case-sensitively.
- :func:`deconfuse` — the same view with confusable (homoglyph) codepoints mapped
  to their ASCII lookalikes.
- :func:`disallowed_invisible_chars` — the reject-list used by the schema rail.

**The folded text is never propagated.** Rails match on it and then hand the
*original* string downstream, so normalisation can never itself become a mutation
vector (and never silently "repairs" hostile input into something that looks safe).

Scope, stated honestly
----------------------
:func:`deconfuse` covers the Cyrillic and Greek homoglyphs in :data:`CONFUSABLES`
plus everything NFKC already folds (fullwidth, mathematical alphanumerics, ligatures,
circled letters, superscripts). It does **not** cover the full Unicode confusables
table (Armenian, Cherokee, Coptic, Deseret and others are absent), and it does
**not** attempt leetspeak (``1gn0re``), which cannot be folded without
false-positiving on ordinary text containing digits. Those evasions fall through to
the model-based classifier layer.
"""

from __future__ import annotations

import re
import unicodedata

__all__ = [
    "CONFUSABLES",
    "deconfuse",
    "disallowed_invisible_chars",
    "fold_for_matching",
    "is_disallowed_invisible",
]

#: Control characters permitted in free text (tab, newline, carriage return).
_ALLOWED_CONTROL = frozenset("\t\n\r")

#: The Unicode Tag block. Every codepoint here renders as *nothing* in every font,
#: yet U+E0020–U+E007F mirror printable ASCII 0x20–0x7F one-for-one and frontier
#: models decode them back to that ASCII. A full instruction can therefore be
#: carried invisibly inside an otherwise innocuous sentence. U+E0000 and
#: U+E0002–U+E001F are unassigned (category ``Cn``) so a category test alone misses
#: them — the range is rejected explicitly.
_TAG_BLOCK_START = 0xE0000
_TAG_BLOCK_END = 0xE007F

#: Unicode general categories rejected wholesale by the schema rail:
#: ``Cf`` format/invisible (zero-width space/joiner, bidi overrides such as
#: U+202E RIGHT-TO-LEFT OVERRIDE, soft hyphen, U+E0001 LANGUAGE TAG), ``Co``
#: private-use, ``Cs`` surrogate. ``Cn`` (unassigned) is deliberately *not* in the
#: set: a Python whose Unicode database predates a newly assigned emoji would
#: otherwise reject legitimate text.
_REJECTED_CATEGORIES = frozenset({"Cf", "Co", "Cs"})

#: Confusable (homoglyph) codepoints mapped to the ASCII letter they imitate.
#: Cyrillic and Greek only — the two scripts that supply a near-complete Latin
#: lookalike alphabet and account for essentially all homoglyph evasion seen in
#: the wild. Applied for *comparison only*, by :func:`deconfuse`.
CONFUSABLES: dict[str, str] = {
    # ── Cyrillic, lowercase ────────────────────────────────────────────────
    "а": "a", "в": "b", "с": "c", "ԁ": "d", "е": "e", "ғ": "f", "һ": "h",
    "і": "i", "ј": "j", "к": "k", "ӏ": "l", "м": "m", "н": "h", "о": "o",
    "р": "p", "ԛ": "q", "ѕ": "s", "т": "t", "ѵ": "v", "ԝ": "w", "х": "x",
    "у": "y", "з": "3", "ч": "4",
    # ── Cyrillic, uppercase ────────────────────────────────────────────────
    "А": "A", "В": "B", "С": "C", "Ԁ": "D", "Е": "E", "Ғ": "F", "Һ": "H",
    "І": "I", "Ј": "J", "К": "K", "Ӏ": "I", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "Ԛ": "Q", "Ѕ": "S", "Т": "T", "Ѵ": "V", "Ԝ": "W", "Х": "X",
    "У": "Y",
    # ── Greek, lowercase ───────────────────────────────────────────────────
    "α": "a", "β": "b", "γ": "y", "ε": "e", "ζ": "z", "η": "n", "ι": "i",
    "κ": "k", "μ": "u", "ν": "v", "ο": "o", "ρ": "p", "ς": "s", "τ": "t",
    "υ": "u", "χ": "x", "ω": "w",
    # ── Greek, uppercase ───────────────────────────────────────────────────
    "Α": "A", "Β": "B", "Ε": "E", "Ζ": "Z", "Η": "H", "Ι": "I", "Κ": "K",
    "Μ": "M", "Ν": "N", "Ο": "O", "Ρ": "P", "Τ": "T", "Υ": "Y", "Χ": "X",
    # ── Latin lookalikes NFKC leaves alone ─────────────────────────────────
    "ı": "i", "ȷ": "j", "ɩ": "i", "ɡ": "g", "ɑ": "a", "ѐ": "e", "ø": "o",
    "ł": "l", "đ": "d",
}

_WHITESPACE_RUN = re.compile(r"\s+")


def is_disallowed_invisible(char: str) -> bool:
    """Return whether ``char`` is a control / invisible codepoint we refuse to accept.

    Rejected: C0 controls other than tab/newline/carriage-return, DEL, the C1 block,
    every ``Cf`` / ``Co`` / ``Cs`` codepoint, and the whole Unicode Tag block.

    Args:
        char: A single-character string.

    Returns:
        ``True`` when the character must be rejected by the schema rail.
    """
    if char in _ALLOWED_CONTROL:
        return False
    code = ord(char)
    if code < 0x20 or code == 0x7F:  # C0 controls + DEL
        return True
    if 0x80 <= code <= 0x9F:  # C1 controls
        return True
    if _TAG_BLOCK_START <= code <= _TAG_BLOCK_END:  # invisible ASCII mirror
        return True
    return unicodedata.category(char) in _REJECTED_CATEGORIES


def disallowed_invisible_chars(text: str) -> list[str]:
    """Return the distinct disallowed codepoints in ``text`` as ``U+XXXX`` labels.

    The labels (never the surrounding text) are what a rail puts in its rejection
    reason, so an operator can see *which* invisible character tripped the rail
    without the reason echoing user content.

    Args:
        text: The text to inspect.

    Returns:
        Distinct ``U+XXXX`` labels in first-seen order, capped at eight.
    """
    seen: list[str] = []
    for char in text:
        if is_disallowed_invisible(char):
            label = f"U+{ord(char):04X}"
            if label not in seen:
                seen.append(label)
                if len(seen) == 8:
                    break
    return seen


def _strip_invisible(text: str) -> str:
    """Drop every disallowed invisible codepoint (comparison view only)."""
    return "".join(char for char in text if not is_disallowed_invisible(char))


def fold_for_matching(text: str) -> str:
    """Return a comparison-only view of ``text`` for deterministic signature matching.

    Pipeline: strip invisible/format characters (so ``ig<ZWSP>nore`` becomes
    ``ignore``) → NFKC (so fullwidth ``ｉｇｎｏｒｅ`` and mathematical-bold
    ``𝐢𝐠𝐧𝐨𝐫𝐞`` become ASCII) → strip combining marks (so ``ignoré`` becomes
    ``ignore``) → collapse whitespace runs to a single space. **Case is preserved**
    so a rail may still match case-sensitively.

    Args:
        text: The original text.

    Returns:
        The folded comparison text. Never fed downstream — matching only.
    """
    folded = unicodedata.normalize("NFKC", _strip_invisible(text))
    decomposed = unicodedata.normalize("NFD", folded)
    without_marks = "".join(c for c in decomposed if not unicodedata.combining(c))
    return _WHITESPACE_RUN.sub(" ", without_marks).strip()


def deconfuse(text: str) -> str:
    """Return :func:`fold_for_matching` with homoglyphs mapped to ASCII lookalikes.

    Use *in addition to* the plain fold, never instead of it: the mapping rewrites
    genuine Cyrillic and Greek prose into Latin gibberish, so a rail that matches a
    non-Latin-script signature must match against the plain fold as well.

    Args:
        text: The original text.

    Returns:
        The folded, homoglyph-normalised comparison text.
    """
    return "".join(CONFUSABLES.get(c, c) for c in fold_for_matching(text))
