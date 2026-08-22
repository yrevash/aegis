"""The six ingest stage handlers — the work Phase 3's substrate calls.

:mod:`aegis.jobs.stages` declares *what* the stages are; :mod:`app.jobs.activities` owns
the transaction, the tenant scope, the replay short-circuit and the ``completed_stage``
bump. What is left is the domain work, and it is here:

===========  ==============  ==========================================================
Stage        Queue           What it does
===========  ==============  ==========================================================
``parse``    ``aegis-cpu``   Docling reads the stored bytes into a structured tree, and
                             the tree is written beside them as the parse artifact.
                             Returns ``page_count``, the derived ``title`` and the
                             D-parse ``parse_confidence``.
``chunk``    ``aegis-...``   Packs the parsed sections into ``chunks`` rows, with the
                             D7 prefix, the page/bbox spans and the tenant on every row.
                             A table becomes its own row, carrying its shape and caption,
                             and — above a configured size — a natural-language summary
                             written in front of the grid (D8). Returns ``chunk_count``.
``enrich``   ``aegis-...``   Folds that prefix into the text that is embedded and
                             full-text indexed — one guarded ``UPDATE``.
``embed``    ``aegis-io``    Embeds each chunk and writes ``chunks.embedding``, the
                             durable source-of-record vector.
``index``    ``aegis-...``   Publishes the chunks to the configured knowledge backend,
                             which is what makes them reachable by the dense arm.
``graph``    ``aegis-cpu``   Extracts entities and relations, records them on the chunk
                             so the graph an ingest built is a fact about rows we own,
                             and projects them into the durable knowledge graph the
                             product shows (:mod:`app.ingestion.graph_projection`).
===========  ==============  ==========================================================

Three rules every handler here obeys, and the reason each is not negotiable:

**Write on the session you were given.** A second session is a second transaction, and a
second transaction is where the stage-commit rule dies: the substrate commits the
handler's rows and the ``completed_stage`` bump together precisely so that a stage which
"finished" but whose output rolled back cannot exist.

**Never a bare insert.** Every write here is a delete-then-insert inside the transaction,
or an ``UPDATE`` guarded so a second run is a no-op. The substrate already guarantees a
handler runs at most once per *committed* stage; what that guarantee cannot cover is an
attempt that wrote its rows, was retried for an unrelated reason, and succeeded on the
second pass. One SQL clause removes the question.

**Return columns, do not write them.** A handler returns the ``documents`` values its work
discovered and the substrate applies them; the allow-list in :mod:`app.jobs.activities`
is what stops a handler claiming progress or moving a document between tenants.

A note on blocking work. ``parse`` is seconds-to-minutes of CPU inside Docling and
``graph`` can be seconds of spaCy; both go through :func:`asyncio.to_thread` so the
activity's heartbeat keeps beating. Without that the orchestrator would conclude the
worker had died in the middle of the one stage that is most expensive to redo.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aegis.ingestion import ParsedDocument, parse_pdf
from aegis.ingestion.blocks import BlockKind
from aegis.ingestion.quality import LOW_CONFIDENCE
from aegis.ingestion.tables import TableSummaryPolicy
from aegis.jobs.facts import report_stage_facts
from aegis.jobs.models import Chunk, Document
from aegis.jobs.stages import INGEST_STAGES, register_stage_handler
from aegis.retrieval.chunker import DocumentContext, SectionChunk, chunk_sections
from aegis.retrieval.graph_extract import Extractor, build_extractor
from aegis.retrieval.models import Chunk as RetrievalChunk
from aegis.retrieval.protocols import CompleteFn, EmbedFn
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    chunk_source_id,
    tenant_metadata_value,
)
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

from app.config import get_settings
from app.ingestion.artifacts import dumps_parsed, loads_parsed
from app.ingestion.chunk_kv import ChunkKVRow, publish_chunk_kv
from app.ingestion.graph_projection import (
    GraphProjectionError,
    ProjectionResult,
    normalised_label,
    project_document_graph,
    tagged_source,
)
from app.ingestion.graph_vectors import GraphVectorResult
from app.ingestion.store import DocumentStore, DocumentStoreProtocol
from app.ingestion.tables import TableSummaryReport, summarise_document_tables

logger = logging.getLogger(__name__)

__all__ = [
    "IngestDependencies",
    "chunk_stage",
    "embed_stage",
    "enrich_stage",
    "graph_stage",
    "index_stage",
    "parse_stage",
    "register_ingest_handlers",
    "reset_ingest_dependencies",
    "set_ingest_dependencies",
]

#: How many chunk texts go into one embedding call. The provider bills per token either
#: way, so the batch size is about round-trips and about not building one request a
#: 200-page document makes megabytes long.
_EMBED_BATCH = 64

#: The ``chunks.embedding`` value written by the ``chunk`` stage, before ``embed`` runs.
#:
#: The column is ``NOT NULL`` (it landed that way in task 4.6, and a live database cannot
#: be relaxed to nullable by the additive reconciler), so the row needs *some* value at
#: insert time — and this is the only one that cannot be mistaken for an embedding. A
#: zero vector of the right width would be a valid-looking vector pointing nowhere, which
#: the mirror and every cosine comparison would happily consume; an empty list is off-dim
#: by construction, so it is skipped rather than believed.
_UNEMBEDDED: list[float] = []


# ─────────────────────────────────────────────────────────────────────────────
# Injected collaborators
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class IngestDependencies:
    """The collaborators the handlers reach outside the database for.

    Injected rather than imported at the call site so a test can drive a real stage
    against a real PostgreSQL without a model gateway, a vector store or a graph — and so
    the production wiring is a single, visible assignment in the worker bootstrap rather
    than five lazy imports scattered through the handlers.

    Attributes:
        store: Where the uploaded bytes and the parse artifact live.
        embed: The embedding function. ``None`` resolves the platform's gateway on first
            use (:func:`app.retrieval.gateway.default_embed`), which is what a deployed
            worker gets.
        complete: The chat-completion function, used by the ``chunk`` stage for D8's
            per-table natural-language summaries and by nothing else. ``None`` resolves
            :func:`app.retrieval.gateway.default_complete` on first use. A test that
            wants to assert *how many* summaries an ingest paid for injects a spy here —
            which is the only honest way to test a cache.
        publish: Coroutine taking the document's chunks and writing them into the
            knowledge backend the platform searches. ``None`` resolves the process
            retriever's backend on first use.
        extractor: The entity/relation extractor. ``None`` builds the one this deployment
            configured (see :meth:`resolve_extractor`), whose ``name`` is honest about
            which one actually ran.
        project: Coroutine taking a document's extraction and writing it into the durable
            knowledge graph, returning what verifiably landed. ``None`` uses
            :func:`app.ingestion.graph_projection.project_document_graph`. This is the
            seam that keeps a test from writing into the developer's own Neo4j — and the
            reason the ``graph`` stage can no longer report an extraction the product
            cannot show.
        index_graph: Coroutine taking the same extraction and publishing it into the
            vector collections LightRAG's graph-aware (``local``) arm searches, returning
            what the store confirms holding. ``None`` uses
            :func:`app.ingestion.graph_vectors.publish_document_graph_vectors`. Separate
            from ``project`` because the two write to different stores and one can be
            configured without the other — and because the graph store is the record
            while these vectors are an index over it (see that module on why a failure
            here degrades rather than fails the ingest).
        verify: Coroutine taking the chunks just published and returning how many the
            search index actually holds — or ``None`` when it cannot tell. Left unset,
            the process retriever's backend is asked. See :meth:`count_indexed`; this is
            the seam that stops a publish from being taken on trust.
    """

    store: DocumentStoreProtocol
    embed: EmbedFn | None = None
    publish: Any = None  # noqa: ANN401 - a coroutine fn; see publish_chunks
    extractor: Extractor | None = None
    complete: CompleteFn | None = None
    verify: Any = None  # noqa: ANN401 - a coroutine fn; see count_indexed
    project: Any = None  # noqa: ANN401 - a coroutine fn; see project_graph
    index_graph: Any = None  # noqa: ANN401 - a coroutine fn; see publish_graph_vectors

    def resolve_embed(self) -> EmbedFn:
        """Return the embedding function, resolving the platform default on first use."""
        if self.embed is None:
            from app.retrieval.gateway import default_embed  # noqa: PLC0415 - lazy

            self.embed = default_embed()
        return self.embed

    def resolve_complete(self) -> CompleteFn:
        """Return the completion function, resolving the platform default on first use."""
        if self.complete is None:
            from app.retrieval.gateway import default_complete  # noqa: PLC0415 - lazy

            self.complete = default_complete()
        return self.complete

    def resolve_extractor(self) -> Extractor:
        """Return the entity/relation extractor this deployment configured.

        **The choice is a cost decision, so it is a setting rather than a constant.**
        ``GRAPH_EXTRACTOR=llm`` (the default) runs one cheap-model extraction per chunk,
        content-addressed and cached to disk, so the same text is never paid for twice and
        a re-ingest costs nothing. ``GRAPH_EXTRACTOR=spacy`` forces the deterministic,
        free, offline extractor.

        The default is ``llm`` because the deterministic path does not produce a usable
        graph, and that is measured rather than assumed: on the refund-escalation policy
        used to verify this stage, spaCy found **1 entity and 0 relations** (its NER
        surfaces names, and its "relations" are intra-sentence co-occurrence, which needs
        two entities in one sentence to exist at all), while the cached LLM extractor
        found 10 entities and 6 stated relations for one call. A graph of nodes with no
        edges is not a knowledge graph, and inventing edges to fill it is the one thing
        this platform must never do — so the honest way to have edges is to pay for the
        extraction once.

        Falling back is not silent: if the model gateway cannot be resolved the
        deterministic extractor runs instead and reports its own ``name``, which is
        recorded on every chunk and on the stage's event.
        """
        if self.extractor is not None:
            return self.extractor
        choice = (get_settings().graph_extractor or "").strip().lower()
        if choice != "spacy":
            try:
                complete = self.resolve_complete()
            except Exception as exc:  # noqa: BLE001 - any gateway failure is one outcome
                logger.warning(
                    "GRAPH_EXTRACTOR=%s but the model gateway is unavailable (%s); "
                    "falling back to the deterministic extractor, which yields few "
                    "relations. The extractor that ran is recorded on every chunk.",
                    choice or "llm",
                    exc,
                )
            else:
                self.extractor = build_extractor(complete=complete, prefer="llm")
                return self.extractor
        self.extractor = build_extractor(prefer="deterministic")
        return self.extractor

    async def publish_chunks(self, chunks: Sequence[RetrievalChunk]) -> None:
        """Write ``chunks`` into the knowledge backend this deployment searches.

        Args:
            chunks: The document's chunks, already carrying their owning tenant.
        """
        if self.publish is not None:
            await self.publish(chunks)
            return
        from app.retrieval.pipeline import get_retriever  # noqa: PLC0415 - lazy

        await get_retriever().backend.ingest_chunks(chunks)

    async def count_indexed(self, chunks: Sequence[RetrievalChunk]) -> int | None:
        """Return how many of ``chunks`` the search index actually holds, or ``None``.

        The counterweight to :meth:`publish_chunks`. A publish that returns without
        raising is not evidence that anything was written — ``LightRAG.ainsert`` records
        per-document failures into its own doc-status store and returns normally, which
        is how the ``index`` stage came to log ``{"indexed": 37}`` against a collection
        holding zero points for five months.

        ``None`` is a real answer and means "this deployment cannot audit its index",
        never "the index is empty". The two demand opposite responses — one is a gap in
        observability, the other is an outage — so they are never collapsed into a
        number. A test that injects its own ``publish`` owns a store this process knows
        nothing about, so it gets ``None`` and the stage records an honest "unverified"
        rather than failing every ingest that runs against a fake.

        Args:
            chunks: The chunks that were just published.

        Returns:
            The number present in the index, or ``None`` when it cannot be audited.
        """
        if self.verify is not None:
            return await self.verify(chunks)
        if self.publish is not None:
            return None
        from app.retrieval.pipeline import get_retriever  # noqa: PLC0415 - lazy

        audit = getattr(get_retriever().backend, "audit_chunks", None)
        if audit is None:
            return None
        return await audit(chunks)

    async def project_graph(
        self,
        entities: Sequence[Any],
        relations: Sequence[Any],
        *,
        tenant_value: str | None,
        source: str,
        extractor: str,
        entity_sources: Mapping[str, Sequence[str]] | None = None,
        relation_sources: Mapping[tuple[str, str], Sequence[str]] | None = None,
    ) -> ProjectionResult:
        """Write one document's extraction into the durable graph, and report what landed.

        The counterweight to the ``graph`` stage's own ``chunks.meta`` write, and the half
        that was missing: the meta rows are what *this ingest* extracted, the durable
        graph is what the product can *show*, and for the whole of Phase 4 only the first
        of the two was ever written.

        Args:
            entities: Every entity the document's chunks yielded.
            relations: Every relation, referring to entities by their extractor id.
            tenant_value: The owning tenant's metadata value — the provenance
                ``scoped_graph`` decides visibility from. Never omitted: an element whose
                owner cannot be established is shown to nobody.
            source: The document's source name, as ``index`` tags it.
            extractor: The extractor's honest ``name``.
            entity_sources: Chunk ids per normalised entity label, written onto the
                node as LightRAG's ``source_id``. This is the field the ``local`` arm
                walks to turn a matched entity into a passage; without it the arm
                returns entities and contributes no candidates at all.
            relation_sources: Chunk ids per ``(src label, tgt label)`` pair, likewise.

        Returns:
            What was attempted and what was verified present in the graph afterwards.
        """
        projector = self.project or project_document_graph
        return await projector(
            entities,
            relations,
            tenant_value=tenant_value,
            source=source,
            extractor=extractor,
            entity_sources=entity_sources,
            relation_sources=relation_sources,
        )

    async def publish_graph_vectors(
        self,
        entities: Sequence[Any],
        relations: Sequence[Any],
        *,
        tenant_value: str | None,
        source: str,
        extractor: str,
        entity_sources: Mapping[str, Sequence[str]] | None = None,
        relation_sources: Mapping[tuple[str, str], Sequence[str]] | None = None,
    ) -> GraphVectorResult:
        """Index the same extraction for LightRAG's graph-aware arm, and report what landed.

        The counterweight to :meth:`project_graph`, and the half that was missing from
        *it*: Neo4j is what the Graph screen draws, and ``entities_vdb`` is what a query
        has to match before anything in Neo4j can be reached. The live deployment had 156
        nodes in the one and 0 points in the other, so the ``local`` arm of hybrid recall
        returned nothing for every query ever asked.

        The embedder is resolved only on the default path: a host that injected its own
        publisher owns a store this process knows nothing about, and resolving the
        platform's model gateway on its behalf would make a fake-driven test depend on a
        provider it never asked for.

        Args:
            entities: Every entity the document's chunks yielded.
            relations: Every relation, referring to entities by their extractor id.
            tenant_value: The owning tenant's metadata value — tagged into every
                ``file_path``, the same way the graph and the chunk vectors carry it.
            source: The document's source name, as ``index`` tags it.
            extractor: The extractor's honest ``name``.
            entity_sources: Chunk ids per normalised entity label, for LightRAG's
                ``source_id`` field.
            relation_sources: Chunk ids per ``(src label, tgt label)`` pair, likewise.

        Returns:
            What was attempted and what the vector store confirmed holding.
        """
        if self.index_graph is not None:
            return await self.index_graph(
                entities,
                relations,
                tenant_value=tenant_value,
                source=source,
                extractor=extractor,
                entity_sources=entity_sources,
                relation_sources=relation_sources,
            )
        from app.ingestion.graph_vectors import (  # noqa: PLC0415 - lazy, avoids a cycle
            publish_document_graph_vectors,
        )

        return await publish_document_graph_vectors(
            entities,
            relations,
            tenant_value=tenant_value,
            source=source,
            extractor=extractor,
            embed=self.resolve_embed(),
            entity_sources=entity_sources,
            relation_sources=relation_sources,
        )


_dependencies: IngestDependencies | None = None


def set_ingest_dependencies(dependencies: IngestDependencies) -> None:
    """Install the collaborators the handlers use.

    Args:
        dependencies: What the handlers should reach for outside the database.
    """
    global _dependencies
    _dependencies = dependencies


def reset_ingest_dependencies() -> None:
    """Drop the installed collaborators, so the next call rebuilds them from settings."""
    global _dependencies
    _dependencies = None


def _deps() -> IngestDependencies:
    """Return the installed collaborators, building the configured default if unset."""
    global _dependencies
    if _dependencies is None:
        _dependencies = IngestDependencies(store=DocumentStore.from_settings())
    return _dependencies


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────


def _fatal(message: str, *, kind: str) -> ApplicationError:
    """Build the non-retryable failure a handler raises when retrying cannot help.

    Args:
        message: What went wrong, in a sentence a tenant could be shown.
        kind: The error type the orchestrator records.

    Returns:
        The error to raise.
    """
    return ApplicationError(message, type=kind, non_retryable=True)


async def _document(session: AsyncSession, document_id: int) -> Document:
    """Load the document through the caller's bound tenant scope.

    No ``WHERE tenant_id``: the scope is on the connection, so the ``tenant_isolation``
    policy is what hides another tenant's row — which makes this read part of the proof
    that the policy works rather than a Python filter that would pass either way.

    Args:
        session: The scoped session the substrate handed the handler.
        document_id: The document being processed.

    Returns:
        The row.

    Raises:
        ApplicationError: Non-retryable, when no such row is visible.
    """
    document = (
        await session.execute(select(Document).where(Document.id == document_id))
    ).scalar_one_or_none()
    if document is None:
        raise _fatal(
            f"document {document_id} is not visible under this tenant scope",
            kind="DocumentNotVisible",
        )
    return document


def _owning_tenant(document: Document, stage: str) -> int:
    """Return the tenant that owns ``document``, refusing a platform-level one.

    ``chunks.tenant_id`` is ``NOT NULL`` by design: under the ``tenant_isolation``
    predicate ``NULL = <scope>`` is NULL, so a null-tenant chunk would be invisible to
    every tenant while still being indexed and paid for. A platform-level document
    therefore owns no rows in that table, and the honest place to say so is here — before
    the work is done — rather than as an integrity error after a parse has been paid for.

    Args:
        document: The row.
        stage: The stage asking, for the message.

    Returns:
        The owning tenant id.

    Raises:
        ApplicationError: Non-retryable, when the document has no owning tenant.
    """
    if document.tenant_id is None:
        raise _fatal(
            f"document {document.id} has no owning tenant, so stage {stage!r} has "
            "nowhere to write: chunks.tenant_id is NOT NULL because a null-tenant chunk "
            "would be invisible to every tenant while still being indexed and paid for",
            kind="PlatformDocumentNotIngestable",
        )
    return document.tenant_id


#: Share of a document's pages a heading must appear on before it is read as a **running
#: header** rather than as a title. A running header is printed on every page or every
#: other page by construction, so a half is the structural line rather than a fitted one.
#: Measured on the real corpus, the two populations sit well clear of it on both sides:
#: the CFR reprints' "Federal Trade Commission" runs at 0.50 and 0.60 and their
#: "12 CFR Ch. X (1-1-23 Edition)" at 0.50, while the most-repeated *genuine* heading of
#: any fixture — a section title recurring in a statistics breakdown — reaches 0.40, and
#: a cover title reprinted on its first content page reaches 0.04.
_RUNNING_HEADER_PAGE_SHARE = 0.5

#: Pages a document needs before repetition means anything. On a one- or two-page
#: document every heading trivially covers half the pages, so the test would reject the
#: real title and every alternative to it.
_MIN_PAGES_FOR_RUNNING_HEADER = 3


def _running_headers(parsed: ParsedDocument) -> frozenset[str]:
    """Return the heading texts this document prints as page furniture.

    The evidence is repetition across *pages*, which is what a running header is and what
    a title is not. It is counted per page rather than per block on purpose: a heading
    Docling emits twice on one page is a parse artefact, not a header, and counting
    occurrences would let it pass for one.

    Args:
        parsed: The parse output.

    Returns:
        Every heading text appearing on at least :data:`_RUNNING_HEADER_PAGE_SHARE` of the
        document's pages, or an empty set on a document too short
        (:data:`_MIN_PAGES_FOR_RUNNING_HEADER`) for repetition to mean anything.
    """
    if parsed.page_count < _MIN_PAGES_FOR_RUNNING_HEADER:
        return frozenset()
    pages: dict[str, set[int]] = defaultdict(set)
    for block in parsed.blocks:
        if block.kind is BlockKind.HEADING and block.text.strip():
            pages[block.text.strip()].add(block.page_no)
    threshold = parsed.page_count * _RUNNING_HEADER_PAGE_SHARE
    return frozenset(text for text, seen in pages.items() if len(seen) >= threshold)


def _derive_title(parsed: ParsedDocument, filename: str) -> str:
    """Return the document's title: its first heading that is not page furniture.

    The first heading is what Docling's ``title`` label maps to, and on a born-digital
    paper it is the printed title. **On a reprint it is the running header**, and that is
    not a rare shape — it is what every CFR part, every gazette and every bound statutory
    volume looks like. Measured on this corpus: two different FTC rules, 16 CFR 435 and
    16 CFR 703, both derived the title ``"Federal Trade Commission"``, because that is the
    verso running head printed above the first section of each; and 12 CFR 1026.13 derived
    ``"§1026.13"``, its recto running head, rather than
    ``"§1026.13 Billing error resolution."``. Retrieval never suffered — the chunk prefix
    carries the section path — but a screen listing ``documents.title`` showed two
    identical rows for two unrelated regulations, which is a corpus a tenant cannot
    navigate.

    So the heading that names the document is the first one that is **not** printed on
    page after page; see :func:`_running_headers`. A document whose every heading repeats
    that way falls back to the first heading regardless, because a repeated heading is
    still the document's own words and is strictly no worse than what this function
    returned before.

    The final fallback is deliberately the **row's** file name and not
    :attr:`ParsedDocument.source_name`: the parser is handed a content-addressed path in
    the document store, so its idea of the source name is a SHA-256 — a true fact about
    the file and a useless title. The tenant's own file name is the weakest *honest* title
    available, and inventing one with a model call is exactly the cost D7 exists to avoid.

    Args:
        parsed: The parse output.
        filename: ``documents.filename`` — the name the tenant uploaded it under.

    Returns:
        The title, never empty.
    """
    furniture = _running_headers(parsed)
    headings = [
        text
        for block in parsed.blocks
        if block.kind is BlockKind.HEADING and (text := block.text.strip())
    ]
    titled = next((text for text in headings if text not in furniture), "")
    return titled or (headings[0] if headings else "") or Path(filename).stem or filename


def _document_context(document: Document) -> DocumentContext:
    """Build the D7 prefix fields from the ``documents`` row.

    Type and date are the tenant's to supply at upload (see the correction under D7);
    absent, they are left empty so :func:`aegis.retrieval.chunker.chunk_prefix` renders
    ``untyped`` / ``undated``. That is a *stated absence*, which keeps the prefix's shape
    constant across the corpus — and the shape matters because the prefix is embedded
    with the chunk.

    Args:
        document: The row.

    Returns:
        The context to build prefixes from.
    """
    return DocumentContext(
        title=document.title or Path(document.filename).stem,
        doc_type=document.doc_type or "",
        doc_date=document.doc_date,
    )


def _table_policy() -> TableSummaryPolicy:
    """Build D8's table-summary policy from this deployment's settings.

    Returns:
        The threshold, the prompt's size budget and the model role, as configured. The
        defaults live in :class:`aegis.ingestion.tables.TableSummaryPolicy`; the settings
        exist so the cut-off can follow a corpus rather than a commit.
    """
    settings = get_settings()
    return TableSummaryPolicy(
        enabled=settings.table_summary_enabled,
        min_rows=settings.table_summary_min_rows,
        min_cols=settings.table_summary_min_cols,
        min_cells=settings.table_summary_min_cells,
        max_grid_chars=settings.table_summary_max_grid_chars,
    )


def _chunk_content(chunk: SectionChunk, summary: str) -> str:
    """Return the text written to ``chunks.content`` — grid included, always.

    The summary goes **in front of** the grid and never in place of it. D8's own wording
    ("the table's embedded text *is* a generated NL summary") reads the other way, and
    following it would be a quiet data loss: the numbers are what most questions about a
    table are actually asking for, and a chunk holding only prose about them cannot be
    quoted, cannot be span-verified (4.14), and cannot answer "what was the BLEU score".
    In front rather than behind because the head of a chunk is what a truncating
    embedder and a cross-encoder reranker both see most of.

    Args:
        chunk: The packed chunk.
        summary: The generated summary, or ``""`` when the chunk has none.

    Returns:
        The chunk's stored text.
    """
    return f"{summary}\n\n{chunk.text}" if summary else chunk.text


def _table_meta(chunk: SectionChunk, report: TableSummaryReport) -> dict[str, Any] | None:
    """Return the ``table`` block of a table chunk's metadata, or ``None`` for prose.

    This is what makes a table retrievable *as a table* (D8): the shape TableFormer
    reported and the caption it was printed with, on the row, rather than something a
    consumer has to recover by counting pipes in the text.

    ``summarised`` and ``reason`` are both recorded because their absence is ambiguous.
    A table with no summary because it is 8x3 and a table with no summary because the
    gateway returned a 500 look identical on a row, and only one of them is worth waking
    somebody for.

    Args:
        chunk: The packed chunk.
        report: The document's summarisation outcome.

    Returns:
        The metadata block, or ``None`` when the chunk is not a table.
    """
    table = chunk.table
    if table is None:
        return None
    summary = report.summary_for(table)
    return {
        "rows": table.rows,
        "cols": table.cols,
        "caption": table.caption,
        "digest": table.digest,
        "summarised": bool(summary),
        "summary": summary or None,
        "reason": report.reason_for(table) or None,
    }


def _chunk_meta(
    chunk: SectionChunk, *, document: Document, parser: str, table: dict[str, Any] | None
) -> dict[str, Any]:
    """Return the provenance recorded on one ``chunks`` row.

    Everything a citation needs and nothing a query has to re-derive: where the text sits
    in the document (ordinal, section, word span), where it sits on the page (spans, each
    with the union of its blocks' boxes), the prefix that will be folded into the text by
    ``enrich``, the id the dense index is keyed by
    (:meth:`~aegis.retrieval.chunker.ChunkPiece.indexed_id` — the content address plus the
    ordinal, so identical text under one heading path cannot collide), and which parser
    produced it — because chunks from two Docling versions are not interchangeable.

    Args:
        chunk: The packed chunk.
        document: The row it belongs to.
        parser: The parser name and version recorded on the parse.
        table: The :func:`_table_meta` block when this chunk is a table, else ``None``.
            Present as ``null`` on a prose row rather than absent, so "this is not a
            table" and "this row predates task 4.10" stay distinguishable.

    Returns:
        A JSON-serialisable mapping for ``chunks.meta``.
    """
    return {
        "table": table,
        "ordinal": chunk.ordinal,
        "section": chunk.section,
        "prefix": chunk.prefix,
        "word_start": chunk.word_start,
        "word_count": chunk.word_count,
        # ``indexed_id``, not ``content_id``: the ordinal is folded in so two chunks with
        # identical text under one heading path cannot claim one key in the vector store.
        # The key stays stable across a re-chunk (deterministic chunking → same ordinals),
        # which is what makes the ``index`` stage a re-publish rather than a duplication.
        "content_id": chunk.indexed_id(),
        "source": document.filename,
        "parser": parser,
        "page_no": chunk.page_no,
        "spans": [
            {
                "page_no": span.page_no,
                "bbox": None
                if span.bbox is None
                else [span.bbox.left, span.bbox.top, span.bbox.right, span.bbox.bottom],
            }
            for span in chunk.spans
        ],
        # Flipped to ``true`` by ``enrich``; it is what makes that stage's UPDATE
        # idempotent without re-reading the text it is about to rewrite.
        "enriched": False,
    }


async def _document_chunks(
    session: AsyncSession, document_id: int
) -> list[tuple[int, str, dict[str, Any]]]:
    """Return ``(id, content, meta)`` for a document's chunks, in reading order.

    Args:
        session: The scoped session.
        document_id: The document.

    Returns:
        One tuple per chunk row, ordered by id (which is insertion order, and insertion
        order is reading order).
    """
    rows = (
        await session.execute(
            select(Chunk.id, Chunk.content, Chunk.meta)
            .where(Chunk.document_id == document_id)
            .order_by(Chunk.id)
        )
    ).all()
    return [(row[0], row[1], dict(row[2] or {})) for row in rows]


async def _document_vectors(
    session: AsyncSession, document_id: int
) -> dict[int, list[float]]:
    """Return ``{chunk_id: embedding}`` for a document's already-embedded chunks.

    Read separately from :func:`_document_chunks` rather than widened into it, because
    only the ``index`` stage needs the vectors and they are the largest column in the
    table — a 3072-float row each. ``embed`` and ``graph`` iterate the same rows and
    would carry megabytes they never look at.

    Chunks still holding the ``_UNEMBEDDED`` sentinel are absent from the result rather
    than present with an empty value, so the caller's ``.get()`` yields ``None`` and the
    chunk is published as text for the backend to embed — the pre-existing behaviour,
    unchanged, for the only case where it is still correct.

    Args:
        session: The scoped session.
        document_id: The document.

    Returns:
        The embedding of record per chunk id, omitting unembedded rows.
    """
    rows = (
        await session.execute(
            select(Chunk.id, Chunk.embedding).where(Chunk.document_id == document_id)
        )
    ).all()
    return {row[0]: list(row[1]) for row in rows if row[1]}


# ─────────────────────────────────────────────────────────────────────────────
# parse
# ─────────────────────────────────────────────────────────────────────────────


async def parse_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Read the stored bytes into a structured tree and record what the parse found.

    The tree is written to the document store as the parse artifact, beside the bytes,
    because ``chunk`` is a different activity in a different transaction and re-deriving
    the structure costs 0.4–3.2 seconds a page. That artifact — not a re-parse — is what
    the next stage reads.

    **A low-confidence parse is flagged, not blocked**, and that is a decision rather than
    an omission. ``parse_confidence`` (D-parse; :mod:`aegis.ingestion.quality`) is written
    to the row and logged at WARNING with the reasons behind it, and the ingest continues.
    Raising here would fail the activity, and the orchestrator would then re-parse a
    126-page document twice more to reach the same verdict — while the check itself is a
    *disagreement* detector that has a known false-positive mode on PDFs whose content
    stream is emitted out of visual order. Refusing a document on a signal that cannot say
    which of two readings is wrong would be a gate that blocks legitimate work, which is
    its own failure. So the tenant gets the document *and* the warning.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document to parse.
        stage: The stage name (``"parse"``).

    Returns:
        ``page_count``, ``title`` and ``parse_confidence``, applied by the substrate with
        the stage bump.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or its bytes
            are not in the store. Neither is fixed by trying again, and retrying a parse
            burns the stage's whole attempt budget rediscovering the same absence.
    """
    document = await _document(session, document_id)
    store = _deps().store
    # ``open_local`` rather than ``path_for``: the parse needs a file on disk because
    # Docling parses one, but it must not need the *store* to be a disk. An object-store
    # implementation materialises a temporary file here and removes it on exit, and this
    # stage cannot tell the difference — which is what keeps the bytes movable.
    try:
        with store.open_local(
            tenant_id=document.tenant_id, sha256=document.content_sha256
        ) as path:
            # Docling is blocking, CPU-bound and minutes long on a large document. Off
            # the loop, so the activity's heartbeat keeps beating and the orchestrator
            # does not conclude this worker died in the most expensive stage to redo.
            #
            # Inside the ``with``: an object-store implementation's temporary file must
            # outlive the parse, and only the parse knows when it is finished with it.
            parsed: ParsedDocument = await asyncio.to_thread(parse_pdf, path)
    except FileNotFoundError as exc:
        # Not retryable, and retrying burns the stage's whole attempt budget
        # rediscovering the same absence.
        raise _fatal(str(exc), kind="DocumentBytesMissing") from exc
    store.put_artifact(
        tenant_id=document.tenant_id,
        sha256=document.content_sha256,
        payload=dumps_parsed(parsed),
    )
    quality = parsed.quality
    logger.info(
        "parsed document %s: %d page(s), %d block(s), %d table(s), OCR %s (%s), "
        "parse confidence %s in %.1fs",
        document_id,
        parsed.page_count,
        len(parsed.blocks),
        parsed.table_count,
        "on" if parsed.ocr.enabled else "off",
        parsed.ocr.reason,
        "not scored" if quality is None else f"{quality.confidence:.2f}",
        parsed.parse_seconds,
    )
    if quality is not None and quality.is_low:
        # The one line that makes a silent bad parse audible. It names every signal, not
        # only the score, because "0.57" tells a person nothing they can act on and
        # "reading order DISAGREES with the raw text layer" tells them what to look at.
        logger.warning(
            "document %s parsed at LOW confidence %.2f (below %.2f) — indexed and "
            "searchable, but its reading order is suspect: %s",
            document_id,
            quality.confidence,
            LOW_CONFIDENCE,
            "; ".join(quality.reasons),
        )
    # Task 4.12 / 4.6c's hand-off. ``parse_confidence`` lands on the row; the *reasons*
    # have nowhere on a row to live and were, until now, only a WARNING in a log file no
    # tenant can read. They go into the durable run record instead, which is what makes
    # "this document parsed at 0.57" actionable rather than merely alarming. The OCR
    # decision and the heading histogram travel with them for the same reason: D3 trades
    # a silent ``do_ocr=False`` away, and a silent trade is one nobody can audit.
    report_stage_facts(
        parser=parsed.parser,
        parse_seconds=round(parsed.parse_seconds, 3),
        page_count=parsed.page_count,
        block_count=len(parsed.blocks),
        table_count=parsed.table_count,
        heading_histogram={str(level): count for level, count in sorted(
            parsed.heading_histogram.items()
        )},
        removed_furniture=len(parsed.removed_furniture),
        ocr={"enabled": parsed.ocr.enabled, "reason": parsed.ocr.reason},
        quality=None
        if quality is None
        else {
            "confidence": round(quality.confidence, 4),
            "low": quality.is_low,
            "threshold": LOW_CONFIDENCE,
            "ordering": None if quality.ordering is None else round(quality.ordering, 4),
            "ordering_pages": quality.ordering_pages,
            "ordering_anchors": quality.ordering_anchors,
            "fragment_rate": round(quality.fragment_rate, 4),
            "flat_headings": quality.flat_headings,
            "reasons": list(quality.reasons),
        },
    )
    return {
        "page_count": parsed.page_count,
        "title": _derive_title(parsed, document.filename),
        "parse_confidence": None if quality is None else quality.confidence,
    }


# ─────────────────────────────────────────────────────────────────────────────
# chunk
# ─────────────────────────────────────────────────────────────────────────────


async def chunk_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Pack the parsed sections into ``chunks`` rows this tenant owns.

    **Delete-then-insert, inside the caller's transaction**, and never a bare insert. The
    substrate guarantees this handler runs at most once per committed stage, so a bare
    insert would be safe against replay; what it would not be safe against is an attempt
    that inserted its rows, was retried for an unrelated reason, and succeeded on the
    second pass — leaving the first pass's chunks behind, still matching queries. The
    ``DELETE`` costs one statement and removes the question, and because it shares the
    transaction with the insert there is no window in which the document has no chunks.

    **Tables (D8, task 4.10).** Each table is its own chunk, carrying the shape
    TableFormer reported and the caption it was printed with; above a configured size it
    also carries a generated sentence or two saying what it shows, written *in front of*
    the grid rather than instead of it. That is the one place this handler spends money,
    and it is bounded twice: by the threshold, which never sends a table a reader could
    already follow, and by ``table_summaries``, which is keyed on the table's own content
    hash. The idempotency contract therefore covers the bill as well as the rows — the
    second run of this stage finds every summary cached and makes no model call at all,
    which is also what makes 4.13's re-index free.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document to chunk.
        stage: The stage name (``"chunk"``).

    Returns:
        ``chunk_count``, applied by the substrate with the stage bump.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible, owns no
            tenant, or has no readable parse artifact.
    """
    document = await _document(session, document_id)
    owner = _owning_tenant(document, stage)
    store = _deps().store
    try:
        payload = store.read_artifact(
            tenant_id=document.tenant_id, sha256=document.content_sha256
        )
    except OSError as exc:
        raise _fatal(
            f"document {document_id} has no parse artifact: re-run the parse stage "
            f"(reading it failed with {exc})",
            kind="ParseArtifactMissing",
        ) from exc
    try:
        parsed = loads_parsed(payload)
    except ValueError as exc:
        raise _fatal(
            f"document {document_id}'s parse artifact cannot be read: {exc}",
            kind="ParseArtifactUnreadable",
        ) from exc

    context = _document_context(document)
    pieces = chunk_sections(parsed, context=context)
    # D8 / task 4.10. Before the delete rather than after, so a summarisation that fails
    # outright leaves the previous ingest's chunks untouched rather than replacing them
    # with a set that has lost its table summaries. The gateway is resolved only when
    # summaries are switched on and this document actually has a table: a corpus of prose
    # must not make the ``chunk`` stage depend on a model being reachable at all.
    policy = _table_policy()
    wants_model = policy.enabled and any(piece.table is not None for piece in pieces)
    summaries = await summarise_document_tables(
        session,
        pieces,
        tenant_id=owner,
        complete=_deps().resolve_complete() if wants_model else None,
        policy=policy,
        title=context.title,
    )
    # One transaction: the old rows and the new ones can never both be visible, and a
    # failure anywhere below leaves the previous ingest's chunks exactly as they were.
    await session.execute(delete(Chunk).where(Chunk.document_id == document_id))
    if pieces:
        await session.execute(
            insert(Chunk),
            [
                {
                    "tenant_id": owner,
                    "document_id": document_id,
                    "persona": None,
                    "content": _chunk_content(piece, summaries.summary_for(piece.table)),
                    "embedding": _UNEMBEDDED,
                    "meta": _chunk_meta(
                        piece,
                        document=document,
                        parser=parsed.parser,
                        table=_table_meta(piece, summaries),
                    ),
                }
                for piece in pieces
            ],
        )
    tables = sum(1 for piece in pieces if piece.table is not None)
    logger.info(
        "chunked document %s into %d chunk(s), %d of them tables (%d summarised, "
        "%d model call(s))",
        document_id,
        len(pieces),
        tables,
        len(summaries.summaries),
        summaries.model_calls,
    )
    report_stage_facts(
        chunks=len(pieces),
        tables=tables,
        summarised=len(summaries.summaries),
        model_calls=summaries.model_calls,
        words=sum(piece.word_count for piece in pieces),
    )
    return {"chunk_count": len(pieces)}


# ─────────────────────────────────────────────────────────────────────────────
# enrich
# ─────────────────────────────────────────────────────────────────────────────

#: Fold the D7 prefix into the text that is embedded *and* full-text indexed.
#:
#: Two rules the evidence behind D7 is unambiguous about: **prefix, not suffix**, and
#: **into the embedded text, not metadata alongside it** — the second is precisely what
#: LangChain's header splitter gets wrong. ``chunks.search_vector`` is generated from
#: ``content``, so this one statement moves the lexical arm as well as the dense one.
#:
#: Idempotent by the ``enriched`` guard rather than by inspecting the text: a second run
#: updates zero rows. Guarding on "does the content already start with the prefix?" would
#: be a heuristic about the tenant's own words; a flag the writer sets is a fact.
_ENRICH_SQL = """
    UPDATE chunks
       SET content = CASE
                       WHEN coalesce(meta->>'prefix', '') = '' THEN content
                       ELSE (meta->>'prefix') || E'\\n' || content
                     END,
           meta = jsonb_set(meta, '{enriched}', 'true'::jsonb)
     WHERE document_id = :document_id
       AND tenant_id = :tenant_id
       AND coalesce((meta->>'enriched')::boolean, false) = false
"""


async def enrich_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Fold the document · type · date · heading-path prefix into each chunk's text.

    The prefix was built at chunk time, because the document context it needs is not
    reachable from a chunk afterwards; this stage is where it becomes part of the text
    that is actually embedded and indexed.

    **Whose measurement, and what ours says.** "Context@5 33.3% → 55.0%" is the **ECIR 2026
    field-ablation result** (``arXiv:2601.11863``), on their corpus — it is the evidence the
    prefix's *shape* was chosen from (see :mod:`aegis.retrieval.chunker`) and it is theirs,
    not a number this pipeline produced. **Our own A1 → A2 ablation moves recall@6 by
    −3.8 pp** (0.774 → 0.736; recall@20 0.906 → 0.896) on a 53-case gold set over four PDFs
    — a decline, not a gain, and one n=53 cannot distinguish from noise in either direction
    (``runs/eval-goldset-20260819.json``). The prefix is kept because it is what makes a
    chunk self-describing for a citation and for the graph extractor, and because the
    external evidence for its *shape* is stronger than our 53 cases are against it — not
    because we measured it helping retrieval here. Saying otherwise on a slide is the
    defect this paragraph exists to prevent.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document whose chunks to enrich.
        stage: The stage name (``"enrich"``).

    Returns:
        An empty mapping: this stage's whole output is in ``chunks``.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant.
    """
    document = await _document(session, document_id)
    owner = _owning_tenant(document, stage)
    result = await session.execute(
        text(_ENRICH_SQL), {"document_id": document_id, "tenant_id": owner}
    )
    logger.info("enriched %d chunk(s) of document %s", result.rowcount, document_id)
    report_stage_facts(enriched=result.rowcount)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# embed
# ─────────────────────────────────────────────────────────────────────────────


async def embed_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Embed every chunk of the document and write the embedding of record.

    ``chunks.embedding`` is the durable source-of-record vector — not a search index
    (this cluster has no ``pgvector``) — so an index rebuilt from scratch replays these
    rows instead of paying the provider a second time for text that has not changed.

    The write is an ``UPDATE`` keyed on the chunk's primary key, so running the stage
    twice recomputes the same vectors over the same rows and leaves exactly one of each.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document whose chunks to embed.
        stage: The stage name (``"embed"``).

    Returns:
        An empty mapping.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant, or when the embedder returns a different number of vectors than it
            was given texts — an off-by-one there would attach every chunk's vector to
            its neighbour, which no later stage could detect.
    """
    document = await _document(session, document_id)
    _owning_tenant(document, stage)
    rows = await _document_chunks(session, document_id)
    if not rows:
        logger.info("document %s has no chunks to embed", document_id)
        report_stage_facts(embedded=0)
        return {}
    embed = _deps().resolve_embed()
    embedded = 0
    for start in range(0, len(rows), _EMBED_BATCH):
        batch = rows[start : start + _EMBED_BATCH]
        vectors = await embed([content for _id, content, _meta in batch])
        if len(vectors) != len(batch):
            raise _fatal(
                f"the embedder returned {len(vectors)} vector(s) for {len(batch)} chunk(s) "
                f"of document {document_id}; pairing them by position would attach each "
                "chunk's vector to a different chunk",
                kind="EmbeddingCountMismatch",
            )
        for (chunk_id, _content, _meta), vector in zip(batch, vectors, strict=True):
            await session.execute(
                update(Chunk)
                .where(Chunk.id == chunk_id)
                .values(embedding=[float(value) for value in vector])
            )
            embedded += 1
    logger.info("embedded %d chunk(s) of document %s", embedded, document_id)
    report_stage_facts(embedded=embedded, batches=-(-len(rows) // _EMBED_BATCH))
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# index
# ─────────────────────────────────────────────────────────────────────────────


async def index_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Publish the document's chunks into the knowledge backend the platform searches.

    The lexical arm needs nothing here — ``chunks.search_vector`` is generated by
    PostgreSQL from ``content``, which is the whole reason D5 chose Postgres FTS. The
    dense arm does: its index lives in the configured backend (LightRAG's store in full
    mode, the per-tenant Qdrant collections in lite mode), and this is the call that puts
    a newly ingested document into it.

    Chunks are published under their **content-addressed** ids, prefixed by the owning
    tenant. Both halves matter: content-addressing makes a re-publish of unchanged text an
    overwrite of the same key rather than a duplicate, and the tenant prefix stops two
    tenants who uploaded the same public filing from overwriting each other's row in a
    store whose ids are global.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document to publish.
        stage: The stage name (``"index"``).

    Each chunk is published **with the embedding of record already written onto its row**
    by the ``embed`` stage, rather than as bare text for the backend to embed again. That
    is what makes this stage deterministic and free: the vector the index serves is
    byte-for-byte the vector the database holds, so the two stores cannot answer
    differently, and rebuilding the index later costs nothing (see
    :mod:`app.ingestion.vector_index`).

    **LightRAG keeps chunks in two stores and this stage writes both.** The vectors are
    what the dense arm searches. The KV (``lightrag_doc_chunks``) is what the *graph* arm
    reads: a matched entity names chunk ids and nothing else, and
    ``text_chunks.get_by_ids`` is the only thing that turns those ids into text. That
    table held **0 rows for every workspace**, because the ``ainsert`` bypass skips the
    one call in LightRAG that writes it — so the graph arm returned entities and
    contributed no passages to any answer. See :mod:`app.ingestion.chunk_kv`; the write
    runs on this session inside a ``SAVEPOINT`` and its outcome is reported rather than
    raised, because the corpus is fully searchable without it and a wholly correct
    document must not be discarded over a derived index.

    **The stage then reads the index back and refuses to report a success it cannot
    show.** It used to log ``"indexed 37 chunk(s)"`` on the strength of a publish call
    that had returned without raising, and record ``{"indexed": 37}`` onto the ingest log
    — while the collection held zero points. Every downstream reading of the platform,
    the Jobs funnel included, inherited that number. A count the writer chose is a claim;
    only the store can supply evidence.

    Returns:
        An empty mapping: what this stage produced lives in the backend's index, and the
        row already records the chunk count that was published.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant, or when the index is auditable and does **not** hold what was just
            published. Failing here costs a re-run of one stage; succeeding here costs a
            corpus that answers every question with silence.
    """
    document = await _document(session, document_id)
    owner = _owning_tenant(document, stage)
    rows = await _document_chunks(session, document_id)
    if not rows:
        logger.info("document %s has no chunks to index", document_id)
        report_stage_facts(indexed=0)
        return {}
    owner_token = tenant_metadata_value(owner)
    vectors = await _document_vectors(session, document_id)
    published = [
        RetrievalChunk(
            id=chunk_source_id(owner, meta.get("content_id") or chunk_id),
            doc_id=str(document_id),
            ordinal=int(meta.get("ordinal", 0)),
            text=content,
            metadata={
                **meta,
                TENANT_METADATA_KEY: owner_token,
                "document_id": document_id,
                "source": meta.get("source") or document.filename,
            },
            vector=vectors.get(chunk_id) or None,
        )
        for chunk_id, content, meta in rows
    ]
    await _deps().publish_chunks(published)

    indexed = await _deps().count_indexed(published)
    if indexed is not None and indexed < len(published):
        raise _fatal(
            f"the index stage published {len(published)} chunk(s) of document "
            f"{document_id} but the search index holds {indexed} of them. The publish "
            "call returned without raising, which is exactly how this failure stayed "
            "invisible before: the corpus is not searchable and the run must not record "
            "a success for it.",
            kind="IndexNotWritten",
        )

    # The second of LightRAG's two chunk stores. The dense arm reads the vectors; the
    # graph arm reads *this*, because a matched entity names chunk ids and nothing else,
    # and ``text_chunks.get_by_ids`` is where those ids become text. It is written here
    # rather than in ``graph`` because it is a fact about chunks, not about extraction: a
    # chunk no entity happens to mention still belongs in the store, and the two writes
    # then share one list and cannot disagree about which chunks exist.
    kv = await publish_chunk_kv(
        session,
        [
            ChunkKVRow(
                key=chunk.id,
                content=chunk.text,
                # The same tag the graph carries and the dense point carries, from the
                # one function that spells it — this is the field ``_scoped_recall``
                # reads to decide whether a graph-arm passage may be shown at all.
                file_path=tagged_source(str(chunk.metadata["source"]), owner_token),
                full_doc_id=str(document_id),
                chunk_order_index=chunk.ordinal,
            )
            for chunk in published
        ],
    )

    logger.info(
        "indexed %d chunk(s) of document %s; the index reports holding %s",
        len(published),
        document_id,
        "an unauditable number" if indexed is None else indexed,
    )
    facts: dict[str, Any] = {
        "indexed": len(published),
        "verified": indexed,
        "collection": owner_token,
        # What the KV confirmed holding, on the same rule as ``verified``: ``None`` means
        # it could not be asked and is never rounded to zero.
        "chunk_kv": kv.rows,
    }
    if kv.skipped is not None:
        facts["chunk_kv_note"] = f"skipped: {kv.skipped}"
    elif kv.failed is not None:
        facts["chunk_kv_note"] = f"failed: {kv.failed}"
    report_stage_facts(**facts)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# graph
# ─────────────────────────────────────────────────────────────────────────────


async def graph_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Extract what each chunk states, record it on the row, and project it to the graph.

    **Three writes, because they answer three different questions, and each was added
    only after the one before it turned out to be invisible from where a user stands.**

    The extraction is written to ``chunks.meta`` — a tenant-scoped, RLS-protected row we
    own — rather than only into a graph store we do not. That is what makes "the graph
    this ingest built" answerable with the graph store unreachable, and it is the record
    the ingest log is projected from. The extractor's own ``name`` is recorded beside its
    output, so a corpus extracted by the deterministic extractor is never mistaken for one
    an LLM extracted.

    That row, however, is not the graph the product shows. ``GET /v1/graph`` reads Neo4j
    unioned with the in-process per-run slice, and neither of them has ever read
    ``chunks.meta``. Measured on a real upload: the stage completed reporting one
    extracted entity while Neo4j held 78 nodes before and 78 after, and the Graph screen
    59 before and 59 after. So the second write —
    :func:`app.ingestion.graph_projection.project_document_graph` — puts the same
    entities and relations into the durable graph, carrying the owning tenant on every
    element so :func:`~aegis.retrieval.types.scoped_graph` can decide who may see them.

    That durable graph, in turn, is not *searchable* on its own. Hybrid recall's second
    arm (``local``) matches a query against LightRAG's ``entities_vdb`` and only then
    looks the matched names up in Neo4j — and nothing wrote those vectors, because
    ``publish_vectors`` deliberately bypasses the ``ainsert`` that would have. Measured:
    ``lightrag_vdb_entities`` held **0** points against a Neo4j holding 156 nodes for the
    same workspace, so the graph arm returned nothing for every query ever asked. So the
    third write — :func:`app.ingestion.graph_vectors.publish_document_graph_vectors` —
    publishes the same entities and relations as vectors, shaped from the same
    ``projection_rows`` call as the graph itself so the two cannot name an entity
    differently.

    Finding an entity is still not quoting one, and that gap was measured too. Both writes
    above were correct and the arm *still* contributed 0 candidates to the merged ranking,
    because ``lightrag.operate._find_related_text_unit_from_entities`` reads ``source_id``
    off the matched **node** to learn which chunks the entity came from, and the nodes
    carried none: it logged ``No entities with text chunks found`` and returned before it
    read a single chunk. So the chunk ids accumulated below travel into both the node and
    the vector payload, and the passages they name are written by the ``index`` stage into
    LightRAG's chunk KV (:mod:`app.ingestion.chunk_kv`). Three stores and one set of ids.

    **And the stage refuses to report a success it cannot show.** It reports what each
    store confirms holding, not what was handed to it; a projection that was owed and did
    not land fails the stage. A deployment with no durable graph at all (``STORES=off``,
    no ``NEO4J_URI``, a test process with no scratch instance) is a different fact from a
    failed write, and is recorded as an explicit ``projection: skipped: …`` rather than
    passed off as a projection of zero. The vector index is reported the same way and
    fails the stage for neither: it is an index over a graph that is already verified
    present, rebuildable from durable state, and losing it costs one arm's reach rather
    than the document — see :mod:`app.ingestion.graph_vectors` for the full argument.

    Idempotent in both halves: the ``chunks.meta`` write is an ``UPDATE`` per chunk id
    that replaces those metadata keys outright, and the projection merges on the graph's
    own identity, so a second run over the same text converges rather than accumulating.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document whose chunks to extract from.
        stage: The stage name (``"graph"``).

    Returns:
        An empty mapping.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant. Retryable, when the durable graph was owed this document's entities
            and does not hold them afterwards — a graph store blip is worth a second
            attempt, and a silent success is worth none.
    """
    document = await _document(session, document_id)
    owner = _owning_tenant(document, stage)
    rows = await _document_chunks(session, document_id)
    if not rows:
        logger.info("document %s has no chunks to extract from", document_id)
        report_stage_facts(
            entities=0,
            relations=0,
            projected_entities=0,
            projected_relations=0,
            entity_vectors=0,
            relation_vectors=0,
        )
        return {}
    extractor = _deps().resolve_extractor()
    entities_total = 0
    relations_total = 0
    extracted: list[Any] = []
    related: list[Any] = []
    # Which chunks each graph element was extracted from, keyed by the *normalised* label
    # so the mapping lines up with the rows the projection builds. This is LightRAG's
    # ``source_id``, and it is accumulated here because here is the only place that knows
    # it: by the time the extraction is flattened into ``extracted``/``related`` the chunk
    # it came from is gone. The chunk's key is the one the ``index`` stage published it
    # under, so the two indexes name the same passage.
    entity_sources: dict[str, list[str]] = {}
    relation_sources: dict[tuple[str, str], list[str]] = {}
    for chunk_id, content, meta in rows:
        entities, relations = await extractor.extract(content)
        entities_total += len(entities)
        relations_total += len(relations)
        extracted.extend(entities)
        related.extend(relations)
        chunk_key = chunk_source_id(owner, meta.get("content_id") or chunk_id)
        labels = {
            entity.id: normalised_label(entity.label)
            for entity in entities
            if normalised_label(entity.label)
        }
        for label in dict.fromkeys(labels.values()):
            entity_sources.setdefault(label, []).append(chunk_key)
        for relation in relations:
            src, tgt = labels.get(relation.src_id), labels.get(relation.tgt_id)
            if src and tgt and src != tgt:
                relation_sources.setdefault((src, tgt), []).append(chunk_key)
        await session.execute(
            update(Chunk)
            .where(Chunk.id == chunk_id)
            .values(
                meta={
                    **meta,
                    "extractor": extractor.name,
                    "entities": [
                        {"id": entity.id, "label": entity.label, "kind": entity.kind}
                        for entity in entities
                    ],
                    "relations": [
                        {
                            "src_id": relation.src_id,
                            "tgt_id": relation.tgt_id,
                            "phrase": relation.phrase,
                        }
                        for relation in relations
                    ],
                }
            )
        )
    logger.info(
        "extracted %d entities and %d relations from document %s with the %s extractor",
        entities_total,
        relations_total,
        document_id,
        extractor.name,
    )

    # The same source name the ``index`` stage tags its chunks with, so one document is
    # one provenance string across both stores rather than two spellings of itself.
    source = next(
        (str(meta["source"]) for _, _, meta in rows if meta.get("source")),
        document.filename,
    )
    try:
        projection = await _deps().project_graph(
            extracted,
            related,
            tenant_value=tenant_metadata_value(owner),
            source=source,
            extractor=extractor.name,
            entity_sources=entity_sources,
            relation_sources=relation_sources,
        )
    except GraphProjectionError as exc:
        raise ApplicationError(str(exc), type="GraphNotProjected") from exc
    if projection.skipped is not None:
        # An honest "there is no durable graph here", not a projection of zero. The two
        # demand opposite responses and are never collapsed into a number.
        logger.warning(
            "document %s extracted %d entities that were not projected: %s",
            document_id,
            entities_total,
            projection.skipped,
        )
    elif not projection.complete:
        raise ApplicationError(
            f"the graph stage handed the knowledge graph "
            f"{projection.attempted_nodes} entities and {projection.attempted_edges} "
            f"relations from document {document_id}, and the graph reports holding "
            f"{projection.nodes} and {projection.edges} of them. The extraction is on "
            "the chunk rows either way; what is missing is the half a person can see, "
            "and recording this stage as completed is how that stayed invisible.",
            type="GraphNotProjected",
        )

    # The graph is written; now make it *findable*. Gated on the projection having
    # actually run, because LightRAG's ``local`` arm matches an entity vector and then
    # looks that name up in the graph — a vector whose node is not there is dropped by
    # ``_get_node_data`` without comment, so publishing one would raise ``points_count``
    # and change nothing a query can see.
    vectors = GraphVectorResult(
        entities=None,
        relations=None,
        skipped=f"the graph itself was not written ({projection.skipped})",
    )
    if projection.skipped is None:
        vectors = await _deps().publish_graph_vectors(
            extracted,
            related,
            tenant_value=tenant_metadata_value(owner),
            source=source,
            extractor=extractor.name,
            entity_sources=entity_sources,
            relation_sources=relation_sources,
        )
        if not vectors.complete:
            # Not fatal, and not silent: see app.ingestion.graph_vectors on why this
            # degrades rather than failing an ingest whose durable stores are all correct.
            logger.warning(
                "document %s has %s of %d entity and %s of %d relation vector(s) in the "
                "graph index; the graph arm will not find the rest",
                document_id,
                vectors.entities,
                vectors.attempted_entities,
                vectors.relations,
                vectors.attempted_relations,
            )

    # Task 4.12b. The *counts* here; the entities and relations themselves are already
    # durable on ``chunks.meta``, which is what the log's own view reads them out of — so
    # this event stays a fixed size whether the document yielded nine entities or nine
    # thousand. ``projected_*`` are what the graph store confirmed holding, never what
    # was handed to it.
    facts: dict[str, Any] = {
        "entities": entities_total,
        "relations": relations_total,
        "extractor": extractor.name,
        "projected_entities": projection.nodes,
        "projected_relations": projection.edges,
        # What the *vector* store confirmed holding, on the same rule: a writer's own
        # count is a claim. ``None`` means the index could not be asked, and is never
        # rounded to zero — "the arm has nothing to find" and "we cannot tell" call for
        # opposite responses.
        "entity_vectors": vectors.entities,
        "relation_vectors": vectors.relations,
    }
    if vectors.skipped is not None:
        facts["graph_vectors"] = f"skipped: {vectors.skipped}"
    elif vectors.failed is not None:
        facts["graph_vectors"] = f"failed: {vectors.failed}"
    if projection.skipped is not None:
        facts["projection"] = f"skipped: {projection.skipped}"
    if projection.dropped_relations:
        facts["dropped_relations"] = projection.dropped_relations
    facts["chunks"] = len(rows)
    report_stage_facts(**facts)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────────────────────

#: Every declared stage, and the handler that performs it. Written out rather than
#: derived so that a stage added to :data:`aegis.jobs.INGEST_STAGES` without a handler is
#: a failure of :func:`register_ingest_handlers` at boot — the loud version of the same
#: gap the worker otherwise only warns about.
_HANDLERS = {
    "parse": parse_stage,
    "chunk": chunk_stage,
    "enrich": enrich_stage,
    "embed": embed_stage,
    "index": index_stage,
    "graph": graph_stage,
}


def register_ingest_handlers() -> None:
    """Register every ingest stage handler with the substrate.

    Called from the worker bootstrap, and safe to call twice (registration is a dict
    assignment) — a host that rebuilds its wiring re-registers rather than duplicating.

    Raises:
        RuntimeError: If the declared pipeline contains a stage this module has no
            handler for. A missing handler fails that stage's activity the first time a
            document reaches it, which is correct and far too late; the pipeline is a
            constant, so the mismatch can be found at boot instead.
    """
    declared = {spec.name for spec in INGEST_STAGES}
    missing = sorted(declared - set(_HANDLERS))
    if missing:
        raise RuntimeError(
            f"aegis.jobs.INGEST_STAGES declares {missing} but app.ingestion.stages has no "
            "handler for them; a document reaching one of those stages would fail its "
            "activity rather than record progress it did not make"
        )
    for name, handler in _HANDLERS.items():
        register_stage_handler(name, handler)
