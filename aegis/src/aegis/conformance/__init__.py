"""The executable conformance suite: one command that proves an adapter is wired right.

::

    pip install 'aegis[conformance]'
    AEGIS_ADAPTER=myapp.adapter pytest --pyargs aegis.conformance

Every check in this package descends from a **defect this repository actually shipped**
— named, in each check's docstring, under ``SCAR``. That is the whole design rule. A
suite of plausible-sounding checks is a checklist; a suite where every check has a scar
is evidence, and the scars are all of the same species: *the integration looked like it
worked*. A specialist that silently answered as QA. A memory piece on disk that was
never an attribute of the adapter package. A playbook renamed out of the selector's
reach. An ML spec that quietly trained on generic noise and served the result as domain
evidence. None of them raised anything; a first-attempt integrator would have shipped
every one.

**What this suite is not.** It never asks "does this work *well*" — no retrieval
quality, no answer grading, no latency. It asks "did you wire this correctly", which is
why it needs **no Postgres, no Redis, no Temporal, no gateway key and never a model
call**: an integrator runs it *before* any of that works, which is exactly when a wiring
mistake is cheapest to fix.

Pointing it at an adapter
-------------------------

Three ways, in precedence order:

1. ``pytest --pyargs aegis.conformance --aegis-adapter myapp.adapter``
2. ``AEGIS_ADAPTER=myapp.adapter pytest --pyargs aegis.conformance``
3. Nothing — the run stops immediately with a usage error naming both of the above.

The adapter is an *import path*, normally the domain package described by
:class:`aegis.adapter.DomainAdapter`. It is imported, read, and never executed beyond
the pure, side-effect-free reads each check documents.
"""

from __future__ import annotations

import importlib

__all__ = [
    "ADAPTER_ENV_VAR",
    "ADAPTER_OPTION",
    "AdapterNotSelectedError",
    "load_adapter",
]

ADAPTER_ENV_VAR = "AEGIS_ADAPTER"
"""Environment variable naming the adapter import path."""

ADAPTER_OPTION = "--aegis-adapter"
"""Command-line option naming the adapter import path (wins over the environment)."""

_HOW_TO_POINT_IT = (
    f"Name the adapter to check, either way:\n"
    f"    pytest --pyargs aegis.conformance {ADAPTER_OPTION} myapp.adapter\n"
    f"    {ADAPTER_ENV_VAR}=myapp.adapter pytest --pyargs aegis.conformance\n"
    f"'myapp.adapter' is the import path of the package that satisfies "
    f"aegis.adapter.DomainAdapter."
)


class AdapterNotSelectedError(RuntimeError):
    """No adapter was named, or the one named could not be imported.

    Deliberately a hard stop rather than a skipped suite: a conformance run that
    quietly checks nothing is indistinguishable, in a terminal, from a conformance run
    that passed.
    """


def load_adapter(name: str | None) -> object:
    """Import and return the adapter module named by ``name``.

    Args:
        name: The adapter's import path (e.g. ``"myapp.adapter"``), or ``None`` /
            empty when the caller found nothing on the command line or in the
            environment.

    Returns:
        The imported adapter module.

    Raises:
        AdapterNotSelectedError: If ``name`` is empty, or the module cannot be
            imported. The message carries the exact command to run, because the person
            reading it is normally seeing this suite for the first time.
    """
    if not name:
        raise AdapterNotSelectedError(
            f"aegis.conformance was not told which adapter to check.\n\n{_HOW_TO_POINT_IT}"
        )
    try:
        return importlib.import_module(name)
    except ImportError as exc:  # pragma: no cover - exercised by the broken-adapter run
        raise AdapterNotSelectedError(
            f"aegis.conformance could not import the adapter {name!r}: {exc}\n\n"
            f"The module must be importable from the interpreter running pytest — the "
            f"usual cause is that the host package is not installed and its source "
            f"directory is not on PYTHONPATH.\n\n{_HOW_TO_POINT_IT}"
        ) from exc
