"""The ``SKILL.md`` authoring format — parsed in, rendered out.

Skills are authored in the **``SKILL.md`` open standard** (agentskills.io): a
frontmatter block naming the skill and describing when it applies, then the Markdown
body that is the skill itself. Adopting it rather than inventing a shape means a skill
written for any of the standard's other implementations can be pasted into Aegis, and
one written here can be pasted back out — :func:`render_skill_md` is the other half of
that promise, and the round trip is asserted rather than assumed.

**Why this parses a strict subset by hand instead of calling a YAML library.** Two
reasons, and the second is the one that matters. The small one: ``aegis`` declares its
dependencies narrowly and a three-key header does not earn a new one. The load-bearing
one: this text arrives from a browser, from a tenant's user, and its body is destined
for a prompt. A full YAML parser on that path accepts anchors, aliases, merge keys,
tags and multi-document streams — an expression language, in a field whose entire job
is to carry three strings. The subset here accepts ``key: value``, an inline
``[a, b]`` list and a ``- item`` block list, and refuses everything else by name. A
parser that cannot express a billion laughs cannot be asked to.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = [
    "SKILL_NAME_PATTERN",
    "MAX_BODY_CHARS",
    "MAX_DESCRIPTION_CHARS",
    "SkillDocument",
    "SkillFormatError",
    "parse_skill_md",
    "render_skill_md",
]

#: The fence a ``SKILL.md`` frontmatter block opens and closes with.
_FENCE = "---"

#: Slug-shaped, because the name is an identifier the model puts in a tool argument and
#: a unique key in three partial indexes — not a title. Bounded to the column's width.
SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

#: Matches ``AgentSkill.description``'s column. The description is the whole of tier 1:
#: it sits in **every** system prompt for every user the skill resolves for, so its
#: length is a per-turn context cost multiplied by the number of skills in force.
MAX_DESCRIPTION_CHARS = 280

#: The body is loaded on demand, so it may be long — but not unbounded: it is returned
#: as a tool result and re-enters the prompt whole once loaded.
MAX_BODY_CHARS = 20_000

#: The keys the frontmatter may carry. Closed, so a typo is a refusal with a suggestion
#: rather than a silently dropped field.
_KNOWN_KEYS = frozenset({"name", "description", "triggers"})


class SkillFormatError(ValueError):
    """The text is not a well-formed ``SKILL.md``, with a sentence saying what is wrong.

    A ``ValueError`` so an authoring route can map every malformed document to one 422,
    and always with a reason: an author who is refused without being told which line is
    wrong will paste the same document again.
    """


@dataclass(frozen=True)
class SkillDocument:
    """One parsed ``SKILL.md``: the frontmatter fields plus the body.

    Attributes:
        name: The slug the model calls ``load_skill`` with.
        description: The one sentence that lives in the system prompt.
        body: The Markdown the tool call returns.
        triggers: Terms that hint when the skill applies. May be empty.
    """

    name: str
    description: str
    body: str
    triggers: tuple[str, ...] = field(default=())


def _split_frontmatter(text: str) -> tuple[list[str], str]:
    """Split ``text`` into its frontmatter lines and its body.

    Args:
        text: The whole ``SKILL.md`` document.

    Returns:
        ``(frontmatter_lines, body)``.

    Raises:
        SkillFormatError: If the document does not open with a fence, or never closes
            one.
    """
    lines = text.replace("\r\n", "\n").split("\n")
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != _FENCE:
        raise SkillFormatError(
            "A SKILL.md opens with a '---' frontmatter fence carrying at least 'name' "
            "and 'description'. Nothing before it, and the fence on its own line."
        )
    for index in range(start + 1, len(lines)):
        if lines[index].strip() == _FENCE:
            return lines[start + 1 : index], "\n".join(lines[index + 1 :]).strip()
    raise SkillFormatError(
        "The SKILL.md frontmatter was opened with '---' and never closed with a second "
        "'---', so the whole document was read as the header and there is no body."
    )


def _inline_list(raw: str) -> tuple[str, ...]:
    """Parse an inline ``[a, b, c]`` list into a tuple of trimmed members."""
    inner = raw[1:-1].strip()
    if not inner:
        return ()
    return tuple(
        member.strip().strip("'\"") for member in inner.split(",") if member.strip()
    )


def _parse_fields(lines: list[str]) -> dict[str, object]:
    """Parse the frontmatter lines into a mapping.

    Args:
        lines: The lines between the two fences.

    Returns:
        The parsed fields.

    Raises:
        SkillFormatError: On an unknown key, a line that is neither ``key: value`` nor a
            ``- item`` continuation, or a construct outside the accepted subset.
    """
    fields: dict[str, object] = {}
    pending_list: str | None = None
    for number, line in enumerate(lines, start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            if pending_list is None:
                raise SkillFormatError(
                    f"Frontmatter line {number} ({stripped!r}) is a list item, but no "
                    "key opened a list above it."
                )
            member = stripped[2:].strip().strip("'\"")
            if member:
                fields[pending_list] = (*fields.get(pending_list, ()), member)  # type: ignore[arg-type]
            continue
        if ":" not in stripped:
            raise SkillFormatError(
                f"Frontmatter line {number} ({stripped!r}) is not 'key: value'. This "
                "header accepts plain scalars and lists only — no anchors, no tags, no "
                "nested mappings."
            )
        key, _, raw = stripped.partition(":")
        key = key.strip()
        raw = raw.strip()
        if key not in _KNOWN_KEYS:
            raise SkillFormatError(
                f"Frontmatter key {key!r} is not one of {sorted(_KNOWN_KEYS)}. A key "
                "that is silently ignored is how a skill ships with a trigger nobody "
                "notices was never read."
            )
        if raw.startswith("[") and raw.endswith("]"):
            fields[key] = _inline_list(raw)
            pending_list = None
        elif not raw:
            fields[key] = ()
            pending_list = key
        else:
            fields[key] = raw.strip("'\"")
            pending_list = None
    return fields


def parse_skill_md(text: str) -> SkillDocument:
    """Parse a ``SKILL.md`` document, or refuse with the reason.

    Args:
        text: The whole document, frontmatter included.

    Returns:
        The parsed :class:`SkillDocument`.

    Raises:
        SkillFormatError: If the frontmatter is malformed, a required field is missing,
            the name is not slug-shaped, or a field is over its bound. Every refusal
            names the field.
    """
    header, body = _split_frontmatter(text)
    fields = _parse_fields(header)

    name = fields.get("name")
    if not isinstance(name, str) or not name:
        raise SkillFormatError(
            "The frontmatter has no 'name'. The name is what the model passes to "
            "load_skill, so a skill without one cannot be loaded."
        )
    if not SKILL_NAME_PATTERN.match(name):
        raise SkillFormatError(
            f"{name!r} is not a usable skill name. Use lowercase letters, digits, '-' "
            "and '_', 2-64 characters, starting with a letter or digit — it is an "
            "identifier in a tool call, not a title."
        )

    description = fields.get("description")
    if not isinstance(description, str) or not description.strip():
        raise SkillFormatError(
            "The frontmatter has no 'description'. The description is the ONLY part of "
            "a skill that is always in the system prompt, so a skill without one can "
            "never be chosen: the model would have nothing to choose it on."
        )
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise SkillFormatError(
            f"The description is {len(description)} characters; the limit is "
            f"{MAX_DESCRIPTION_CHARS}. It is carried in every system prompt this skill "
            "resolves into, so its length is a cost paid on every turn."
        )

    if not body.strip():
        raise SkillFormatError(
            "The SKILL.md has no body below the frontmatter. The body is the skill; "
            "without it load_skill would return a description the prompt already had."
        )
    if len(body) > MAX_BODY_CHARS:
        raise SkillFormatError(
            f"The body is {len(body)} characters; the limit is {MAX_BODY_CHARS}. A body "
            "is returned whole as a tool result and re-enters the prompt whole."
        )

    triggers = fields.get("triggers", ())
    if isinstance(triggers, str):
        triggers = (triggers,)
    return SkillDocument(
        name=name,
        description=description.strip(),
        body=body,
        triggers=tuple(dict.fromkeys(t.lower() for t in triggers if t)),  # type: ignore[union-attr]
    )


def render_skill_md(
    *, name: str, description: str, body: str, triggers: object = ()
) -> str:
    """Render a stored skill back to a ``SKILL.md`` document.

    The other half of the open-standard promise: a skill authored in Aegis can be
    copied out and used elsewhere, and the round trip through :func:`parse_skill_md` is
    asserted in the tests rather than hoped for.

    Args:
        name: The skill's slug.
        description: The one-sentence description.
        body: The Markdown body.
        triggers: The trigger terms (any iterable of strings).

    Returns:
        The document, frontmatter first.
    """
    terms = ", ".join(str(t) for t in (triggers or ()))
    header = [_FENCE, f"name: {name}", f"description: {description}"]
    if terms:
        header.append(f"triggers: [{terms}]")
    header.append(_FENCE)
    return "\n".join(header) + "\n\n" + body.strip() + "\n"
