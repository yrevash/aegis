"""The adapter's own count of itself must match what is on disk (§3.11).

The directory simultaneously claimed "piece 2 of 5", "3 of 5", "4 of 5", "6 of 5"
and "**6 of 6**" while holding eight modules plus ``corpus/`` and ``skills/`` —
and ``roster.py`` and ``skills/`` appeared in no checklist at all. An integrator
(human or AI) retargeting the domain on the day follows that checklist, so a piece
missing from it is a piece that does not get swapped.

Counting is the one thing a test does better than a reviewer, so the manifest is
checked against the filesystem rather than trusted: add a ninth module and this
fails until the docstrings, ``README.md`` and ``SWAP.md`` are updated with it.
"""

from __future__ import annotations

import re
from pathlib import Path

import app.adapter

_ADAPTER = Path(app.adapter.__file__).resolve().parent

#: The eight domain modules. ``__init__.py`` is excluded deliberately: it is the
#: registry that re-exports the pieces to the core, not a piece to be rewritten.
EXPECTED_MODULES = frozenset(
    {
        "schema.py",
        "ml_spec.py",
        "generator.py",
        "tools.py",
        "personas.py",
        "prompts.py",
        "memory_spec.py",
        "roster.py",
    }
)

#: The two content directories that complete the ten pieces.
EXPECTED_DIRS = frozenset({"corpus", "skills"})

#: Total pieces: eight modules + two directories. Every ``piece N of M`` claim in
#: the directory must use this as ``M``.
TOTAL_PIECES = len(EXPECTED_MODULES) + len(EXPECTED_DIRS)

_PIECE_RE = re.compile(r"piece (\d+) of (\d+)")


def _adapter_sources() -> list[Path]:
    """Return every Python source in the adapter tree (``__pycache__`` excluded)."""
    return [p for p in sorted(_ADAPTER.rglob("*.py")) if "__pycache__" not in p.parts]


def test_the_eight_modules_and_two_directories_are_what_is_on_disk():
    """The manifest above is the directory, not a wish about it."""
    modules = {
        p.name
        for p in _ADAPTER.glob("*.py")
        if p.name != "__init__.py"
    }
    assert modules == EXPECTED_MODULES, (
        "the adapter's module set changed; update EXPECTED_MODULES, every "
        "'piece N of M' docstring, README.md and SWAP.md together"
    )
    dirs = {
        p.name
        for p in _ADAPTER.iterdir()
        if p.is_dir() and p.name != "__pycache__"
    }
    assert dirs == EXPECTED_DIRS
    assert TOTAL_PIECES == 10


def test_every_piece_claim_agrees_on_the_total():
    """No module may claim a different denominator from its neighbours."""
    disagreements: dict[str, list[str]] = {}
    for source in _adapter_sources():
        wrong = [
            f"piece {n} of {m}"
            for n, m in _PIECE_RE.findall(source.read_text())
            if int(m) != TOTAL_PIECES
        ]
        if wrong:
            disagreements[str(source.relative_to(_ADAPTER))] = wrong
    assert not disagreements, (
        f"these adapter docstrings disagree with the real total of {TOTAL_PIECES}: "
        f"{disagreements}"
    )


def test_piece_numbers_are_unique_and_in_range():
    """Two modules may not both be piece 6, and none may be piece 11.

    A module's **first** claim is its own number; later mentions are cross
    references ("paired with piece 5") and are not ownership.
    """
    owner: dict[int, str] = {}
    for source in _adapter_sources():
        claims = _PIECE_RE.findall(source.read_text())
        if not claims:
            continue
        number = int(claims[0][0])
        name = str(source.relative_to(_ADAPTER))
        assert 1 <= number <= TOTAL_PIECES, (
            f"{name} claims piece {number}, outside 1..{TOTAL_PIECES}"
        )
        assert number not in owner, (
            f"piece {number} is claimed by both {owner[number]} and {name}"
        )
        owner[number] = name


def test_every_module_states_its_own_piece_number():
    """A module with no number is a module that falls out of the checklist."""
    silent = [
        name
        for name in sorted(EXPECTED_MODULES)
        if not _PIECE_RE.search((_ADAPTER / name).read_text())
    ]
    assert not silent, f"these adapter modules state no piece number: {silent}"


def test_the_checklists_name_every_piece():
    """``README.md`` and ``SWAP.md`` must list all ten pieces, roster and skills included.

    ``roster.py`` and ``skills/`` were both absent from every checklist, which is how
    a swap-day edit misses them entirely.
    """
    for doc in ("README.md", "SWAP.md"):
        text = (_ADAPTER / doc).read_text()
        missing = [name for name in sorted(EXPECTED_MODULES) if name not in text]
        missing += [f"{name}/" for name in sorted(EXPECTED_DIRS) if f"{name}/" not in text]
        assert not missing, f"adapter/{doc} does not name: {missing}"
