"""A workflow killed mid-run resumes without re-running completed stages.

**This is the phase's hardest definition-of-done item, and it is proved by killing a
process.** Not by asserting a code path, not by shutting a worker down gracefully — a
graceful shutdown tells the orchestrator what happened, and the case the substrate exists
to survive is the one where nothing is told anything.

The shape:

1. a real Temporal dev server, and a real ``documents`` row in the scratch PostgreSQL;
2. worker **A**, a separate OS process running the shipped
   :func:`app.jobs.worker.main` (see :mod:`tests.jobs._worker_main`), started with the
   scratch DSNs in its environment;
3. the workflow runs; ``parse``, ``chunk`` and ``enrich`` complete in A and are journalled
   with A's pid;
4. A is ``SIGKILL``ed while ``embed`` is in flight — verified from the journal, not from a
   sleep, so the kill cannot land in the wrong place;
5. worker **B** starts, a brand-new process that inherits nothing;
6. the workflow completes, and the journal says which stages ran in which process.

The assertion is the spike's measured claim, now made against this repository's own code:
**the only stage that re-runs is the one that was in flight.** ``parse``, ``chunk`` and
``enrich`` are never executed by B — which is what makes a failure at ``graph`` not
re-parse two hundred pages.

Two supporting facts are asserted alongside it, because without them the headline could
be true for the wrong reason: the document's ``completed_stage`` really does advance to
``graph`` (so the run genuinely finished rather than merely stopping), and every stage
appears exactly once in the ``done`` journal except ``embed``.

Why the recovery is not instant: a ``SIGKILL``ed worker sends no notice, so the
orchestrator learns the attempt is gone only when its heartbeat times out. That is
:attr:`aegis.jobs.StageSpec.heartbeat_seconds` times
:data:`aegis.jobs.stages.HEARTBEAT_TIMEOUT_FACTOR` — fifteen seconds here, which is why
this test costs about half a minute and why the alternative (waiting out ``embed``'s
fifteen-minute ``start_to_close_timeout``) is not one.
"""

from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from aegis.jobs import Document, JobRun, JobStatus
from aegis.jobs.stages import HEARTBEAT_TIMEOUT_FACTOR, stage_spec
from sqlalchemy import select
from temporalio.testing import WorkflowEnvironment

from app.jobs.flows import INGEST_WORKFLOW
from app.jobs.flows.contracts import IngestParams, IngestResult

from .conftest import (
    TENANT_A,
    free_port,
    seed_document,
    seed_tenants,
    skip_without_temporal,
    temporal_cli_path,
)

#: The stage the kill lands in.
_SLOW_STAGE = "embed"

#: How long that stage's **first** attempt takes. Comfortably longer than the time the
#: test needs to notice it started and send the signal, so the kill cannot race past it.
_SLOW_SECONDS = 30.0

#: Ceiling on waiting for the whole run to finish after the kill. Generous against the
#: heartbeat timeout below so a slow machine fails this test for a real reason or not at
#: all.
_RESUME_TIMEOUT = 90.0

#: Repository root, from which the worker subprocess resolves ``src`` and ``../aegis/src``.
_BACKEND = Path(__file__).resolve().parents[2]


def _worker_env(*, address: str, journal: Path, app_dsn: str, owner_dsn: str) -> dict[str, str]:
    """Build the environment a worker subprocess runs with.

    Nothing is inherited implicitly that matters: the DSNs point at this test's scratch
    database and the address at this test's dev server, so a developer's own ``.env``
    cannot make the subprocess talk to something else and quietly pass.

    Args:
        address: ``host:port`` of the dev server.
        journal: The file the handlers append their evidence to.
        app_dsn: The scratch database's **serving** DSN (non-superuser).
        owner_dsn: The scratch database's owner DSN.

    Returns:
        The environment mapping.
    """
    return {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            [str(_BACKEND / "src"), str(_BACKEND.parent / "aegis" / "src"), str(_BACKEND)]
        ),
        "PYTHONUNBUFFERED": "1",
        "TEMPORAL_ADDRESS": address,
        "TEMPORAL_NAMESPACE": "default",
        "POSTGRES_DSN": app_dsn,
        "POSTGRES_ADMIN_DSN": owner_dsn,
        "STORES": "on",
        "DB_BOOTSTRAP": "0",
        "AEGIS_KILL_TEST_LOG": str(journal),
        "AEGIS_KILL_TEST_SLOW_STAGE": _SLOW_STAGE,
        "AEGIS_KILL_TEST_SLOW_SECONDS": str(_SLOW_SECONDS),
    }


