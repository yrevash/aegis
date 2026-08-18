"""Shared scaffolding for the ingestion tests — a real store, real handlers, one parse.

Everything here is the real thing pointed somewhere temporary. The document store is the
shipped :class:`~app.ingestion.store.DocumentStore` on a ``tmp_path``; the stage handlers
are the ones the worker registers in production; the session factory bound into the
substrate is the suite's **serving** one, which connects as the ``NOSUPERUSER
NOBYPASSRLS`` scratch role — the only reason the isolation assertions in these tests can
fail at all.

The one concession to wall clock is :func:`parsed_artifact`: Docling takes ~7 s on the
15-page fixture, and the tests that are about the *chunk* stage do not need it re-derived
for each of them. What they are handed is genuine parser output — the same JSON the
``parse`` stage writes and the ``chunk`` stage reads in production — so only the
repetition is skipped, not the reality.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
import pytest_asyncio
from aegis.jobs.scope import (
    reset_activity_session_factory,
    set_activity_session_factory,
)
from aegis.jobs.stages import clear_stage_handlers

from app.config import get_settings
from app.ingestion.stages import (
    IngestDependencies,
    register_ingest_handlers,
    reset_ingest_dependencies,
    set_ingest_dependencies,
)
from app.ingestion.store import DocumentStore
from app.jobs.client import reset_temporal_client, set_temporal_client

#: The fixture these tests ingest. Single-column, 15 pages, ~0.45 s/page — the *control*
#: document from ``tests/fixtures/pdfs/README.md``, whose reading order Docling is known
#: to get right, so a failure here is a failure of this code rather than of the parser.
FIXTURE = "transformer-single-column.pdf"

PDF_DIR = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "pdfs"


def fixture_pdf(name: str = FIXTURE) -> Path:
    """Return a fixture PDF, skipping when it has not been downloaded.

    The PDFs are deliberately not committed (see ``tests/fixtures/pdfs/README.md``), so a
    machine where ``fetch.sh`` has not been run skips with a message naming the file
    rather than failing for a reason unrelated to the code.

    Args:
        name: The file name inside ``tests/fixtures/pdfs``.

    Returns:
        The path to the PDF.
    """
    path = PDF_DIR / name
    if not path.exists():
        pytest.skip(f"{name} is not downloaded — run {PDF_DIR}/fetch.sh")
    return path


class FakeTemporalClient:
    """Records every workflow the upload path starts, so no server is needed.

    Installed through :func:`app.jobs.client.set_temporal_client` — the shipped seam the
    durability tests already use — so the route runs unmodified. The stages themselves are
    driven directly through :func:`app.jobs.activities.run_stage`, which is how the
    substrate's own tests drive them: what is under test here is the handlers, not the
    orchestrator that would call them.
    """

    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_workflow(self, *args: object, **kwargs: object) -> object:
        """Record a start and return something handle-shaped."""
        self.started.append(str(kwargs.get("id", "")))
        return object()


@pytest.fixture
def temporal():
    """Install the recording Temporal double for one test, then clear it."""
    client = FakeTemporalClient()
    set_temporal_client(client)  # type: ignore[arg-type] - a deliberate test double
    try:
        yield client
    finally:
        reset_temporal_client()


@dataclass(frozen=True)
class _Completion:
    """The one field :class:`aegis.retrieval.protocols.CompletionResult` requires."""

    content: str


class SummarySpy:
    """The model gateway, replaced by something that counts.

    D8's table summaries are the only money the ``chunk`` stage spends, and the whole of
    task 4.10's cost control — the size threshold and the ``table_summaries`` content-hash
    cache — is a claim about **how many calls happen**. That is not observable from a
    timing, from a log line or from the rows: it has to be counted at the seam, which is
    what this is.

    The reply is derived from the prompt rather than constant, so a test can also assert
    that the summary written onto a chunk is the summary generated *for that table* and
    not for the one before it.
    """

    def __init__(self) -> None:
        self.calls: list[list[dict[str, object]]] = []

    async def __call__(
        self,
        role: object,
        messages: list[dict[str, object]],
        *,
        temperature: float = 0.0,
        response_format: dict[str, object] | None = None,
    ) -> _Completion:
        """Record the call and return a deterministic, table-specific sentence."""
        self.calls.append(messages)
        prompt = str(messages[-1].get("content", ""))
        caption = next(
            (
                line.removeprefix("Caption:").strip()
                for line in prompt.splitlines()
                if line.startswith("Caption:")
            ),
            "",
        )
        shape = next(
            (
                line.removeprefix("Shape:").strip()
                for line in prompt.splitlines()
                if line.startswith("Shape:")
            ),
            "",
        )
        return _Completion(f"This table ({shape}) reports {caption or 'unnamed results'}.")

    @property
    def count(self) -> int:
        """How many completions were asked for."""
        return len(self.calls)


@pytest.fixture
def summariser() -> SummarySpy:
    """The spy every table summary in this package is generated by."""
    return SummarySpy()


@pytest.fixture(autouse=True)
def _no_live_gateway(monkeypatch, summariser):
    """Make it impossible for a test in this package to reach the real model gateway.

    ``chunk`` resolves :func:`app.retrieval.gateway.default_complete` lazily when a
    document has tables and the caller injected no completer — which is most of the tests
    here, and every fixture in ``tests/fixtures/pdfs`` has tables. Patching the resolver
    rather than each :class:`IngestDependencies` construction means a test added later
    cannot forget: it gets the spy whether it asked for one or not.
    """
    monkeypatch.setattr(
        "app.retrieval.gateway.default_complete", lambda: summariser, raising=True
    )


@pytest.fixture
def store(tmp_path, monkeypatch):
    """Point the document store at a temporary directory, for the route and the handlers.

    Yields:
        The :class:`~app.ingestion.store.DocumentStore` everything will write through, so
        a test can assert on where the bytes actually landed.
    """
    root = tmp_path / "document_storage"
    monkeypatch.setattr(get_settings(), "document_store_path", str(root), raising=False)
    store = DocumentStore(root)
    set_ingest_dependencies(IngestDependencies(store=store))
    try:
        yield store
    finally:
        reset_ingest_dependencies()


@pytest_asyncio.fixture
async def wired(db):
    """Register the real ingest handlers against the **serving** session factory.

    Yields:
        The session factory, for a test that wants to read rows back.
    """
    clear_stage_handlers()
    register_ingest_handlers()
    set_activity_session_factory(db)
    try:
        yield db
    finally:
        reset_activity_session_factory()
        clear_stage_handlers()


@pytest.fixture(scope="session")
def parsed_artifact():
    """Parse the fixture once for the whole session and return ``(bytes, artifact)``.

    Returns:
        The PDF's bytes and the serialised parse the ``chunk`` stage reads.
    """
    from aegis.ingestion import parse_pdf

    from app.ingestion.artifacts import dumps_parsed

    path = fixture_pdf()
    return path.read_bytes(), dumps_parsed(parse_pdf(path))
