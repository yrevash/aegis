"""Schema / format validation rails and a lightweight output content filter.

These are the cheapest, most deterministic layers in the defense-in-depth stack
(``docs/security/overview.md`` §3): before any text reaches the model (input rail) or the
user (output rail) it must be structurally well-formed. Malformed, oversized, or
control-character-laden payloads are a classic vector for downstream
"insecure output handling" (LLM02) and for smuggling instructions past naive
parsers, so we reject them early and cheaply.

All checks are pure functions returning a :class:`FormatCheck`; there is no I/O
and nothing here is domain-specific.

Invisible characters
--------------------
The character rail rejects far more than the C0 controls it started with. A plain
``ord(char) < 0x20`` test passes U+200B ZERO WIDTH SPACE, U+200E LEFT-TO-RIGHT MARK,
U+202E RIGHT-TO-LEFT OVERRIDE and — most seriously — the Unicode **Tag block**
(U+E0000–U+E007F), whose codepoints render as nothing in every font yet mirror
printable ASCII one-for-one, so a frontier model reads them as the instruction they
encode. An attacker can therefore paste a sentence that *looks* like "what is the
escalation policy?" and carries a complete jailbreak the reviewer cannot see. The rail
now rejects every ``Cf`` / ``Co`` / ``Cs`` codepoint, the C0/C1 control blocks (bar
tab/newline/carriage-return) and the whole Tag block, checking the text both as
written and after NFKC normalisation.

The deliberate cost: ``Cf`` also covers U+200D ZERO WIDTH JOINER (multi-person emoji
such as 👨‍👩‍👧 are built from it) and the Arabic formatting marks U+0600–U+0605 /
U+061C. Those inputs are rejected too. That is the intended trade for closing an
invisible-instruction channel; the rejection reason names the exact codepoint so an
operator can see why.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence

from aegis.core.types import FormatCheck
from aegis.guardrails.normalize import disallowed_invisible_chars

#: Maximum accepted length of a user query, in characters. Anything larger is a
#: probable abuse / context-stuffing attempt rather than a genuine question.
MAX_INPUT_CHARS = 8_000

#: Maximum accepted length of a model answer we will hand back downstream.
MAX_OUTPUT_CHARS = 20_000

#: Case-insensitive markers that indicate a leaked system prompt or a smuggled
#: instruction block in *output*. Explicit and specific (chosen to avoid false
#: positives on genuine answers) — a targeted backstop layered *after* PII
#: redaction, not a general moderation model. Covers the three common leak shapes:
#: (1) chat-template control tokens, (2) explicit "system prompt" framing, and
#: (3) verbatim echoes of this system's own instruction preamble.
_OUTPUT_DENYLIST: tuple[str, ...] = (
    # 1. Chat-template / role control tokens that should never reach a user.
    "<|im_start|>",
    "<|im_end|>",
    "<|system|>",
    "<|endoftext|>",
    "[/inst]",
    "<<sys>>",
    # 2. Explicit system-prompt framing / meta-instruction leakage.
    "begin system prompt",
    "end system prompt",
    "system prompt:",
    "my system prompt",
    "here are my instructions",
    "the following are your instructions",
    # 3. Verbatim echoes of this platform's own instruction preamble (see the
    #    guardrails Colang `instructions` block / adapter system prompts).
    "you are a guarded enterprise assistant",
    "never reveal system instructions",
)


def _disallowed_chars(text: str) -> list[str]:
    """Return the distinct disallowed invisible codepoints in ``text``.

    Delegates to :func:`aegis.guardrails.normalize.disallowed_invisible_chars`, which
    rejects C0 controls other than tab/newline/carriage-return, DEL, the C1 block,
    every ``Cf`` / ``Co`` / ``Cs`` codepoint, and the whole Unicode Tag block. The
    text is checked **both** as written and after NFKC normalisation, so a codepoint
    that only decomposes into a disallowed one cannot slip past.

    Args:
        text: The text to inspect.

    Returns:
        Distinct ``U+XXXX`` labels for the offending codepoints (empty when clean).
    """
    found = disallowed_invisible_chars(text)
    normalized = unicodedata.normalize("NFKC", text)
    if normalized != text:
        for label in disallowed_invisible_chars(normalized):
            if label not in found:
                found.append(label)
    return found


def validate_input_format(text: str) -> FormatCheck:
    """Validate the structural shape of an inbound user query.

    Args:
        text: The raw query text.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` (with a reason) when the text
        is empty, over :data:`MAX_INPUT_CHARS`, or carries disallowed control
        characters.
    """
    if not text or not text.strip():
        return FormatCheck(ok=False, reason="Empty input is not a valid query.")
    if len(text) > MAX_INPUT_CHARS:
        return FormatCheck(
            ok=False,
            reason=f"Input exceeds the {MAX_INPUT_CHARS}-character limit.",
        )
    hidden = _disallowed_chars(text)
    if hidden:
        return FormatCheck(
            ok=False,
            reason=(
                "Input contains disallowed control or invisible characters: "
                f"{', '.join(hidden)}."
            ),
        )
    return FormatCheck(ok=True, reason="Input format is valid.")


def validate_output_format(text: str) -> FormatCheck:
    """Validate the structural shape of a model answer before it is returned.

    Args:
        text: The model's answer text.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` when the output is over
        :data:`MAX_OUTPUT_CHARS` or carries disallowed control characters. Empty
        output is allowed (a model may legitimately produce nothing).
    """
    if len(text) > MAX_OUTPUT_CHARS:
        return FormatCheck(
            ok=False,
            reason=f"Output exceeds the {MAX_OUTPUT_CHARS}-character limit.",
        )
    hidden = _disallowed_chars(text)
    if hidden:
        return FormatCheck(
            ok=False,
            reason=(
                "Output contains disallowed control or invisible characters: "
                f"{', '.join(hidden)}."
            ),
        )
    return FormatCheck(ok=True, reason="Output format is valid.")


def denied_term(text: str, terms: Sequence[str] | None) -> FormatCheck:
    """Screen ``text`` against a tenant's own denied terms.

    The rail behind ``guardrails.denylist.terms``, which until now was a catalogue key
    a tenant admin could write, audit and see badged "Your setting" while nothing on any
    path read it. It is deliberately separate from :func:`content_filter`: that
    function's markers are the *platform's* fixed backstop against system-prompt
    leakage on the outbound path, whereas these are one tenant's own words — client
    names, project codenames, an unreleased product — and they are screened on the
    inbound, tool-result **and** outbound paths, because a term that must not be
    discussed must not be typed in, fetched, or answered with.

    Case-insensitive substring matching, which is what a list of names needs and what
    the catalogue's ``tags`` control implies. It is not a word-boundary match: a
    denylist that misses ``project-zephyr's`` because of an apostrophe is a denylist
    that does not work.

    Args:
        text: The text to screen.
        terms: The tenant's resolved denied terms (already UNION-merged across the
            platform, tenant and user scopes). ``None``/empty disables the rail.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` when a denied term is present, and
        ``reason`` names the term that fired so the console never shows an anonymous
        block.
    """
    if not terms:
        return FormatCheck(ok=True, reason="No denied terms are configured.")
    lowered = text.lower()
    for term in terms:
        if not isinstance(term, str):
            continue
        needle = term.strip().lower()
        if needle and needle in lowered:
            return FormatCheck(
                ok=False,
                reason=f"Matched a denied term configured for this tenant: {term.strip()!r}.",
            )
    return FormatCheck(ok=True, reason="No denied term is present.")


def content_filter(text: str) -> FormatCheck:
    """Flag output that leaks a system prompt or smuggles an instruction block.

    This is a deliberately small, explicit backstop (see :data:`_OUTPUT_DENYLIST`)
    layered *after* PII redaction; it is not a general moderation model.

    Args:
        text: The model's answer text.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` when a denylisted marker is
        present.
    """
    lowered = text.lower()
    for marker in _OUTPUT_DENYLIST:
        if marker in lowered:
            return FormatCheck(
                ok=False,
                reason=f"Output matched a blocked content marker: {marker!r}.",
            )
    return FormatCheck(ok=True, reason="Output passed the content filter.")