def _start_worker(env: dict[str, str]) -> subprocess.Popen[bytes]:
    """Launch one worker process.

    Returns:
        The running process.
    """
    return subprocess.Popen(
        [sys.executable, str(_BACKEND / "tests" / "jobs" / "_worker_main.py")],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _read_journal(journal: Path) -> list[tuple[str, int, int, str]]:
    """Parse the journal into ``(stage, pid, attempt, event)`` records."""
    if not journal.exists():
        return []
    records = []
    for line in journal.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        stage, pid, attempt, event = line.split(",")
        records.append((stage, int(pid), int(attempt), event))
    return records


async def _await_journal(
    journal: Path, predicate, *, timeout: float, what: str
) -> list[tuple[str, int, int, str]]:
    """Poll the journal until ``predicate`` holds, or fail saying what never happened.

    Polling a file rather than sleeping a guessed interval is what makes the kill land
    where the test says it does: a fixed sleep would sometimes fire before ``embed``
    started and sometimes after it finished, and both would test something else.

    Args:
        journal: The journal file.
        predicate: Called with the parsed records; truthy stops the wait.
        timeout: Seconds to wait before failing.
        what: What was being waited for, quoted in the failure.

    Returns:
        The records at the moment the predicate held.

    Raises:
        AssertionError: If the timeout expires first.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = _read_journal(journal)
        if predicate(records):
            return records
        await asyncio.sleep(0.1)
    raise AssertionError(
        f"timed out after {timeout}s waiting for {what}; "
        f"journal={_read_journal(journal)}"
    )


@pytest.fixture
async def kill_test_env():
    """A dev server on a known port, so a subprocess can be told where to connect."""
    binary = temporal_cli_path()
    if binary is None:
        skip_without_temporal(
            "that a workflow whose worker is hard-killed mid-run resumes without "
            "re-running the stages that had already committed."
        )
    port = free_port()
    env = await WorkflowEnvironment.start_local(
        dev_server_existing_path=binary, port=port, ui=False
    )
    try:
        yield env, f"127.0.0.1:{port}"
    finally:
        await env.shutdown()


async def test_a_hard_killed_worker_resumes_without_re_running_completed_stages(
    kill_test_env, postgres_database, wired_jobs, tmp_path
):
    env, address = kill_test_env
    journal = tmp_path / "stages.journal"
    await seed_tenants(wired_jobs, TENANT_A)
    document_id = await seed_document(wired_jobs, TENANT_A, sha="a" * 64)
    worker_env = _worker_env(
        address=address,
        journal=journal,
        app_dsn=postgres_database.scratch.app_dsn,
        owner_dsn=postgres_database.scratch.owner_dsn,
    )

    worker_a = _start_worker(worker_env)
    worker_b: subprocess.Popen[bytes] | None = None
    try:
        handle = await env.client.start_workflow(
            INGEST_WORKFLOW,
            IngestParams(tenant_id=TENANT_A, document_id=document_id),
            id=f"ingest:{TENANT_A}:{document_id}",
            task_queue="aegis-default",
            result_type=IngestResult,
        )

        # ── Wait until the kill can only land mid-``embed``. ──────────────────
        await _await_journal(
            journal,
            lambda records: (_SLOW_STAGE, "start") in [(r[0], r[3]) for r in records],
            timeout=60.0,
            what=f"worker A to start the {_SLOW_STAGE!r} stage",
        )
        before_kill = _read_journal(journal)
        completed_before = [r[0] for r in before_kill if r[3] == "done"]
        assert completed_before == ["parse", "chunk", "enrich"], (
            "the run had not reached the intended point when the kill was about to "
            f"happen; completed={completed_before}"
        )
        pid_a = worker_a.pid
        assert {r[1] for r in before_kill} == {pid_a}

        # ── The hard kill. No shutdown hook, no notice to the orchestrator. ──
        os.kill(pid_a, signal.SIGKILL)
        worker_a.wait(timeout=30)
        assert worker_a.returncode == -signal.SIGKILL

        # ── A brand-new process, inheriting nothing. ─────────────────────────
        worker_b = _start_worker(worker_env)
        result = await asyncio.wait_for(handle.result(), timeout=_RESUME_TIMEOUT)
    finally:
        for process in (worker_a, worker_b):
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:  # pragma: no cover - defensive
                    process.kill()

    records = _read_journal(journal)
    pid_b = worker_b.pid
    done = [(stage, pid) for stage, pid, _, event in records if event == "done"]
    started_in_b = {stage for stage, pid, _, _ in records if pid == pid_b}

    # ── The headline: only the in-flight stage re-ran. ───────────────────────
    assert started_in_b == {_SLOW_STAGE, "index", "graph"}, (
        "worker B ran stages that worker A had already committed — the resume "
        f"re-did completed work. B ran: {sorted(started_in_b)}"
    )
    for stage in ("parse", "chunk", "enrich"):
        assert [pid for name, pid in done if name == stage] == [pid_a], (
            f"{stage!r} was completed more than once, or in the wrong process: "
            f"{[entry for entry in done if entry[0] == stage]}"
        )

    # ── And the run genuinely finished, rather than merely stopping. ─────────
    assert result.stages_run == (
        "parse",
        "chunk",
        "enrich",
        "embed",
        "index",
        "graph",
    )
    async with wired_jobs() as session:
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
        job = (
            await session.execute(
                select(JobRun).where(
                    JobRun.workflow_id == f"ingest:{TENANT_A}:{document_id}"
                )
            )
        ).scalar_one()
    assert document.completed_stage == "graph"
    assert document.status is JobStatus.SUCCEEDED
    assert job.status is JobStatus.SUCCEEDED
    # The output of a stage that ran only in the killed process survived the kill,
    # because it was committed in the same transaction as its ``completed_stage`` bump.
    assert document.page_count == 11
    assert document.chunk_count == 42


def test_the_recovery_delay_is_the_heartbeat_timeout_and_not_the_attempt_timeout():
    """The design fact the test above depends on, asserted rather than assumed.

    A ``SIGKILL``ed worker is detected when its heartbeat lapses. If a stage had no
    heartbeat, the orchestrator would wait out ``start_to_close_timeout`` instead — up to
    half an hour for ``parse`` — and the phase's "kill a worker and watch the job get
    reclaimed" demo would look like a hang.
    """
    spec = stage_spec(_SLOW_STAGE)

    assert spec.heartbeat_timeout_seconds == spec.heartbeat_seconds * HEARTBEAT_TIMEOUT_FACTOR
    assert spec.heartbeat_timeout_seconds < spec.timeout_seconds
    # And the test above must be able to outwait it.
    assert spec.heartbeat_timeout_seconds * 2 < _RESUME_TIMEOUT
