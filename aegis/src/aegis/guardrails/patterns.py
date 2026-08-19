"""The vetted pattern library — the only regexes a tenant may point a rail at.

**Why a library and not a text box.** §7.6 offers a tenant four rail *templates* —
``contains_term``, ``matches_pattern``, ``max_length`` and ``requires_citation`` — and is
explicit that ``matches_pattern`` draws from *"a vetted pattern library"* and is **not**
free-form regex. The reason is not tidiness: a regex a tenant types is executed by this
process, on the request path, against attacker-influenced text. ``(a+)+$`` against a
sixty-character string is a wedged worker, so a free-form pattern box is a
denial-of-service control handed to the least-trusted writer in the system, dressed as a
guardrail. Making it safe needs a timeout, a complexity bound and a sandbox, which is a
different project; a closed library needs none of them because the platform wrote every
pattern here and can reason about all of them at once.

So the tenant-facing value is an **id**, not a pattern. ``guardrails.denylist.patterns``
declares its legal members from :func:`pattern_ids`, so an id nobody vetted is refused by
:meth:`aegis.settings.spec.SettingSpec.validate` on write — the same check every setting
goes through — rather than by a screen that could be walked around with a ``curl``.

**What is in here, and what is deliberately not.** These are *secret and identifier*
shapes: credentials and tokens that must never be typed into a prompt, pasted back by a
model, or carried out of a tool result. They are not PII — :mod:`aegis.guardrails.pii`
owns that, backed by a real detection engine, and duplicating it with regexes would give
two answers to one question. Every pattern here is linear-time by construction: no nested
quantifier, no alternation under a quantifier, and a bounded repetition wherever a run of
characters is matched, so the screen costs the same on hostile input as on friendly input.

Deliberately case-sensitive where the real token is (``AKIA``, ``xoxb-``, ``eyJ``) and
case-insensitive only where the surrounding prose is (the internal-hostname suffixes).
A pattern that fires on prose is worse than no pattern, because a tenant turns it off and
never turns another one on.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "PATTERN_LIBRARY",
    "PatternSpec",
    "matched_pattern",
    "pattern_for",
    "pattern_ids",
]


@dataclass(frozen=True, slots=True)
class PatternSpec:
    """One vetted pattern a tenant may switch on by id.

    Attributes:
        id: The stable identifier a tenant writes into
            ``guardrails.denylist.patterns``. Never the pattern itself.
        label: Short human name, rendered on the guardrails screen.
        description: What it catches and why, rendered as the control's help text.
            Required for the same reason a :class:`~aegis.settings.spec.SettingSpec`
            needs one: an unexplained rail is one nobody dares switch on.
        pattern: The compiled expression. Linear-time by review, not by hope.
    """

    id: str
    label: str
    description: str
    pattern: re.Pattern[str]


#: **The closed set.** Adding a member is a code change and a review, which is the point:
#: the safety property of this module is that a human read every expression in it.
PATTERN_LIBRARY: tuple[PatternSpec, ...] = (
    PatternSpec(
        id="aws_access_key_id",
        label="AWS access key id",
        description=(
            "An AWS access key id (AKIA/ASIA followed by 16 upper-case alphanumerics). "
            "Pasted into a prompt it is a live credential in a transcript, a log line "
            "and every downstream trace."
        ),
        pattern=re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[0-9A-Z]{16}\b"),
    ),
    PatternSpec(
        id="private_key_block",
        label="Private key block",
        description=(
            "The opening line of a PEM private key block. The single highest-value "
            "secret shape there is, and it is unambiguous — nothing else says "
            "BEGIN PRIVATE KEY."
        ),
        pattern=re.compile(
            r"-----BEGIN (?:RSA |DSA |EC |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
        ),
    ),
    PatternSpec(
        id="jwt",
        label="JSON Web Token",
        description=(
            "A three-part JWT (a base64url header beginning eyJ, a payload and a "
            "signature). Usually a live session or service token, and it carries its "
            "own claims out with it."
        ),
        pattern=re.compile(
            r"\beyJ[A-Za-z0-9_-]{8,512}\.[A-Za-z0-9_-]{8,2048}\.[A-Za-z0-9_-]{8,512}\b"
        ),
    ),
    PatternSpec(
        id="slack_token",
        label="Slack token",
        description=(
            "A Slack bot, user, app or refresh token (xoxb-, xoxp-, xoxa-, xoxr-, "
            "xoxs-). Distinctive enough that a match is a match."
        ),
        pattern=re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,256}\b"),
    ),
    PatternSpec(
        id="github_token",
        label="GitHub token",
        description=(
            "A GitHub personal-access, OAuth, app or refresh token (ghp_, gho_, ghu_, "
            "ghs_, ghr_, github_pat_). Repository access in a single string."
        ),
        pattern=re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,255}|github_pat_[A-Za-z0-9_]{20,255})\b"),
    ),
    PatternSpec(
        id="iban",
        label="IBAN",
        description=(
            "An International Bank Account Number. Screened as a *pattern* rather than "
            "as PII because for most tenants it is a payment instrument on the "
            "outbound path, not a person's identifier on the inbound one."
        ),
        pattern=re.compile(r"\b[A-Z]{2}[0-9]{2}[A-Z0-9]{11,30}\b"),
    ),
    PatternSpec(
        id="internal_hostname",
        label="Internal hostname",
        description=(
            "A hostname on a private suffix (.internal, .intranet, .corp, .lan, "
            ".local). Names infrastructure that is not supposed to be discoverable, "
            "and is the reconnaissance half of most real exfiltration."
        ),
        pattern=re.compile(
            r"\b[a-z0-9][a-z0-9-]{0,62}(?:\.[a-z0-9][a-z0-9-]{0,62}){0,8}"
            r"\.(?:internal|intranet|corp|lan|local)\b",
            re.IGNORECASE,
        ),
    ),
)

_BY_ID: dict[str, PatternSpec] = {spec.id: spec for spec in PATTERN_LIBRARY}
if len(_BY_ID) != len(PATTERN_LIBRARY):
    raise ValueError("the vetted pattern library declares an id twice")


def pattern_ids() -> tuple[str, ...]:
    """Return every vetted pattern id, in declaration order.

    The catalogue reads its legal members from here rather than restating them, so the
    ids a screen offers and the ids a write is validated against are one set — the same
    discipline ``agent.model`` keeps against the fleet declaration.
    """
    return tuple(spec.id for spec in PATTERN_LIBRARY)


def pattern_for(pattern_id: str) -> PatternSpec:
    """Return the vetted pattern registered under ``pattern_id``.

    Args:
        pattern_id: The id a tenant wrote.

    Returns:
        Its :class:`PatternSpec`.

    Raises:
        KeyError: If no such pattern is vetted. Never a permissive default: an
            unrecognised id means the catalogue and this library have drifted, and
            screening nothing while reporting a configured rail is the failure this
            whole module exists to make impossible.
    """
    try:
        return _BY_ID[pattern_id]
    except KeyError:
        raise KeyError(
            f"{pattern_id!r} is not a vetted guardrail pattern; the library declares "
            f"{list(_BY_ID)}"
        ) from None


def matched_pattern(text: str, pattern_ids_: object) -> PatternSpec | None:
    """Return the first vetted pattern present in ``text``, or ``None``.

    Args:
        text: The text to screen.
        pattern_ids_: The tenant's resolved pattern ids (already UNION-merged across
            the platform, tenant and user scopes). Empty disables the rail.

    Returns:
        The :class:`PatternSpec` that fired, so the caller can name it in the verdict —
        a console must never show an anonymous block — or ``None`` when nothing matched.

    Raises:
        KeyError: If an id is not in the library. **Deliberately not swallowed.** A
            stored id the library no longer vets means a rail the tenant believes is
            screening their traffic is screening nothing, and the caller
            (:func:`aegis.guardrails.schema.denied_pattern`) turns it into a
            fail-closed BLOCK rather than a silent pass.
    """
    if not pattern_ids_:
        return None
    for pattern_id in pattern_ids_:  # type: ignore[union-attr]
        if not isinstance(pattern_id, str) or not pattern_id.strip():
            continue
        spec = pattern_for(pattern_id.strip())
        if spec.pattern.search(text):
            return spec
    return None
