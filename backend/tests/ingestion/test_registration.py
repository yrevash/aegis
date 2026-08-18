"""Which process binds the work of the stages to the substrate, and which must not.

Registration is a *seam*: :func:`aegis.jobs.register_stage_handler` is how the domain work
of a stage reaches a substrate that knows nothing about parsing. Two things follow, and
both are asserted here because getting either wrong fails silently:

* **Every declared stage has a handler.** A stage with none fails its activity the first
  time a document reaches it — correct, because the substrate must never advance
  ``completed_stage`` past work nobody did, but discovered at the worst possible moment.
  :func:`app.ingestion.stages.register_ingest_handlers` refuses at boot instead, and this
  is the test that keeps that promise honest when a stage is added to the pipeline.
* **Starting a worker does not re-register.** ``run_workers`` is shared by both launch
  modes and is called by tests that have deliberately installed instrumented handlers (the
  kill-and-resume test reads *which process ran which stage* off them). If the bootstrap
  force-registered, those handlers would be replaced by the real ones and the test would
  silently be measuring something else.
"""

from __future__ import annotations

import pytest
from aegis.jobs.scope import reset_activity_session_factory
from aegis.jobs.stages import (
    INGEST_STAGES,
    clear_stage_handlers,
    register_stage_handler,
    stage_handler,
)

from app.ingestion import stages as ingest_stages
from app.jobs import worker


def test_every_declared_stage_gets_a_handler() -> None:
    clear_stage_handlers()
    try:
        ingest_stages.register_ingest_handlers()

        for spec in INGEST_STAGES:
            assert stage_handler(spec.name) is ingest_stages._HANDLERS[spec.name]
    finally:
        clear_stage_handlers()


def test_registration_is_refused_if_a_declared_stage_has_no_handler(monkeypatch) -> None:
    """The boot-time failure the pipeline's constancy makes possible.

    A stage added to :data:`aegis.jobs.INGEST_STAGES` without a handler is a programming
    error, and it is knowable before a single document is uploaded.
    """
    monkeypatch.delitem(ingest_stages._HANDLERS, "graph")
    clear_stage_handlers()
    try:
        with pytest.raises(RuntimeError, match="graph"):
            ingest_stages.register_ingest_handlers()
    finally:
        clear_stage_handlers()


def test_starting_a_worker_does_not_replace_a_hosts_handlers() -> None:
    """The seam survives the bootstrap, which is what the substrate's tests rely on."""
    clear_stage_handlers()

    async def instrumented(session, *, tenant_id, document_id, stage):  # noqa: ANN001, ANN202
        """A host's own handler, which must still be the one registered afterwards."""
        return {}

    register_stage_handler("parse", instrumented)
    try:
        # The two things ``run_workers`` does before it touches the network.
        worker._wire_session_factory()
        worker._report_unhandled_stages()

        assert stage_handler("parse") is instrumented
    finally:
        reset_activity_session_factory()
        clear_stage_handlers()
