"""The ``SKILL.md`` authoring format: what it accepts, and what it will not.

Two claims worth a test and no more. The **round trip** is the open-standard promise made
checkable — a skill authored in Aegis can be copied out and read back. The **refusals**
are the reason this parses a strict subset by hand instead of calling a YAML library: the
text arrives from a browser and its body is destined for a prompt, so the header is three
strings and not an expression language.
"""

from __future__ import annotations

import pytest

from aegis.skills import (
    MAX_DESCRIPTION_CHARS,
    SkillFormatError,
    parse_skill_md,
    render_skill_md,
)

_DOC = """---
name: refund_policy
description: How this tenant handles refund requests over the standard window.
triggers: [refund, chargeback]
---

# Refunds

- Ask for the order id before quoting anything.
- Over 90 days needs an approval.
"""


def test_a_skill_survives_the_round_trip_out_and_back():
    """Authored here, readable anywhere: parse -> render -> parse is a fixed point."""
    first = parse_skill_md(_DOC)
    second = parse_skill_md(render_skill_md(**vars(first)))
    assert second == first
    assert first.triggers == ("refund", "chargeback")


@pytest.mark.parametrize(
    ("document", "expected"),
    [
        pytest.param("no fence at all", "frontmatter fence", id="no-frontmatter"),
        pytest.param("---\nname: a_skill\n", "never closed", id="unclosed-fence"),
        pytest.param(
            "---\ndescription: d\n---\nbody", "no 'name'", id="missing-name"
        ),
        pytest.param(
            "---\nname: a_skill\n---\nbody", "no 'description'", id="missing-description"
        ),
        pytest.param(
            "---\nname: A Skill\ndescription: d\n---\nbody",
            "not a usable skill name",
            id="name-is-a-title",
        ),
        pytest.param(
            "---\nname: a_skill\ndescription: d\n---\n\n",
            "no body",
            id="header-only",
        ),
        pytest.param(
            "---\nname: a_skill\ndescription: d\nauthor: me\n---\nbody",
            "is not one of",
            id="unknown-key",
        ),
        pytest.param(
            "---\nname: a_skill\ndescription: &anchor d\nx: *anchor\n---\nbody",
            "is not one of",
            id="yaml-anchor",
        ),
    ],
)
def test_a_malformed_skill_is_refused_with_a_reason_naming_the_field(document, expected):
    """Every refusal says which line is wrong; an author refused silently pastes it again."""
    with pytest.raises(SkillFormatError, match=expected):
        parse_skill_md(document)


def test_an_oversized_description_is_refused_because_every_turn_pays_for_it():
    """Tier 1 sits in every system prompt this skill resolves into — so it is bounded."""
    document = (
        f"---\nname: a_skill\ndescription: {'x' * (MAX_DESCRIPTION_CHARS + 1)}\n---\nbody"
    )
    with pytest.raises(SkillFormatError, match="the limit is"):
        parse_skill_md(document)
