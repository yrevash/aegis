"""Announce a removal instead of shipping one.

There was no deprecation machinery in this package at all: a name either existed
or had already gone, and an integrator learned which by watching an import fail.
This module is the whole mechanism, deliberately one decorator and one function.

The rule it enforces is the reason it exists. **A deprecation that does not name
its replacement is a threat, not a warning** — so ``since``, ``removed_in`` and
``use`` are all required, and an empty ``use`` raises :class:`ValueError` at
decoration time (import time), not at call time. The failure lands on whoever
wrote the deprecation, while they are still looking at it.

Usage::

    from aegis.core.deprecation import deprecated

    @deprecated(since="0.2.0", removed_in="0.3.0", use="aegis.retrieval.Retriever")
    def build_legacy_retriever() -> None:
        ...

Calling it emits an :class:`AegisDeprecationWarning` naming the replacement and
the version that removes it, pointed at the *caller's* line, not at this module.

Python hides :class:`DeprecationWarning` outside ``__main__`` by default, which is
correct for a library — the noise belongs to whoever chose to look. Run
``python -W default::DeprecationWarning`` or ``pytest -W error::DeprecationWarning``
to surface every one on the path an integration actually takes.
"""

from __future__ import annotations

import functools
import warnings
from collections.abc import Callable
from typing import ParamSpec, TypeVar

__all__ = ["AegisDeprecationWarning", "deprecated", "warn_deprecated"]

_P = ParamSpec("_P")
_R = TypeVar("_R")


class AegisDeprecationWarning(DeprecationWarning):
    """A deprecation raised by Aegis itself.

    A distinct subclass so an integrator can act on *our* removals alone —
    ``warnings.simplefilter("error", AegisDeprecationWarning)`` in a test suite
    turns an Aegis deprecation into a build failure without also failing on a
    transitive dependency's.
    """


def _describe(name: str, *, since: str, removed_in: str, use: str) -> str:
    """Return the warning text: what went, when, and what replaces it.

    Args:
        name: Fully-qualified name of the deprecated object.
        since: Version in which it was deprecated.
        removed_in: Version in which it will stop existing.
        use: The replacement to call instead.

    Returns:
        A single sentence naming all four facts.
    """
    return (
        f"{name} is deprecated since aegis {since} and will be removed in "
        f"aegis {removed_in}. Use {use} instead."
    )


def _reject_a_threat(*, since: str, removed_in: str, use: str) -> None:
    """Raise unless the deprecation names a replacement and both versions.

    Args:
        since: Version in which the object was deprecated.
        removed_in: Version in which it will stop existing.
        use: The replacement to call instead.

    Raises:
        ValueError: If any of the three is empty or blank.
    """
    missing = [
        field
        for field, value in (("since", since), ("removed_in", removed_in), ("use", use))
        if not value or not value.strip()
    ]
    if missing:
        raise ValueError(
            f"deprecation is missing {missing}: a deprecation that does not say what "
            "to use instead, and by which version, is a threat rather than a warning"
        )


def warn_deprecated(
    name: str,
    *,
    since: str,
    removed_in: str,
    use: str,
    stacklevel: int = 2,
) -> None:
    """Emit the deprecation warning for ``name`` without wrapping a callable.

    For the cases a decorator cannot reach — a deprecated module attribute served
    from ``__getattr__``, a deprecated keyword argument, a deprecated branch inside
    a function that is otherwise current.

    Args:
        name: Fully-qualified name of the deprecated object.
        since: Version in which it was deprecated.
        removed_in: Version in which it will stop existing.
        use: The replacement to call instead.
        stacklevel: Frames to skip so the warning points at the caller. The
            default of 2 is right when this is called directly from the
            deprecated object itself.

    Raises:
        ValueError: If ``since``, ``removed_in`` or ``use`` is blank.
    """
    _reject_a_threat(since=since, removed_in=removed_in, use=use)
    warnings.warn(
        _describe(name, since=since, removed_in=removed_in, use=use),
        AegisDeprecationWarning,
        stacklevel=stacklevel,
    )


def deprecated(
    *,
    since: str,
    removed_in: str,
    use: str,
) -> Callable[[Callable[_P, _R]], Callable[_P, _R]]:
    """Mark a callable deprecated, naming its replacement and its removal version.

    The three arguments are keyword-only and all required. Two version strings in
    a row are exactly the pair a positional call gets backwards, and the resulting
    warning would be wrong in a way nothing catches.

    The wrapped callable keeps its behaviour, its signature and its identity; the
    replacement and removal version are also appended to ``__doc__``, so the
    generated reference (``scripts/build_api_docs.py``) carries the same notice
    the runtime does.

    Args:
        since: Version in which the callable was deprecated, e.g. ``"0.2.0"``.
        removed_in: Version in which it will stop existing, e.g. ``"0.3.0"``.
        use: The replacement, as an importable dotted path or a short instruction.

    Returns:
        A decorator that wraps the callable and warns on every call.

    Raises:
        ValueError: If ``since``, ``removed_in`` or ``use`` is blank. Raised at
            decoration time, so a threat cannot be imported, let alone shipped.
    """
    _reject_a_threat(since=since, removed_in=removed_in, use=use)

    def decorate(func: Callable[_P, _R]) -> Callable[_P, _R]:
        """Wrap ``func`` so every call announces the removal first."""
        name = f"{func.__module__}.{func.__qualname__}"
        notice = _describe(name, since=since, removed_in=removed_in, use=use)

        @functools.wraps(func)
        def wrapper(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            """Warn, then call the deprecated object unchanged."""
            warnings.warn(notice, AegisDeprecationWarning, stacklevel=2)
            return func(*args, **kwargs)

        wrapper.__doc__ = f"{func.__doc__ or ''}\n\n.. deprecated:: {since}\n    {notice}\n"
        return wrapper

    return decorate
