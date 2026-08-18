"""D8's table summaries on the real stage: what lands on a row, and what it costs.

Task 4.10 makes two promises, and only one of them is about retrieval quality.

The first is that **a table is a first-class object**: its own chunk, carrying the shape
TableFormer reported and the caption it was printed with, with a generated sentence or two
in front of the grid — *in front of*, never instead of, because the numbers are what most
questions about a table are actually asking for.

The second is a promise about money, and it is the one that fails silently. A 200-page
document with eighty tables is real spend against $100 of credit, and every naive version
of this feature bills it again on the next upload and again on every 4.13 re-index. So the
assertions here are call counts taken from a spy at the seam, not timings and not log
lines: *how many completions did this ingest actually buy?*

Everything else is the real thing. Real PostgreSQL, real ``chunks`` and ``table_summaries``
rows written by the real handler through the real stage-runner activity under a bound
tenant scope, and — where the table has to be a real one — the real Docling parse of the
15-page fixture. The only double is the completer, because a live gateway cannot answer
the question these tests ask and would send the corpus to a provider to fail to answer it.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.ingestion import BBox, BlockKind, OcrDecision, ParsedBlock, ParsedDocument, ParsedPage
from aegis.ingestion.tables import table_digest
from aegis.jobs import Chunk, Document, JobStatus, TableSummary
from sqlalchemy import func, select

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.artifacts import dumps_parsed
from app.ingestion.stages import chunk_stage
from app.jobs.activities import run_stage
from app.jobs.flows.contracts import StageInput

from .conftest import FIXTURE

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A = 11
_USER_B = 22

#: A table comfortably above the default threshold, written out in full rather than
#: generated so the exact bytes the digest is taken over are visible.
_BIG_TABLE = """Table 2: BLEU scores on newstest2014.

| Model       | EN-DE   | EN-FR   |
|-------------|---------|---------|
| ByteNet     | 23.75   |         |
| GNMT + RL   | 24.6    | 39.92   |
| ConvS2S     | 25.16   | 40.46   |
| Transformer | 28.4    | 41.8    |"""

#: Its shape as TableFormer would report it: the header row plus four data rows.
_BIG_SHAPE = (5, 3)

#: The case D8's cost note names: a 2x3 grid whose Markdown a reader follows unaided.
_SMALL_TABLE = """Table 9: Vocabulary sizes.

