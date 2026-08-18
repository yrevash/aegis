"""Shared scaffolding for the job-substrate tests.

Everything here is deliberately *real*: a real tenant, a real ``documents`` row, real
stage handlers that write real columns. The substrate's whole claim is about what happens
to rows in a transaction under a bound tenant scope, and a mock of a handler would test
the mock.

The handlers below are the shape Phase 4's Docling parse will have — they take the
scoped session, do their work, and return the record-layer columns they discovered — so
what these tests exercise is the same contract the real pipeline will be written against.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import pytest
import pytest_asyncio
from aegis.governance import Tenant
from aegis.jobs import Document, JobStatus
from aegis.jobs.scope import (
    reset_activity_session_factory,
    set_activity_session_factory,
)
from aegis.jobs.stages import (
    INGEST_STAGES,
    clear_stage_handlers,
    register_stage_handler,
    stage_spec,
)
from tests import pgsupport

#: The two tenants the job tests use. Far outside anything else the suite seeds, so a
#: stray row cannot be mistaken for one of these.
TENANT_A = 7
TENANT_B = 8


@dataclass
class StageLog:
    """A record of every handler call, for asserting on what actually ran.

    Concurrency is tracked **per task queue**, not globally, and that distinction is the
    whole test. A global "how many handlers were in flight" counter reaches two the
    moment one document's ``chunk`` overlaps another's ``parse`` — which is correct,
    intended behaviour, since those are different queues. Only overlap *within* a queue
    says anything about that queue's slot count.

    Peaks are maxima over the whole run rather than snapshots: a snapshot taken at the
    wrong instant would pass with the limit removed.

    Attributes:
        calls: ``(stage, document_id)`` in call order. A stage that ran twice appears
            twice — which is what an idempotency test needs to see, and what the returned
            outcome alone cannot show.
        peak_by_queue: The greatest number of handlers in flight at once on each queue.
        peak_by_stage: The same, per stage.
    """

    calls: list[tuple[str, int]] = field(default_factory=list)
    peak_by_queue: dict[str, int] = field(default_factory=dict)
    peak_by_stage: dict[str, int] = field(default_factory=dict)
    _live_queue: dict[str, int] = field(default_factory=dict)
    _live_stage: dict[str, int] = field(default_factory=dict)

    def enter(self, stage: str, document_id: int) -> None:
        """Record the start of a handler call."""
        self.calls.append((stage, document_id))
        queue = stage_spec(stage).task_queue
        for live, peak, key in (
            (self._live_queue, self.peak_by_queue, queue),
            (self._live_stage, self.peak_by_stage, stage),
        ):
            live[key] = live.get(key, 0) + 1
            peak[key] = max(peak.get(key, 0), live[key])

    def leave(self, stage: str) -> None:
        """Record the end of a handler call."""
        self._live_queue[stage_spec(stage).task_queue] -= 1
        self._live_stage[stage] -= 1

    def stages(self) -> list[str]:
        """Return just the stage names that ran, in order."""
        return [stage for stage, _ in self.calls]


def register_recording_handlers(
    log: StageLog, *, delays: Mapping[str, float] | None = None
) -> None:
    """Register a real handler for every declared stage, recording each call.

    Each handler writes a genuine ``documents`` column through the substrate's return
    contract — ``page_count`` for ``parse``, ``chunk_count`` for ``chunk`` — so the tests
    can assert that the stage's *output* and its ``completed_stage`` bump landed together
    rather than assuming it.

    Args:
        log: The recorder every handler reports to.
        delays: Optional per-stage sleep, in seconds, used by the concurrency test to
            make an overlap observable if one existed.
    """
    delays = delays or {}

    def make(stage_name: str) -> Any:  # noqa: ANN401 - a StageHandler closure
        async def handler(
            session: Any,  # noqa: ANN401 - AsyncSession
            *,
            tenant_id: int | None,
            document_id: int,
            stage: str,
        ) -> Mapping[str, Any]:
            log.enter(stage, document_id)
            try:
                delay = delays.get(stage, 0.0)
                if delay:
                    await asyncio.sleep(delay)
                if stage == "parse":
                    return {"page_count": 11}
                if stage == "chunk":
                    return {"chunk_count": 42}
                return {}
            finally:
                log.leave(stage)

        handler.__name__ = f"{stage_name}_handler"
        return handler

    for spec in INGEST_STAGES:
        register_stage_handler(spec.name, make(spec.name))


@pytest.fixture
def stage_log():
    """A fresh :class:`StageLog` with the registry cleaned before and after."""
    clear_stage_handlers()
    log = StageLog()
    yield log
    clear_stage_handlers()


@pytest_asyncio.fixture
async def wired_jobs(db):
    """Bind the serving-engine session factory into ``aegis.jobs.scope`` for one test.

    The **serving** factory, not the owner's: it connects as the ``NOSUPERUSER
    NOBYPASSRLS`` scratch role, which is the only reason the tenant-isolation assertions
    in these tests can fail. Wiring the admin engine here would make every one of them
    vacuous while leaving them green.

    Yields:
        The session factory, for a test that wants to read rows back.
    """
    set_activity_session_factory(db)
    try:
        yield db
    finally:
        reset_activity_session_factory()


async def seed_tenants(db, *tenant_ids: int) -> None:
    """Create the named tenants, so the job rows' foreign keys resolve."""
    async with db() as session:
        await pgsupport.seed(
            session, *[Tenant(id=tid, name=f"tenant-{tid}") for tid in tenant_ids]
        )
        await session.commit()


