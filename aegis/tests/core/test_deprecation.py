"""A removal must be announced, and the announcement must name the replacement.

Two claims are load-bearing here, and nothing else in this file is.

1. **The warning names the replacement and the version that removes it.** A
   deprecation warning that says only "this is deprecated" leaves the caller with
   the same problem plus a warning, so the message text is asserted, not just the
   fact that a warning fired.
2. **A deprecation with no replacement cannot be written.** ``use=""`` raises at
   *decoration* time — import time — so the failure lands on the author rather
   than on the integrator who calls it six months later.
"""

from __future__ import annotations

import warnings

import pytest

from aegis.core import AegisDeprecationWarning, deprecated, warn_deprecated


def test_the_warning_names_the_replacement_and_the_removal_version():
    """All four facts — the name, since, removed_in, and the replacement."""

    @deprecated(since="0.2.0", removed_in="0.3.0", use="aegis.retrieval.Retriever")
    def old_retriever() -> str:
        return "still works"

    with pytest.warns(AegisDeprecationWarning) as caught:
        assert old_retriever() == "still works"

    message = str(caught[0].message)
    assert "old_retriever" in message
    assert "0.2.0" in message
    assert "0.3.0" in message
    assert "aegis.retrieval.Retriever" in message


def test_a_deprecation_with_no_replacement_is_refused_at_decoration_time():
    """The threat is rejected where it is written, not where it is called."""
    with pytest.raises(ValueError, match="threat rather than a warning"):
        deprecated(since="0.2.0", removed_in="0.3.0", use="")

    with pytest.raises(ValueError, match="threat rather than a warning"):
        warn_deprecated("x", since="0.2.0", removed_in="", use="y")


def test_the_warning_points_at_the_caller_not_at_aegis():
    """``stacklevel`` is right, so the reported file is the one to edit.

    A warning attributed to ``aegis/core/deprecation.py`` tells the integrator
    nothing; one attributed to their own call site tells them the line to change.
    """

    @deprecated(since="0.2.0", removed_in="0.3.0", use="something_else")
    def old() -> None:
        return None

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old()

    assert len(caught) == 1
    assert caught[0].filename == __file__


def test_the_deprecated_callable_keeps_working_and_keeps_its_identity():
    """A deprecation is an announcement, never a behaviour change."""

    @deprecated(since="0.2.0", removed_in="0.3.0", use="new_add")
    def add(a: int, b: int) -> int:
        """Add two numbers."""
        return a + b

    with warnings.catch_warnings():
        warnings.simplefilter("ignore", AegisDeprecationWarning)
        assert add(2, 3) == 5

    assert add.__name__ == "add"
    assert "Add two numbers." in (add.__doc__ or "")
    assert "new_add" in (add.__doc__ or "")
