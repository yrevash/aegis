"""The package's README must name symbols that exist (§3.11).

``aegis/README.md`` told an integrator to call ``aegis.require()``. That attribute
has never existed — the helper is ``aegis.core.require`` — so the first line an AI
integrator copied out of the README raised ``AttributeError``. This is the cheapest
possible test and it guards the most expensive kind of failure: a first-contact
document that is wrong.

Also asserted here is the PEP 561 marker. Without ``py.typed`` beside
``aegis/__init__.py`` every annotation in the package is invisible to an
integrator's type checker, however complete it is — the same class of silent
first-contact failure.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import aegis
import aegis.core

_PACKAGE_ROOT = Path(aegis.__file__).resolve().parent
_README = _PACKAGE_ROOT.parents[1] / "README.md"


def test_require_lives_on_aegis_core_not_on_aegis():
    """``require`` is reachable exactly where the README says it is.

    Both halves matter: it must be on ``aegis.core``, and it must **not** silently
    appear on ``aegis`` later — if it does, the README's careful "it is not
    ``aegis.require``" caveat becomes the new lie.
    """
    assert hasattr(aegis.core, "require")
    assert not hasattr(aegis, "require")
    from aegis.core import require

    assert callable(require)
    assert list(inspect.signature(require).parameters) == ["extra", "module"]


def test_readme_does_not_document_a_symbol_that_does_not_exist():
    """No ``aegis.<name>()`` in the README may name a missing top-level attribute."""
    assert _README.is_file(), f"package README missing at {_README}"
    text = _README.read_text()
    # Only bare top-level calls: ``aegis.foo(`` but not ``aegis.core.foo(``.
    named = set(re.findall(r"`aegis\.(\w+)\(", text))
    missing = sorted(name for name in named if not hasattr(aegis, name))
    assert not missing, (
        "aegis/README.md documents top-level attributes that do not exist on the "
        f"`aegis` package: {missing}"
    )


def test_readme_points_at_the_real_require():
    """The README names ``aegis.core.require`` — the symbol an integrator can call."""
    text = _README.read_text()
    assert "aegis.core.require" in text


def test_package_ships_the_pep_561_marker():
    """``py.typed`` sits beside ``__init__.py``, so the annotations are visible.

    Asserted from the imported package's own directory rather than from a relative
    source path, so it is the *installed* layout that is checked.
    """
    marker = _PACKAGE_ROOT / "py.typed"
    assert marker.is_file(), (
        f"PEP 561 marker missing at {marker}: every annotation in this package is "
        "invisible to an integrator's type checker without it."
    )