| Tokens | Types | Merges |
|--------|-------|--------|
| 37000  | 32000 | 32000  |"""

#: Two rows, three columns, six cells — under every arm of the default threshold.
_SMALL_SHAPE = (2, 3)


# ─────────────────────────────────────────────────────────────────────────────
# Scaffolding
# ─────────────────────────────────────────────────────────────────────────────


def _headers(*, tenant_id: int, username: str, user_id: int) -> dict[str, str]:
    """A tenant-admin bearer for one tenant."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants() -> None:
    """Two tenants with an admin and a budget each."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            Tenant(id=_TENANT_B, name="Tenant B"),
            User(id=_USER_A, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=_USER_B, username="b-admin", role=Role.ADMIN, tenant_id=_TENANT_B),
            Budget(
                tenant_id=_TENANT_A,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_A,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
            Budget(
                tenant_id=_TENANT_B,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_B,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


def _synthetic(
    *tables: tuple[str, tuple[int, int]], name: str = "filing.pdf"
) -> ParsedDocument:
    """Build a one-page parse holding a heading, a paragraph and the given tables.

    Hand-built rather than parsed because these tests are about the *cache*, and the cache
    is keyed on table content: two documents that genuinely share a table is the case
    worth proving, and no two of the phase's fixtures do.

    Args:
        *tables: ``(markdown, (rows, cols))`` per table, in reading order. The shape is
            stated rather than counted out of the pipes, exactly as the real path takes
            it from TableFormer rather than from the rendering.
        name: The source file name recorded on the parse.

    Returns:
        A parse the ``chunk`` stage reads exactly as it reads a real one.
    """
    blocks: list[ParsedBlock] = [
        ParsedBlock(
            kind=BlockKind.HEADING,
            text="Results",
            page_no=1,
            bbox=BBox(72.0, 90.0, 540.0, 112.0),
            level=1,
        ),
        ParsedBlock(
            kind=BlockKind.TEXT,
            text="The model outperforms every previously reported result on this task.",
            page_no=1,
            bbox=BBox(72.0, 130.0, 540.0, 160.0),
            heading_path=("Results",),
        ),
    ]
    top = 200.0
    for grid, shape in tables:
        blocks.append(
            ParsedBlock(
                kind=BlockKind.TABLE,
                text=grid,
                page_no=1,
                bbox=BBox(72.0, top, 540.0, top + 120.0),
                heading_path=("Results",),
                table_shape=shape,
            )
        )
        top += 140.0
    return ParsedDocument(
        source_name=name,
        pages=(
            ParsedPage(
                page_no=1, width=612.0, height=792.0, char_count=1800, has_text_layer=True
            ),
        ),
        blocks=tuple(blocks),
        ocr=OcrDecision(enabled=False, reason="synthetic fixture"),
        parser="test-parser 1.0",
    )


async def _seed_document(
    store, *, document_id: int, tenant_id: int, sha: str, parsed: ParsedDocument
) -> int:
    """Write a ``documents`` row and its parse artifact, ready for the ``chunk`` stage.

    The upload route is not used here because it deduplicates on ``content_sha256`` per
    tenant, and the point of these tests is two *different* documents that happen to hold
    the same table.

    Args:
        store: The temporary document store the handlers were given.
        document_id: The id to write.
        tenant_id: The owning tenant.
        sha: The content digest — different per document, unlike the table inside it.
        parsed: The parse to store as the artifact.

    Returns:
        The document id.
    """
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Document(
                id=document_id,
                tenant_id=tenant_id,
                filename=parsed.source_name,
                content_sha256=sha,
                mime_type="application/pdf",
                size_bytes=4096,
                title="Results",
                status=JobStatus.PENDING,
            ),
        )
        await session.commit()
    store.put_artifact(tenant_id=tenant_id, sha256=sha, payload=dumps_parsed(parsed))
    return document_id


async def _run_chunk(*, tenant_id: int, document_id: int) -> None:
    """Run the ``chunk`` stage through the real activity, exactly as the workflow would."""
    await run_stage(
        StageInput(
            tenant_id=tenant_id,
            workflow_id=f"ingest:{tenant_id}:{document_id}",
            document_id=document_id,
            stage="chunk",
        )
    )


async def _chunks(document_id: int, *, tenant_id: int) -> list[Chunk]:
    """Read a document's chunks back over the serving role, in insertion order."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return list(
            (
                await session.execute(
                    select(Chunk).where(Chunk.document_id == document_id).order_by(Chunk.id)
                )
            )
            .scalars()
            .all()
        )


async def _cached_summaries(tenant_id: int) -> list[TableSummary]:
    """Read the tenant's table-summary cache rows."""
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, tenant_id)
        return list((await session.execute(select(TableSummary))).scalars().all())


def _tables(chunks: list[Chunk]) -> list[Chunk]:
    """Return only the chunk rows that are tables."""
    return [chunk for chunk in chunks if (chunk.meta or {}).get("table")]


# ─────────────────────────────────────────────────────────────────────────────
# A real table, off a real parse, on a real row
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_real_pdfs_table_becomes_a_row_carrying_its_shape_caption_and_summary(
    client, db, wired, store, temporal, parsed_artifact, summariser
) -> None:
    """The 15-page paper's four tables, as four chunk rows a consumer can recognise.

    Everything asserted here is read back out of PostgreSQL after the real handler ran:
    the shape TableFormer reported, the caption the table was printed with, the generated
    summary, and the grid still sitting underneath it.
    """
    await _seed_tenants()
    data, payload = parsed_artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        data={"doc_type": "research paper", "doc_date": "2017-06-12"},
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    store.put_artifact(
        tenant_id=_TENANT_A, sha256=body["content_sha256"], payload=payload
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=body["document_id"])

    chunks = await _chunks(body["document_id"], tenant_id=_TENANT_A)
    tables = _tables(chunks)
    assert len(tables) == 4, "the fixture has four tables; the rows say otherwise"
    assert summariser.count == 4, "one call per table, and not one per chunk"

    shapes = [(row.meta["table"]["rows"], row.meta["table"]["cols"]) for row in tables]
    assert shapes == [(5, 4), (12, 5), (21, 13), (13, 3)]

    bleu = next(row for row in tables if row.meta["table"]["caption"].startswith("Table 2:"))
    assert bleu.meta["table"]["summarised"] is True
    assert bleu.meta["table"]["reason"] is None
    assert bleu.meta["table"]["digest"] == table_digest(
        bleu.content.split("\n\n", 1)[1]
    )
    # The summary is in front of the grid, in the text that is embedded and FTS-indexed.
    assert bleu.content.startswith(bleu.meta["table"]["summary"] + "\n\n")
    # …and the grid is still there. This is the D8 correction: the summary is additional
    # text, not a replacement, because "41.8" is the answer and no sentence about the
    # table contains it.
    assert "| Transformer" in bleu.content
    assert "41.8" in bleu.content

    # A prose chunk is still prose: it carries a null table block rather than none at all.
    prose = [chunk for chunk in chunks if not (chunk.meta or {}).get("table")]
    assert prose, "the document is not all tables"
    assert all("table" in (chunk.meta or {}) for chunk in prose)


