"""The six ingest stage handlers — the work Phase 3's substrate calls.

:mod:`aegis.jobs.stages` declares *what* the stages are; :mod:`app.jobs.activities` owns
the transaction, the tenant scope, the replay short-circuit and the ``completed_stage``
bump. What is left is the domain work, and it is here:

===========  ==============  ==========================================================
Stage        Queue           What it does
===========  ==============  ==========================================================
``parse``    ``aegis-cpu``   Docling reads the stored bytes into a structured tree, and
                             the tree is written beside them as the parse artifact.
                             Returns ``page_count`` and the derived ``title``.
``chunk``    ``aegis-...``   Packs the parsed sections into ``chunks`` rows, with the
                             D7 prefix, the page/bbox spans and the tenant on every row.
                             Returns ``chunk_count``.
``enrich``   ``aegis-...``   Folds that prefix into the text that is embedded and
                             full-text indexed — one guarded ``UPDATE``.
``embed``    ``aegis-io``    Embeds each chunk and writes ``chunks.embedding``, the
                             durable source-of-record vector.
``index``    ``aegis-...``   Publishes the chunks to the configured knowledge backend,
                             which is what makes them reachable by the dense arm.
``graph``    ``aegis-cpu``   Extracts entities and relations and records them on the
                             chunk, so the graph an ingest built is a fact about rows we
                             own rather than only about a store we do not.
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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aegis.ingestion import ParsedDocument, parse_pdf
from aegis.ingestion.blocks import BlockKind
from aegis.jobs.models import Chunk, Document
from aegis.jobs.stages import INGEST_STAGES, register_stage_handler
from aegis.retrieval.chunker import DocumentContext, SectionChunk, chunk_sections
from aegis.retrieval.graph_extract import Extractor, build_extractor
from aegis.retrieval.models import Chunk as RetrievalChunk
from aegis.retrieval.protocols import EmbedFn
from aegis.retrieval.types import TENANT_METADATA_KEY, tenant_metadata_value
from sqlalchemy import delete, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.exceptions import ApplicationError

from app.ingestion.artifacts import dumps_parsed, loads_parsed
from app.ingestion.store import DocumentStore

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
        publish: Coroutine taking the document's chunks and writing them into the
            knowledge backend the platform searches. ``None`` resolves the process
            retriever's backend on first use.
        extractor: The entity/relation extractor. ``None`` builds the best available one
            (:func:`aegis.retrieval.graph_extract.build_extractor`), whose ``name`` is
            honest about which one actually ran.
    """

    store: DocumentStore
    embed: EmbedFn | None = None
    publish: Any = None  # noqa: ANN401 - a coroutine fn; see publish_chunks
    extractor: Extractor | None = None

    def resolve_embed(self) -> EmbedFn:
        """Return the embedding function, resolving the platform default on first use."""
        if self.embed is None:
            from app.retrieval.gateway import default_embed  # noqa: PLC0415 - lazy

            self.embed = default_embed()
        return self.embed

    def resolve_extractor(self) -> Extractor:
        """Return the entity/relation extractor, building the default on first use."""
        if self.extractor is None:
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


def _derive_title(parsed: ParsedDocument, filename: str) -> str:
    """Return the document's title: its first heading, else the file name's stem.

    The first heading is what Docling's ``title`` label maps to and is the real printed
    title on every fixture in ``tests/fixtures/pdfs``. The fallback is deliberately the
    **row's** file name and not :attr:`ParsedDocument.source_name`: the parser is handed a
    content-addressed path in the document store, so its idea of the source name is a
    SHA-256 — a true fact about the file and a useless title. The tenant's own file name
    is the weakest *honest* title available, and inventing one with a model call is
    exactly the cost D7 exists to avoid.

    Args:
        parsed: The parse output.
        filename: ``documents.filename`` — the name the tenant uploaded it under.

    Returns:
        The title, never empty.
    """
    heading = next(
        (
            block.text.strip()
            for block in parsed.blocks
            if block.kind is BlockKind.HEADING and block.text.strip()
        ),
        "",
    )
    return heading or Path(filename).stem or filename


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


