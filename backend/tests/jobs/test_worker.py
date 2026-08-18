"""The worker bootstrap: what it polls, what it refuses, and both launch modes.

The concurrency behaviour these queues exist for is proved against a real server in
``test_durable_ingest.py``. What is checked here is everything around it that would
otherwise fail *silently*:

* a typo'd queue name in ``TEMPORAL_TASK_QUEUES`` would start a worker polling a queue
  nothing schedules onto, while the real queue went unserved — and **no exception would be
  raised anywhere**. Work would simply stop happening. So it raises, and the standalone
  entry point exits non-zero saying which name was wrong;
* a stage with no registered handler fails only when a document first reaches it, which
  may be days later. The worker names the gap at startup instead;
* ``python -m app.jobs.worker`` is one of the two launch modes the phase requires, so it
  is invoked as a real subprocess rather than assumed to exist.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from aegis.jobs.stages import (
    CPU_QUEUE,
    DEFAULT_QUEUE,
    IO_QUEUE,
    TASK_QUEUES,
    UnknownStageError,
    clear_stage_handlers,
)

from app.config import get_settings
from app.jobs.worker import _report_unhandled_stages, configured_queues

from .conftest import register_recording_handlers

_BACKEND = Path(__file__).resolve().parents[2]


@pytest.fixture
def queue_setting():
    """Set ``TEMPORAL_TASK_QUEUES`` for one test and put it back afterwards."""
    settings = get_settings()
    original = settings.temporal_task_queues

    def _set(value: str) -> None:
        settings.temporal_task_queues = value

    yield _set
    settings.temporal_task_queues = original


def test_an_unconfigured_worker_polls_every_declared_queue(queue_setting):
    queue_setting("")

    assert configured_queues() == TASK_QUEUES


def test_a_worker_can_be_pinned_to_one_queue(queue_setting):
    # The scale-out posture: one process per queue, so the CPU box and the IO box are
    # different machines.
    queue_setting(f" {CPU_QUEUE} , {IO_QUEUE} ")

    assert [spec.name for spec in configured_queues()] == [CPU_QUEUE, IO_QUEUE]


def test_a_typo_in_the_queue_configuration_is_refused(queue_setting):
    queue_setting("aegis-cpuu")

    with pytest.raises(UnknownStageError) as raised:
        configured_queues()

    message = str(raised.value)
    assert "aegis-cpuu" in message
    # The message must list the real names, or the operator has nothing to correct it to.
    assert CPU_QUEUE in message


def test_the_worker_names_the_stages_it_has_no_handler_for(caplog):
    clear_stage_handlers()
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        _report_unhandled_stages()

    warnings = [record.getMessage() for record in caplog.records]
    assert warnings, "a worker that can perform no stage at all said nothing about it"
    assert "parse" in warnings[0]
    assert "register_stage_handler" in warnings[0]


def test_a_fully_wired_worker_reports_no_gap(caplog, stage_log):
    register_recording_handlers(stage_log)
    with caplog.at_level(logging.WARNING, logger="app.jobs.worker"):
        _report_unhandled_stages()

    # The positive control for the test above: the warning must be a fact about the
    # registry, not something the worker always says.
    assert [record.getMessage() for record in caplog.records] == []


def test_the_standalone_launch_mode_exists_and_validates_its_configuration():
    """``python -m app.jobs.worker`` is a required launch mode, so it is really launched.

    Run with a deliberately wrong queue name: the process must exit non-zero *before*
    reaching the network, which proves both that the module entry point exists and that
    the validation is fatal there rather than only in the in-process mode.
    """
    proc = subprocess.run(
        [sys.executable, "-m", "app.jobs.worker"],
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                [str(_BACKEND / "src"), str(_BACKEND.parent / "aegis" / "src")]
            ),
            "TEMPORAL_TASK_QUEUES": "aegis-typo",
            # Deliberately unreachable: if the guard did not fire first, this is what the
            # process would try to dial, and the failure would name a connection instead.
            "TEMPORAL_ADDRESS": "127.0.0.1:1",
        },
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert proc.returncode != 0
    combined = proc.stdout + proc.stderr
    assert "aegis-typo" in combined
    assert "Connection" not in combined, (
        "the worker tried to connect before validating its queue configuration"
    )


def test_the_default_queue_is_the_one_that_runs_workflows():
    # Asserted here as well as in the aegis suite because this is the module that acts on
    # it: a workflow registered on the single-slot CPU queue could wait for a slot its own
    # activity holds.
    assert [spec.name for spec in TASK_QUEUES if spec.runs_workflows] == [DEFAULT_QUEUE]


async def test_a_worker_that_cannot_reach_the_orchestrator_fails_loudly():
    """An unreachable server must raise, not leave a substrate that accepts nothing.

    The failure mode this rules out is the quiet one: a worker that swallowed the
    connection error would sit there looking healthy while every queued job waited for a
    poller that does not exist. Because it raises, the lifespan's supervisor turns it
    into an ERROR and the operator learns the substrate is down.
    """
    from aegis.jobs.scope import reset_activity_session_factory

    from app.jobs.client import reset_temporal_client
    from app.jobs.worker import run_workers

    settings = get_settings()
    original = settings.temporal_address
    settings.temporal_address = "127.0.0.1:1"
    reset_temporal_client()
    try:
        with pytest.raises(RuntimeError) as raised:
            await run_workers()
    finally:
        settings.temporal_address = original
        reset_temporal_client()
        # ``run_workers`` wires the session factory before it dials, so unwire it: a
        # factory left bound to another test's disposed engine is a miserable failure to
        # trace back to here.
        reset_activity_session_factory()

    assert "127.0.0.1:1" in str(raised.value)