# ─────────────────────────────────────────────────────────────────────────────
# The cost control, which is the point
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_same_table_in_two_documents_costs_exactly_one_model_call(
    client, db, wired, store, temporal, summariser
) -> None:
    """The cache's whole reason to exist, asserted on the call count and nowhere else.

    Two genuinely different documents — different bytes, different rows, different ids —
    holding one identical table. The second ingest must find the sentence the first one
    paid for, and must write the *same* sentence onto its own chunk.
    """
    await _seed_tenants()
    first = await _seed_document(
        store,
        document_id=101,
        tenant_id=_TENANT_A,
        sha="a" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE), name="q1-results.pdf"),
    )
    second = await _seed_document(
        store,
        document_id=102,
        tenant_id=_TENANT_A,
        sha="b" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE), name="q2-results.pdf"),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=first)
    assert summariser.count == 1

    await _run_chunk(tenant_id=_TENANT_A, document_id=second)
    assert summariser.count == 1, (
        "the second document re-bought a summary for a table the platform already "
        "describes; on an eighty-table corpus that is the whole bill, twice"
    )

    one = _tables(await _chunks(first, tenant_id=_TENANT_A))[0]
    two = _tables(await _chunks(second, tenant_id=_TENANT_A))[0]
    assert one.meta["table"]["summary"] == two.meta["table"]["summary"]
    assert one.meta["table"]["digest"] == two.meta["table"]["digest"]

    cached = await _cached_summaries(_TENANT_A)
    assert len(cached) == 1
    assert cached[0].digest == table_digest(_BIG_TABLE)
    assert (cached[0].row_count, cached[0].col_count) == _BIG_SHAPE
    assert cached[0].model_role == "cheap"


async def test_the_same_table_twice_in_one_document_costs_one_call(
    client, db, wired, store, temporal, summariser
) -> None:
    """A repeated schedule or rate card is the common case in the PDFs D8 is about."""
    await _seed_tenants()
    document = await _seed_document(
        store,
        document_id=103,
        tenant_id=_TENANT_A,
        sha="c" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE), (_BIG_TABLE, _BIG_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)

    tables = _tables(await _chunks(document, tenant_id=_TENANT_A))
    assert len(tables) == 2
    assert summariser.count == 1
    assert tables[0].meta["table"]["summary"] == tables[1].meta["table"]["summary"]


