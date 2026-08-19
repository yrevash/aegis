"""The failure message, which is half of what this suite is for.

A conformance failure is read by someone integrating Aegis against a deadline, and
possibly on a projector. ``AssertionError: False`` costs them the rest of the afternoon,
so every failure here is rendered in the same four-part shape as the two "silent seam"
messages: **what** is wrong and which piece owns it, **fix** — the edit to make, **if
not** — the consequence of leaving it (always some flavour of "it will look like it
works"), and **scar** — the defect this repository actually shipped that the check
descends from.

The scar line is not decoration either. It is the difference between a rule someone
invented and a rule something taught us, and it is what stops this suite growing a
fourteenth plausible-sounding check.
"""

from __future__ import annotations

import textwrap
from typing import NoReturn

import pytest

__all__ = ["PIECES", "fail", "piece_label"]

#: Adapter member → its number in the ten-piece table (:mod:`aegis.adapter`).
PIECES: dict[str, str] = {
    "schema": "piece 1",
    "ml_spec": "piece 2",
    "generator": "piece 3",
    "tools": "piece 4",
    "personas": "piece 5",
    "prompts": "piece 6",
    "memory_spec": "piece 7",
    "roster": "piece 8",
    "corpus": "piece 9",
    "skills": "piece 10",
}

_WIDTH = 88
_LABEL_WIDTH = 7


def piece_label(member: str) -> str:
    """Return the human header for an adapter member (``"piece 8 · roster"``).

    Args:
        member: The adapter member name, or any other owner label (e.g. ``"identity"``)
            for a check that is not about one of the ten pieces.

    Returns:
        The rendered owner label.
    """
    number = PIECES.get(member)
    return f"{number} · {member}" if number else member


def _block(label: str, body: str) -> str:
    """Render one labelled paragraph with a hanging indent."""
    return textwrap.fill(
        body,
        width=_WIDTH,
        initial_indent=label.ljust(_LABEL_WIDTH),
        subsequent_indent=" " * _LABEL_WIDTH,
    )


def fail(
    *,
    member: str,
    problem: str,
    what: str,
    fix: str,
    if_not: str,
    scar: str,
) -> NoReturn:
    """Fail the current check with the four-part conformance message.

    Uses ``pytrace=False``: the traceback through this module tells the reader nothing
    they need, and it buries the four lines that do.

    Args:
        member: The adapter member (or other owner) responsible — see :func:`piece_label`.
        problem: A one-line summary of the wiring mistake.
        what: What was actually found, naming the offending values.
        fix: The edit to make, concretely enough to act on without opening the docs.
        if_not: What happens if it is left as it is — the silent consequence.
        scar: The defect in this repository's history that this check descends from.

    Raises:
        Failed: Always; this function does not return.
    """
    message = "\n".join(
        [
            "",
            f"CONFORMANCE FAILURE — {piece_label(member)}",
            problem,
            "",
            _block("what", what),
            _block("fix", fix),
            _block("if not", if_not),
            _block("scar", scar),
            "",
        ]
    )
    pytest.fail(message, pytrace=False)