def _chunk_meta(chunk: SectionChunk, *, document: Document, parser: str) -> dict[str, Any]:
    """Return the provenance recorded on one ``chunks`` row.

    Everything a citation needs and nothing a query has to re-derive: where the text sits
    in the document (ordinal, section, word span), where it sits on the page (spans, each
    with the union of its blocks' boxes), the prefix that will be folded into the text by
    ``enrich``, the content-addressed id the dense index is keyed by, and which parser
    produced it — because chunks from two Docling versions are not interchangeable.

    Args:
        chunk: The packed chunk.
        document: The row it belongs to.
        parser: The parser name and version recorded on the parse.

    Returns:
        A JSON-serialisable mapping for ``chunks.meta``.
    """
    return {
        "ordinal": chunk.ordinal,
        "section": chunk.section,
        "prefix": chunk.prefix,
        "word_start": chunk.word_start,
        "word_count": chunk.word_count,
        "content_id": chunk.content_id(),
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

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document to parse.
        stage: The stage name (``"parse"``).

    Returns:
        ``page_count`` and ``title``, applied by the substrate with the stage bump.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or its bytes
            are not in the store. Neither is fixed by trying again, and retrying a parse
            burns the stage's whole attempt budget rediscovering the same absence.
    """
    document = await _document(session, document_id)
    store = _deps().store
    path = store.path_for(
        tenant_id=document.tenant_id, sha256=document.content_sha256
    )
    if not path.is_file():
        raise _fatal(
            f"document {document_id} has no stored bytes at {path}: the upload did not "
            "complete, or this worker cannot see the document store the API wrote to",
            kind="DocumentBytesMissing",
        )
    # Docling is blocking, CPU-bound and minutes long on a large document. Off the loop,
    # so the activity's heartbeat keeps beating and the orchestrator does not conclude
    # this worker died in the middle of the most expensive stage to redo.
    parsed: ParsedDocument = await asyncio.to_thread(parse_pdf, path)
    store.put_artifact(
        tenant_id=document.tenant_id,
        sha256=document.content_sha256,
        payload=dumps_parsed(parsed),
    )
    logger.info(
        "parsed document %s: %d page(s), %d block(s), %d table(s), OCR %s (%s) in %.1fs",
        document_id,
        parsed.page_count,
        len(parsed.blocks),
        parsed.table_count,
        "on" if parsed.ocr.enabled else "off",
        parsed.ocr.reason,
        parsed.parse_seconds,
    )
    return {
        "page_count": parsed.page_count,
        "title": _derive_title(parsed, document.filename),
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

    pieces = chunk_sections(parsed, context=_document_context(document))
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
                    "content": piece.text,
                    "embedding": _UNEMBEDDED,
                    "meta": _chunk_meta(piece, document=document, parser=parsed.parser),
                }
                for piece in pieces
            ],
        )
    logger.info("chunked document %s into %d chunk(s)", document_id, len(pieces))
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
    that is actually embedded and indexed. Measured value: Context@5 33.3% → 55.0%.

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
    mode, the per-tenant Chroma collections in lite mode), and this is the call that puts
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

    Returns:
        An empty mapping: what this stage produced lives in the backend's index, and the
        row already records the chunk count that was published.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant.
    """
    document = await _document(session, document_id)
    owner = _owning_tenant(document, stage)
    rows = await _document_chunks(session, document_id)
    if not rows:
        logger.info("document %s has no chunks to index", document_id)
        return {}
    owner_token = tenant_metadata_value(owner)
    published = [
        RetrievalChunk(
            id=f"{owner_token}:{meta.get('content_id') or chunk_id}",
            doc_id=str(document_id),
            ordinal=int(meta.get("ordinal", 0)),
            text=content,
            metadata={
                **meta,
                TENANT_METADATA_KEY: owner_token,
                "document_id": document_id,
                "source": meta.get("source") or document.filename,
            },
        )
        for chunk_id, content, meta in rows
    ]
    await _deps().publish_chunks(published)
    logger.info("indexed %d chunk(s) of document %s", len(published), document_id)
    return {}


# ─────────────────────────────────────────────────────────────────────────────
# graph
# ─────────────────────────────────────────────────────────────────────────────


async def graph_stage(
    session: AsyncSession, *, tenant_id: int | None, document_id: int, stage: str
) -> Mapping[str, Any]:
    """Extract the entities and relations each chunk states, and record them on the row.

    The extraction is written to ``chunks.meta`` — a tenant-scoped, RLS-protected row we
    own — rather than only into a graph store we do not. That is what makes "the graph
    this ingest built" answerable with the graph store unreachable, and it is the record
    the ingest log is projected from. The extractor's own ``name`` is recorded beside its
    output, so a corpus extracted by the deterministic extractor is never mistaken for one
    an LLM extracted.

    Idempotent: the write is an ``UPDATE`` per chunk id that replaces those metadata keys
    outright, so a second run over the same text leaves the same single set of entities.

    Args:
        session: The scoped session, inside this stage's transaction.
        tenant_id: The tenant the substrate bound the scope to.
        document_id: The document whose chunks to extract from.
        stage: The stage name (``"graph"``).

    Returns:
        An empty mapping.

    Raises:
        ApplicationError: Non-retryable, when the document is not visible or owns no
            tenant.
    """
    document = await _document(session, document_id)
    _owning_tenant(document, stage)
    rows = await _document_chunks(session, document_id)
    if not rows:
        logger.info("document %s has no chunks to extract from", document_id)
        return {}
    extractor = _deps().resolve_extractor()
    entities_total = 0
    relations_total = 0
    for chunk_id, content, meta in rows:
        entities, relations = await extractor.extract(content)
        entities_total += len(entities)
        relations_total += len(relations)
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
