"""Aegis — modular, importable agentic-AI platform components.

**One supported way up**::

    from aegis import Aegis

    aegis = await Aegis.from_env(adapter="myapp.adapter")

That single call replaces the ordered sequence of ten ``configure_*`` calls (three of
which used to fire as module-import side effects) that a host previously had to make in
the right order to get a working process. See :mod:`aegis.runtime` for what it reads,
what it refuses to guess, and the record it hands back.

Everything else is import-what-you-need: ``from aegis.guardrails import Guardrails``. The
:mod:`aegis.core` package is dependency-free (pydantic + stdlib); each component declares
its own optional dependencies as an extra (``pip install aegis[nemo]``).

:class:`~aegis.adapter.DomainAdapter` and :class:`~aegis.runtime.Aegis` are re-exported
here because they are the two names an integrator needs before they know anything else
about the package — the contract to implement, and the call that brings it up. Both are
resolved lazily (:pep:`562`), so ``import aegis`` still pulls in nothing heavier than the
standard library.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - re-exported for type checkers, resolved lazily below
    # Redundant aliases: the explicit re-export form, so a type checker (and ruff) sees
    # these as part of this package's public surface rather than as unused imports. The
    # runtime resolution is ``__getattr__`` below; this block exists purely so ``from
    # aegis import Aegis`` type-checks without importing anything at module load.
    from aegis.adapter import DomainAdapter as DomainAdapter
    from aegis.adapter import adapter_members as adapter_members
    from aegis.adapter import missing_members as missing_members
    from aegis.runtime import AdapterContractError as AdapterContractError
    from aegis.runtime import Aegis as Aegis
    from aegis.runtime import AegisAlreadyConfiguredError as AegisAlreadyConfiguredError
    from aegis.runtime import AegisError as AegisError
    from aegis.runtime import MissingConfigurationError as MissingConfigurationError
    from aegis.runtime import Seam as Seam
    from aegis.runtime import active as active

__version__ = "0.1.0"

#: Public name → the module it lives in. Resolved on first attribute access so that
#: ``import aegis`` costs nothing: :mod:`aegis.runtime` reads the environment through
#: pydantic-settings and :mod:`aegis.adapter` is typing-only, but neither should be paid
#: for by a caller that only wanted ``aegis.__version__``.
_LAZY: dict[str, str] = {
    "AdapterContractError": "aegis.runtime",
    "Aegis": "aegis.runtime",
    "AegisAlreadyConfiguredError": "aegis.runtime",
    "AegisError": "aegis.runtime",
    "DomainAdapter": "aegis.adapter",
    "MissingConfigurationError": "aegis.runtime",
    "Seam": "aegis.runtime",
    "active": "aegis.runtime",
    "adapter_members": "aegis.adapter",
    "missing_members": "aegis.adapter",
}

__all__ = ["__version__", *sorted(_LAZY)]


def __getattr__(name: str) -> Any:  # noqa: ANN401 - PEP 562 module __getattr__
    """Resolve a re-exported public name on first access (:pep:`562`).

    Raises:
        AttributeError: For any name not in the re-export table — the standard
            behaviour, so a typo still fails like a typo.
    """
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    import importlib

    return getattr(importlib.import_module(module), name)


def __dir__() -> list[str]:
    """Include the lazily-resolved names in ``dir(aegis)`` and tab completion."""
    return sorted(__all__)
