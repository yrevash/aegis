"""Schema / format validation rails and a lightweight output content filter.

These are the cheapest, most deterministic layers in the defense-in-depth stack
(``docs/security.md`` §3): before any text reaches the model (input rail) or the
user (output rail) it must be structurally well-formed. Malformed, oversized, or
control-character-laden payloads are a classic vector for downstream
"insecure output handling" (LLM02) and for smuggling instructions past naive
parsers, so we reject them early and cheaply.

All checks are pure functions returning a :class:`FormatCheck`; there is no I/O
and nothing here is domain-specific.
"""

from __future__ import annotations

from aegis.core.types import FormatCheck

#: Maximum accepted length of a user query, in characters. Anything larger is a
#: probable abuse / context-stuffing attempt rather than a genuine question.
MAX_INPUT_CHARS = 8_000

#: Maximum accepted length of a model answer we will hand back downstream.
MAX_OUTPUT_CHARS = 20_000

#: Control characters permitted in free text (tab, newline, carriage return).
_ALLOWED_CONTROL = {"\t", "\n", "\r"}

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


def _has_disallowed_control_chars(text: str) -> bool:
    """Return ``True`` if ``text`` contains control chars other than tab/newline.

    Args:
        text: The text to inspect.

    Returns:
        Whether a disallowed C0/C1 control character is present.
    """
    return any(
        ord(char) < 0x20 and char not in _ALLOWED_CONTROL or ord(char) == 0x7F
        for char in text
    )


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
    if _has_disallowed_control_chars(text):
        return FormatCheck(
            ok=False, reason="Input contains disallowed control characters."
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
    if _has_disallowed_control_chars(text):
        return FormatCheck(
            ok=False, reason="Output contains disallowed control characters."
        )
    return FormatCheck(ok=True, reason="Output format is valid.")


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
