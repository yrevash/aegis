"""``PUBLIC.md`` is read by this test, so it cannot quietly stop being true.

The boundary between public and internal is only worth stating if it is also
enforced. `arXiv 2603.15159` is the reason this is a test rather than a longer
document: complete, correct specs move an integrating model's pass@1 by only
+5.0 to +8.3 pp, so prose is the third lever. The first is an executable claim.

Two things are checked, and they are exactly the two halves of the rule stated at
the top of ``PUBLIC.md``:

1. Every name in a **Stable** table imports.
2. Every one of them is in its package's ``__all__``.

Rename or move a Stable name and this fails, naming it, until ``PUBLIC.md`` is
updated to match. Additions elsewhere in the package are silent — a Provisional
name is not promised, and this test does not pretend otherwise.
"""

from __future__ import annotations

import importlib
import re
from pathlib import Path

import aegis

_PACKAGE_ROOT = Path(aegis.__file__).resolve().parent
_PUBLIC_MD = _PACKAGE_ROOT.parents[1] / "PUBLIC.md"

#: A table row whose FIRST cell is a backticked dotted name. Anchored to the row
#: start so a name mentioned in a description cell (``PUBLIC.md`` says "it is not
#: ``aegis.require``") is not mistaken for a promise.
_STABLE_ROW = re.compile(r"^\|\s*`(aegis\.[A-Za-z_][A-Za-z0-9_.]*)`\s*\|", re.MULTILINE)

_STABLE_HEADING = re.compile(r"^## Stable — the (\d+)\s*$", re.MULTILINE)


def _stable_section() -> str:
    """Return the text of the Stable section, from its heading to the next ``## ``.

    Returns:
        The section body.
    """
    text = _PUBLIC_MD.read_text()
    match = _STABLE_HEADING.search(text)
    assert match, f"{_PUBLIC_MD} has no '## Stable — the N' heading"
    rest = text[match.end() :]
    end = re.search(r"^## ", rest, re.MULTILINE)
    return rest[: end.start()] if end else rest


def _stable_names() -> list[str]:
    """Return every dotted name promised Stable, in document order."""
    return _STABLE_ROW.findall(_stable_section())


def test_public_md_exists_beside_the_package():
    """The boundary ships with the wheel's source tree, not just in someone's head."""
    assert _PUBLIC_MD.is_file(), f"PUBLIC.md missing at {_PUBLIC_MD}"


def test_every_stable_name_imports_and_is_exported():
    """The two halves of the rule: it resolves, and its package declares it.

    A name that resolves but is absent from ``__all__`` is a name a reader cannot
    discover, and one an ``import *`` will not bring across — so it is not really
    public, whatever this file says about it.
    """
    names = _stable_names()
    assert names, "no Stable rows parsed out of PUBLIC.md — the table format changed"

    unresolved: list[str] = []
    unexported: list[str] = []
    for dotted in names:
        module_name, _, attribute = dotted.rpartition(".")
        module = importlib.import_module(module_name)
        if not hasattr(module, attribute):
            unresolved.append(dotted)
        elif attribute not in getattr(module, "__all__", ()):
            unexported.append(dotted)

    assert not unresolved, (
        "PUBLIC.md promises names that do not resolve — an integrator copying the "
        f"Stable table gets an AttributeError: {unresolved}"
    )
    assert not unexported, (
        "PUBLIC.md promises names their own package does not export in __all__, so "
        f"the stated rule ('in __all__ AND in this table') is violated: {unexported}"
    )


def test_the_stable_count_in_the_heading_is_the_number_of_rows():
    """"Stable — the 41" must be 41, or the ratio the file argues from is fiction."""
    text = _PUBLIC_MD.read_text()
    claimed = int(_STABLE_HEADING.search(text).group(1))
    actual = len(_stable_names())
    assert claimed == actual, (
        f"PUBLIC.md's heading claims {claimed} Stable names but the tables hold "
        f"{actual}; update the heading, and the 'one name in eighteen' ratio it "
        "argues from, together"
    )
