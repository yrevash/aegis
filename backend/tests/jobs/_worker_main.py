"""A worker process for the kill-and-resume test — the shipped bootstrap, plus handlers.

Run as a **separate OS process** so it can be hard-killed with ``SIGKILL``. That is the
point of the test it serves: a graceful shutdown proves nothing about durability, because
a graceful shutdown tells the orchestrator what happened. A ``SIGKILL`` tells it nothing,
which is the case the substrate has to survive.

The only thing this adds to production's entry point is stage handlers. It registers them
and then calls :func:`app.jobs.worker.main` — the identical function ``python -m
app.jobs.worker`` invokes — so what the test exercises is the shipped worker and not a
test-local imitation. (Handlers have to be added here because nothing registers a real
one until Phase 4 brings Docling; the worker warns about exactly that at startup.)

Each handler appends one line per event to the journal file named by ``AEGIS_KILL_TEST_LOG``:

    ``{stage},{pid},{attempt},{start|done}``

Appended, flushed and ``fsync``ed per line, because the process is about to be killed
without warning and a buffered journal would lose the very evidence the test reads. The
pid is what makes "did this stage re-run in the new process?" answerable rather than
inferred.

Environment:
    AEGIS_KILL_TEST_LOG: Path to the journal file.
    AEGIS_KILL_TEST_SLOW_STAGE: The stage that should sleep, so the kill lands mid-flight.
    AEGIS_KILL_TEST_SLOW_SECONDS: How long it sleeps, on its **first attempt only** — a
        retry returns immediately, so the test does not pay the sleep twice.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import Any

from aegis.jobs.stages import INGEST_STAGES, register_stage_handler
from temporalio import activity

_LOG_PATH = os.environ["AEGIS_KILL_TEST_LOG"]
_SLOW_STAGE = os.environ.get("AEGIS_KILL_TEST_SLOW_STAGE", "embed")
_SLOW_SECONDS = float(os.environ.get("AEGIS_KILL_TEST_SLOW_SECONDS", "20"))


def _journal(stage: str, attempt: int, event: str) -> None:
    """Append one durable line to the journal.

    Args:
        stage: The stage the event is about.
        attempt: The orchestrator's attempt number for this activity.
        event: ``"start"`` or ``"done"``.
    """
    with open(_LOG_PATH, "a", encoding="utf-8") as handle:
        handle.write(f"{stage},{os.getpid()},{attempt},{event}\n")
        handle.flush()
        os.fsync(handle.fileno())


def _make_handler(name: str) -> Any:  # noqa: ANN401 - a StageHandler closure
    """Build the handler for one stage.

    Args:
        name: The stage name.

    Returns:
        A coroutine function matching :class:`aegis.jobs.stages.StageHandler`.
    """

    async def handler(
        session: Any,  # noqa: ANN401 - AsyncSession
        *,
        tenant_id: int | None,
        document_id: int,
        stage: str,
    ) -> Mapping[str, Any]:
        attempt = activity.info().attempt if activity.in_activity() else 0
        _journal(stage, attempt, "start")
        if stage == _SLOW_STAGE and attempt == 1:
            # Only the first attempt is slow. The retry after the kill returns at once,
            # so the test's wall clock is the heartbeat timeout and not this sleep twice.
            await asyncio.sleep(_SLOW_SECONDS)
        _journal(stage, attempt, "done")
        if stage == "parse":
            return {"page_count": 11}
        if stage == "chunk":
            return {"chunk_count": 42}
        return {}

    handler.__name__ = f"{name}_handler"
    return handler


def main() -> None:
    """Register the handlers, then hand over to the shipped worker entry point."""
    for spec in INGEST_STAGES:
        register_stage_handler(spec.name, _make_handler(spec.name))

    from app.jobs.worker import main as worker_main

    worker_main()


if __name__ == "__main__":
    main()
