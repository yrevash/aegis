"""Workflow definitions, and the one package in this codebase that must stay import-safe.

The workflow sandbox **re-imports the module that defines a workflow** on every workflow
task, in a restricted environment, to guarantee that a replay of the same history
produces the same decisions. §3.0 of the phase measured the boundary precisely rather
than assuming it: ``time.time()`` and ``os.environ.get(...)`` at import are *accepted*;
``asyncio.run(...)`` at import is **rejected** with ``RuntimeError: Failed validating
workflow``. So the rule for everything under this package is narrower and sharper than
"keep it pure":

    **A workflow module must not run an event loop at import.**

Ordinary module-level constants, dataclasses and frozen tuples are fine — which is why
:mod:`aegis.jobs.stages` is stdlib-only and safe to read from inside a workflow.

Aegis has modules that *do* side-effectful work at import, and this package exists to
keep workflow definitions away from them. Anything a workflow needs from the wider
application is imported inside ``workflow.unsafe.imports_passed_through()``, which tells
the sandbox to share the already-imported module instead of re-executing it — the
supported escape hatch, used deliberately and narrowly rather than as a blanket.
"""

from __future__ import annotations

from app.jobs.flows.ingest import INGEST_WORKFLOW, IngestWorkflow

__all__ = ["INGEST_WORKFLOW", "IngestWorkflow"]
