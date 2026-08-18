"""Host wiring for the durable job substrate — the half that knows about Temporal.

``aegis.jobs`` declares the contract: the record tables, the stage set and their
policies, and ``@tenant_activity``. **It must not import an orchestrator SDK**, so
everything that does lives here:

============================ ==================================================
:mod:`app.jobs.client`       The Temporal client singleton, built from settings.
:mod:`app.jobs.activities`   The activity implementation — one scoped, idempotent,
                             single-transaction stage runner.
:mod:`app.jobs.flows`        Workflow definitions, kept import-safe because the
                             workflow sandbox re-imports the defining module.
:mod:`app.jobs.worker`       The worker bootstrap, in both launch modes.
============================ ==================================================

Importing *this* package pulls nothing: the submodules import ``temporalio``, the
application settings and the session factory, and a host that only wants to read job
rows (the console, the admin DB page) should be able to do so without any of that. The
names below are re-exported lazily for the same reason — see :func:`__getattr__`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.jobs.client import get_temporal_client, reset_temporal_client
    from app.jobs.worker import configured_queues, run_workers, start_worker_task

__all__ = [
    "configured_queues",
    "get_temporal_client",
    "reset_temporal_client",
    "run_workers",
    "start_worker_task",
]

_LAZY: dict[str, str] = {
    "get_temporal_client": "app.jobs.client",
    "reset_temporal_client": "app.jobs.client",
    "configured_queues": "app.jobs.worker",
    "run_workers": "app.jobs.worker",
    "start_worker_task": "app.jobs.worker",
}


def __getattr__(name: str) -> Any:  # noqa: ANN401 - module-level lazy re-export
    """Import a submodule attribute on first access (PEP 562).

    Args:
        name: The attribute being read off this package.

    Returns:
        The attribute from its defining submodule.

    Raises:
        AttributeError: If this package exports no such name.
    """
    module_name = _LAZY.get(name)
    if module_name is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module  # noqa: PLC0415 - local to the lazy path

    return getattr(import_module(module_name), name)