async def seed_document(
    db, tenant_id: int, *, sha: str, filename: str = "filing.pdf"
) -> int:
    """Insert one uploaded-but-unparsed document and return its id.

    Written through the serving role with the scope deliberately unbound: the
    ``tenant_isolation`` predicate fails open for an unscoped request (documented on
    ``_TENANT_ISOLATION_PREDICATE``), which is what lets a fixture seed two tenants over
    one connection. The assertions that matter all bind a scope.
    """
    async with db() as session:
        document = Document(
            tenant_id=tenant_id,
            filename=filename,
            content_sha256=sha,
            mime_type="application/pdf",
            size_bytes=2048,
            status=JobStatus.PENDING,
        )
        session.add(document)
        await session.commit()
        return document.id


# ─────────────────────────────────────────────────────────────────────────────
# A real Temporal server, or a skip that says exactly what went unverified
# ─────────────────────────────────────────────────────────────────────────────

#: Environment variable naming the Temporal CLI binary. The dev server is a single
#: self-contained executable; the SDK can download one, but a test that reaches the
#: network is a test that fails on a locked-down machine for a reason unrelated to the
#: code, so the binary is located rather than fetched.
CLI_PATH_ENV = "AEGIS_TEMPORAL_CLI"

#: Set to ``1`` to turn "no Temporal binary" from a skip into a hard failure, mirroring
#: ``AEGIS_REQUIRE_PG_TESTS``. A durability test that silently skips is indistinguishable
#: from one that passes, which is its own failure mode.
REQUIRE_TEMPORAL_ENV = "AEGIS_REQUIRE_TEMPORAL_TESTS"


def temporal_cli_path() -> str | None:
    """Return a usable Temporal CLI binary, or ``None``.

    Looks at ``AEGIS_TEMPORAL_CLI``, then the location the phase's spike left it, then
    ``PATH``.

    Returns:
        An absolute path to an executable, or ``None`` if none was found.
    """
    configured = os.environ.get(CLI_PATH_ENV)
    if configured and os.access(configured, os.X_OK):
        return configured
    if os.access("/tmp/temporal", os.X_OK):
        return "/tmp/temporal"
    return shutil.which("temporal")


def skip_without_temporal(unverified: str) -> None:
    """Skip — or fail — when no Temporal server can be started.

    Args:
        unverified: What this test would have proved, named explicitly so a skipped run
            does not read as a green one.

    Raises:
        Failed: Via ``pytest.fail`` when ``AEGIS_REQUIRE_TEMPORAL_TESTS=1``.
        Skipped: Via ``pytest.skip`` otherwise.
    """
    message = (
        f"NOT verified against a real orchestrator: {unverified} No Temporal CLI binary "
        f"was found (set {CLI_PATH_ENV}, put one at /tmp/temporal, or install `temporal` "
        f"on PATH). Set {REQUIRE_TEMPORAL_ENV}=1 to make this a failure instead of a skip."
    )
    if os.environ.get(REQUIRE_TEMPORAL_ENV) == "1":
        pytest.fail(message)
    pytest.skip(message)


def free_port() -> int:
    """Return a TCP port that is free right now.

    Bind-and-release rather than a fixed port: the suite may run alongside a developer's
    own dev server on 7233, and a port collision would surface as an unrelated failure.

    Returns:
        A port number no listener currently holds.
    """
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])