async def test_a_table_below_the_threshold_is_never_sent_to_the_model(
    client, db, wired, store, temporal, summariser
) -> None:
    """Zero calls, and the row says which rule declined it.

    "No summary because it is 2x3" and "no summary because the gateway failed" look
    identical on a row unless the reason is written down, and only one of them is a bug.
    """
    await _seed_tenants()
    document = await _seed_document(
        store,
        document_id=104,
        tenant_id=_TENANT_A,
        sha="d" * 64,
        parsed=_synthetic((_SMALL_TABLE, _SMALL_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)

    assert summariser.count == 0, "a 2x3 table bought a model call"
    assert await _cached_summaries(_TENANT_A) == []
    table = _tables(await _chunks(document, tenant_id=_TENANT_A))[0]
    assert table.meta["table"]["summarised"] is False
    assert table.meta["table"]["summary"] is None
    assert "below the summary threshold" in table.meta["table"]["reason"]
    # It is still a table, still indexed, and still holds its own numbers.
    assert table.meta["table"]["rows"] == 2
    assert table.content == _SMALL_TABLE
    assert "37000" in table.content


async def test_running_the_chunk_stage_twice_leaves_one_set_of_chunks_and_buys_nothing(
    client, db, wired, store, temporal, summariser
) -> None:
    """4.5's idempotency contract, extended to the bill task 4.10 introduced.

    The second run goes through the handler directly rather than through
    :func:`app.jobs.activities.run_stage`, because the substrate short-circuits a stage
    that has already committed — that would prove the replay guard and say nothing about
    the write, or about the spend, underneath it.
    """
    await _seed_tenants()
    document = await _seed_document(
        store,
        document_id=105,
        tenant_id=_TENANT_A,
        sha="e" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE), (_SMALL_TABLE, _SMALL_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)
    after_first = await _chunks(document, tenant_id=_TENANT_A)
    assert after_first
    assert summariser.count == 1

    async with db() as session:
        await set_tenant_scope(session, _TENANT_A)
        await chunk_stage(
            session, tenant_id=_TENANT_A, document_id=document, stage="chunk"
        )
        await session.commit()

    after_second = await _chunks(document, tenant_id=_TENANT_A)
    assert len(after_second) == len(after_first)
    assert [chunk.content for chunk in after_second] == [
        chunk.content for chunk in after_first
    ]
    assert summariser.count == 1, (
        "re-running the chunk stage re-bought its summaries; a 4.13 re-index would then "
        "cost a full ingest's worth of model calls for tables that have not changed"
    )
    assert len(await _cached_summaries(_TENANT_A)) == 1
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        total = (await session.execute(select(func.count(Chunk.id)))).scalar_one()
    assert total == len(after_first)


async def test_one_tenants_cached_summary_is_not_served_to_another(
    client, db, wired, store, temporal, summariser
) -> None:
    """The cache is per tenant, and the second tenant pays for its own call.

    This is a deliberate cost, and it is the reason ``table_summaries`` carries a
    ``tenant_id`` at all: a shared cache is the easiest place for a boundary to be argued
    away ("the input was identical, so the output is safe"), and that rule survives
    exactly until what is cached is widened.
    """
    await _seed_tenants()
    mine = await _seed_document(
        store,
        document_id=106,
        tenant_id=_TENANT_A,
        sha="f" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
    )
    theirs = await _seed_document(
        store,
        document_id=107,
        tenant_id=_TENANT_B,
        sha="f" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=mine)
    await _run_chunk(tenant_id=_TENANT_B, document_id=theirs)

    assert summariser.count == 2
    assert len(await _cached_summaries(_TENANT_A)) == 1
    assert len(await _cached_summaries(_TENANT_B)) == 1


# ─────────────────────────────────────────────────────────────────────────────
# What happens when the model does not answer
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_failed_summary_leaves_the_grid_indexed_and_says_what_went_wrong(
    client, db, wired, store, temporal, monkeypatch
) -> None:
    """A gateway failure degrades retrieval on one chunk. It must not fail the ingest.

    The grid is what the document actually contains, and it is still chunked, still
    written, still full-text indexed and still citable. What is lost is the sentence that
    would have made it *findable* from a question — so the loss is recorded on the row
    rather than left to look like a table nobody thought worth summarising.
    """
    await _seed_tenants()

    async def _broken(role, messages, **kwargs):
        raise RuntimeError("gateway returned 503")

    monkeypatch.setattr("app.retrieval.gateway.default_complete", lambda: _broken)
    document = await _seed_document(
        store,
        document_id=108,
        tenant_id=_TENANT_A,
        sha="1" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)

    table = _tables(await _chunks(document, tenant_id=_TENANT_A))[0]
    assert table.meta["table"]["summarised"] is False
    assert "gateway returned 503" in table.meta["table"]["reason"]
    assert table.content == _BIG_TABLE
    assert await _cached_summaries(_TENANT_A) == [], (
        "a failure must not be cached; the next ingest has to be allowed to try again"
    )


async def test_a_summary_the_model_returns_empty_is_recorded_as_an_absence(
    client, db, wired, store, temporal, monkeypatch
) -> None:
    """Nothing here manufactures a sentence out of the grid to fill the gap."""
    await _seed_tenants()

    class _Blank:
        content = "   "

    async def _empty(role, messages, **kwargs):
        return _Blank()

    monkeypatch.setattr("app.retrieval.gateway.default_complete", lambda: _empty)
    document = await _seed_document(
        store,
        document_id=109,
        tenant_id=_TENANT_A,
        sha="2" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)

    table = _tables(await _chunks(document, tenant_id=_TENANT_A))[0]
    assert table.meta["table"]["summarised"] is False
    assert table.meta["table"]["reason"] == "the model returned no usable summary"
    assert table.content == _BIG_TABLE


# ─────────────────────────────────────────────────────────────────────────────
# The threshold is configuration, not a constant
# ─────────────────────────────────────────────────────────────────────────────


async def test_lowering_the_threshold_makes_the_small_table_worth_a_call(
    client, db, wired, store, temporal, summariser
) -> None:
    """The right cut-off is a property of a corpus, so a deployment can move it."""
    from app.config import get_settings

    settings = get_settings()
    settings.table_summary_min_rows = 2
    settings.table_summary_min_cols = 2
    settings.table_summary_min_cells = 4
    try:
        await _seed_tenants()
        document = await _seed_document(
            store,
            document_id=110,
            tenant_id=_TENANT_A,
            sha="3" * 64,
            parsed=_synthetic((_SMALL_TABLE, _SMALL_SHAPE)),
        )

        await _run_chunk(tenant_id=_TENANT_A, document_id=document)
    finally:
        settings.table_summary_min_rows = 3
        settings.table_summary_min_cols = 3
        settings.table_summary_min_cells = 12

    assert summariser.count == 1
    table = _tables(await _chunks(document, tenant_id=_TENANT_A))[0]
    assert table.meta["table"]["summarised"] is True
    assert table.content.endswith(_SMALL_TABLE)


async def test_summaries_can_be_switched_off_entirely_without_losing_the_tables(
    client, db, wired, store, temporal, summariser
) -> None:
    """Off is honest degradation: the grids are still chunked, indexed and citable."""
    from app.config import get_settings

    settings = get_settings()
    settings.table_summary_enabled = False
    try:
        await _seed_tenants()
        document = await _seed_document(
            store,
            document_id=111,
            tenant_id=_TENANT_A,
            sha="4" * 64,
            parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
        )

        await _run_chunk(tenant_id=_TENANT_A, document_id=document)
    finally:
        settings.table_summary_enabled = True

    assert summariser.count == 0
    table = _tables(await _chunks(document, tenant_id=_TENANT_A))[0]
    assert table.content == _BIG_TABLE
    assert table.meta["table"]["rows"] == 5
    assert table.meta["table"]["reason"] == "table summaries are disabled"


# ─────────────────────────────────────────────────────────────────────────────
# The grid is still reachable through the lexical arm after summarisation
# ─────────────────────────────────────────────────────────────────────────────


async def test_a_number_only_the_grid_contains_is_still_searchable_after_summarisation(
    client, db, wired, store, temporal, summariser
) -> None:
    """The D8 correction, proved through PostgreSQL's own generated ``search_vector``.

    ``39.92`` appears in the table and in nothing else — not in the caption, and not in
    any sentence a model would write about it. If the summary had replaced the grid, this
    query would return nothing, and the document's own numbers would have become
    unreachable at the exact moment the feature was supposed to make them findable.
    """
    await _seed_tenants()
    document = await _seed_document(
        store,
        document_id=112,
        tenant_id=_TENANT_A,
        sha="5" * 64,
        parsed=_synthetic((_BIG_TABLE, _BIG_SHAPE)),
    )

    await _run_chunk(tenant_id=_TENANT_A, document_id=document)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        hit = (
            await session.execute(
                select(Chunk.id).where(
                    Chunk.search_vector.op("@@")(func.plainto_tsquery("english", "39.92"))
                )
            )
        ).scalar_one_or_none()

    assert hit is not None, "the grid's own numbers left the full-text index"
