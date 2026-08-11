"""A tiny (kind, name) registry so components are swappable and discoverable.

Impls register with :func:`register`; consumers resolve with :func:`get`.
Third-party components can also be discovered via ``aegis.<kind>`` entry points
(:func:`discover`) without editing Aegis.
"""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import entry_points
from typing import TypeVar

_REGISTRY: dict[tuple[str, str], type] = {}
_T = TypeVar("_T", bound=type)


def register(kind: str, name: str) -> Callable[[_T], _T]:
    """Register a component class under ``(kind, name)`` and return it unchanged.

    Args:
        kind: The component kind (e.g., 'guardrail', 'adapter').
        name: The component name (e.g., 'dummy', 'azure-ai').

    Returns:
        A decorator that registers the decorated class and returns it unchanged.

    Example:
        >>> @register("guardrail", "dummy")
        ... class Dummy:
        ...     pass
        >>> get("guardrail", "dummy") is Dummy
        True
    """

    def _decorate(cls: _T) -> _T:
        _REGISTRY[(kind, name)] = cls
        return cls

    return _decorate


def get(kind: str, name: str) -> type:
    """Return the registered class for ``(kind, name)`` or raise ``KeyError``.

    Args:
        kind: The component kind.
        name: The component name.

    Returns:
        The registered class.

    Raises:
        KeyError: If no component is registered for (kind, name).

    Example:
        >>> @register("guardrail", "dummy")
        ... class Dummy:
        ...     pass
        >>> get("guardrail", "dummy")
        <class 'Dummy'>
    """
    return _REGISTRY[(kind, name)]


def available(kind: str) -> list[str]:
    """Return the sorted registered names for ``kind``.

    Args:
        kind: The component kind.

    Returns:
        A sorted list of registered names for the given kind.

    Example:
        >>> @register("guardrail", "dummy")
        ... class Dummy:
        ...     pass
        >>> "dummy" in available("guardrail")
        True
    """
    return sorted(n for (k, n) in _REGISTRY if k == kind)


def discover(kind: str) -> None:
    """Load third-party components advertised under the ``aegis.<kind>`` group.

    This function discovers and loads entry points registered in the package
    metadata under the ``aegis.<kind>`` group. This allows third-party packages
    to register components without modifying Aegis core.

    Args:
        kind: The component kind (e.g., 'guardrail', 'adapter').

    Example:
        >>> discover("guardrail")  # Loads all aegis.guardrail entry points
    """
    for ep in entry_points(group=f"aegis.{kind}"):
        ep.load()
