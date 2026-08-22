"""Replay an existing corpus into the graph arm's two indexes, without re-extracting.

The gap this closes, stated once
--------------------------------

:mod:`app.ingestion.graph_vectors` made the ``graph`` stage write entity and relation
vectors, and :mod:`app.ingestion.graph_projection` now writes the ``source_id`` that turns
a matched entity into passages. Both run at *ingest*. Every document ingested before them
has neither — measured on the live deployment the day the vectors landed: 8 entity points,
all of them from **one** of 17 succeeded documents. A retrieval feature that only works on
documents uploaded after the fix is a demo, not a feature.

This module is the graph-side counterpart of
:func:`app.ingestion.vector_index.rebuild_dense_index`, and deliberately the same shape:
scoped by tenant or document, idempotent, and reporting what each store **confirmed
holding** rather than what was handed to it.

Why ``chunks.meta`` is the source and Neo4j is not
--------------------------------------------------

Two places hold this corpus's extraction and only one of them can reproduce the *inputs*
:func:`~app.ingestion.graph_projection.projection_rows` takes.

``chunks.meta`` holds, per chunk row, exactly what that chunk yielded — ``entities``
(``id``/``label``/``kind``), ``relations`` (``src_id``/``tgt_id``/``phrase``) and the
``extractor`` that produced them, written by the ``graph`` stage precisely so "the graph
this ingest built" is a fact about rows we own. Rebuilding from it reproduces the same
``Entity``/``Relation`` objects the stage passed, so the nodes and vectors this writes are
byte-identical to the ones a re-ingest would write — and, because the mapping is still
*per chunk*, it is the only source that can rebuild ``source_id`` at all.

Neo4j holds the *projection*, which is the same information after it has been merged,
deduplicated and unioned across documents. Reading it back would give entity names without
their extractor ids, descriptions already rewritten by the projection, and no way to say
which chunk any of it came from — so the relations could not be re-attached to endpoints
and ``source_id`` could not be rebuilt. It is a correct record of the graph and the wrong
record for replaying the write that made it.

What this costs, honestly
-------------------------

Unlike the dense rebuild, this one is **not** free. ``chunks.embedding`` is the embedding
of record for a chunk, so the dense index can be replayed without paying the provider
again; there is no equivalent stored vector for an entity, so
:func:`~app.ingestion.graph_vectors.publish_document_graph_vectors` embeds each element's
text as it goes. The texts are a name plus a one-line description, so a corpus of 17
documents is a handful of small batches — but it is a provider call, and a dry run exists
so an operator can see the size of it before spending it.

Nothing here re-parses, re-chunks, re-embeds a chunk or re-extracts. The extraction is read
off the rows; the LLM extractor is never called.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from aegis.retrieval.graph_extract import Entity, Relation
from aegis.retrieval.types import chunk_source_id, tenant_metadata_value
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.ingestion.graph_projection import (
    GraphProjectionError,
    ProjectionResult,
    normalised_label,
    project_document_graph,
)
from app.ingestion.graph_vectors import publish_document_graph_vectors
from app.retrieval.protocols import EmbedFn

logger = logging.getLogger(__name__)

__all__ = [
    "DocumentExtraction",
    "GraphIndexReport",
    "document_extractions",
    "eligible_status",
    "rebuild_graph_index",
]

#: Documents whose ingest ran to completion. A document still ``PENDING`` has no
#: extraction to replay, and one that ``FAILED`` has an extraction that was never accepted
#: — replaying either would put entities into the graph for a document the platform does
#: not consider ingested.
_ELIGIBLE_STATUS = "SUCCEEDED"


@dataclass(slots=True)
class DocumentExtraction:
    """One document's extraction, read back off its chunk rows.

    Attributes:
        document_id: The document.
        tenant_id: Its owning tenant. Every element written carries this, because it is
            the whole of the evidence deciding who may see the result.
        source: The document's source name, as the ``index`` stage tagged it.
        extractor: The extractor's honest name, read off the rows rather than assumed —
            it is embedded in every entity description, so a corpus extracted by two
            different extractors must not be replayed as if one had produced it.
        entities: Every entity the chunks yielded, duplicates included, exactly as the
            stage passed them.
        relations: Every relation, referring to entities by their extractor id.
        entity_sources: Chunk ids per normalised entity label — LightRAG's ``source_id``,
            and the only reason to rebuild from the chunk rows rather than from the graph.
        relation_sources: Chunk ids per ``(src label, tgt label)`` pair.
        chunks: How many chunk rows contributed.
    """

    document_id: int
    tenant_id: int
    source: str
    extractor: str
    entities: list[Entity] = field(default_factory=list)
    relations: list[Relation] = field(default_factory=list)
    entity_sources: dict[str, list[str]] = field(default_factory=dict)
    relation_sources: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    chunks: int = 0


@dataclass
class GraphIndexReport:
    """What a backfill did, in numbers that came back from the stores.

    Attributes:
        documents: Documents whose extraction was replayed.
        entities: Entity vectors the store confirmed holding, **summed over documents**
            rather than counted distinctly. One entity named by three documents is
            confirmed three times, because each document's write is separately owed an
            answer — so this number is legitimately larger than the collection's
            ``points_count`` (measured: 146 confirmations over 84 points), and it is not
            a claim about how many rows exist.
        relations: Relation vectors confirmed, likewise.
        projected_nodes: Graph nodes verified present carrying the document's tag.
        projected_edges: Graph edges verified present, likewise.
        unextracted: Eligible documents carrying no extraction on any chunk row. These
            need the ``graph`` stage, not this command; naming them keeps a partial
            backfill from reading as a complete one — the same rule ``unembedded`` follows
            in :class:`app.ingestion.vector_index.DenseIndexReport`.
        incomplete: ``(document_id, reason)`` for every document whose stores did not
            confirm everything sent. A backfill that quietly covers 12 of 17 documents is
            the same shape of lie as one that covers none.
        dry_run: Whether the counts are what the stores confirmed or what *would* be
            sent. Carried on the report rather than left to the caller, because
            ":func:`describe` says confirmed" and "nothing was written" cannot both be
            true and a reader has no way to tell them apart from the numbers.
    """

    documents: int = 0
    entities: int = 0
    relations: int = 0
    projected_nodes: int = 0
    projected_edges: int = 0
    unextracted: list[int] = field(default_factory=list)
    incomplete: list[tuple[int, str]] = field(default_factory=list)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        """Return the report as plain data, for a job result or a CLI dump."""
        return {
            "documents": self.documents,
            "entities": self.entities,
            "relations": self.relations,
            "projected_nodes": self.projected_nodes,
            "projected_edges": self.projected_edges,
            "unextracted": list(self.unextracted),
            "dry_run": self.dry_run,
            "incomplete": [
                {"document_id": doc, "reason": reason}
                for doc, reason in self.incomplete
            ],
        }

    def describe(self) -> str:
        """Return a one-line summary fit for a log line or an assertion message."""
        if self.dry_run:
            return (
                f"{self.documents} document(s) would be replayed: {self.entities} "
                f"entity and {self.relations} relation vector(s) to embed and publish; "
                f"{len(self.unextracted)} unextracted"
            )
        return (
            f"{self.documents} document(s): {self.entities} entity and "
            f"{self.relations} relation vector(s) confirmed, {self.projected_nodes} "
            f"node(s) and {self.projected_edges} edge(s) verified in the graph; "
            f"{len(self.unextracted)} unextracted, {len(self.incomplete)} incomplete"
        )


async def document_extractions(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    document_id: int | None = None,
) -> tuple[list[DocumentExtraction], list[int]]:
    """Read a scope's stored extractions back off ``chunks.meta``.

    Reproduces, from durable rows, exactly the four things
    :func:`app.ingestion.stages.graph_stage` computes before it writes: the flattened
    entity and relation lists, the chunk ids attributed per normalised label, the source
    name and the extractor. The attribution is rebuilt with the *same* expressions the
    stage uses — :func:`~aegis.retrieval.types.chunk_source_id` over
    ``meta["content_id"]`` falling back to the row id, and
    :func:`~app.ingestion.graph_projection.normalised_label` over the raw label — because
    a chunk id that differs from the one the ``index`` stage published under names a KV row
    that does not exist, and the entity resolves to no passage at all.

    Args:
        session: A session that can see the chunks in scope.
        tenant_id: Restrict to one tenant; ``None`` means every tenant this session sees.
        document_id: Restrict to one document.

    Returns:
        ``(extractions, unextracted_document_ids)``. A document appears in exactly one of
        the two — an eligible document whose rows carry no ``entities`` key was never run
        through the ``graph`` stage, which is a different problem with a different repair.
    """
    clauses = ["d.status = :status"]
    params: dict[str, Any] = {"status": _ELIGIBLE_STATUS}
    if tenant_id is not None:
        clauses.append("c.tenant_id = :tenant_id")
        params["tenant_id"] = tenant_id
    if document_id is not None:
        clauses.append("c.document_id = :document_id")
        params["document_id"] = document_id

    sql = text(
        f"""
        SELECT c.id, c.tenant_id, c.document_id, c.meta, d.filename
          FROM chunks c
          JOIN documents d ON d.id = c.document_id
         WHERE {" AND ".join(clauses)}
         ORDER BY c.document_id, c.id
        """  # noqa: S608 - clauses are literals chosen above, values stay bound
    )

    by_document: dict[int, DocumentExtraction] = {}
    seen: dict[int, tuple[int, str]] = {}
    for record in (await session.execute(sql, params)).mappings():
        doc = int(record["document_id"])
        meta = dict(record["meta"] or {})
        owner = int(record["tenant_id"])
        seen.setdefault(doc, (owner, str(record["filename"])))
        raw_entities = meta.get("entities")
        if not isinstance(raw_entities, list):
            continue
        entry = by_document.get(doc)
        if entry is None:
            entry = DocumentExtraction(
                document_id=doc,
                tenant_id=owner,
                source=str(meta.get("source") or record["filename"]),
                # The extractor recorded on the first extracted chunk. A document whose
                # chunks disagree is reported by the caller rather than silently averaged;
                # see :func:`rebuild_graph_index`.
                extractor=str(meta.get("extractor") or ""),
            )
            by_document[doc] = entry

        entities = [
            Entity(
                id=str(item["id"]),
                label=str(item.get("label", "")),
                kind=str(item.get("kind", "")),
            )
            for item in raw_entities
            if isinstance(item, dict) and item.get("id")
        ]
        relations = [
            Relation(
                src_id=str(item["src_id"]),
                tgt_id=str(item["tgt_id"]),
                phrase=str(item.get("phrase", "")),
            )
            for item in (meta.get("relations") or [])
            if isinstance(item, dict) and item.get("src_id") and item.get("tgt_id")
        ]
        entry.entities.extend(entities)
        entry.relations.extend(relations)
        entry.chunks += 1

        chunk_key = chunk_source_id(owner, meta.get("content_id") or record["id"])
        labels = {
            entity.id: normalised_label(entity.label)
            for entity in entities
            if normalised_label(entity.label)
        }
        for label in dict.fromkeys(labels.values()):
            entry.entity_sources.setdefault(label, []).append(chunk_key)
        for relation in relations:
            src, tgt = labels.get(relation.src_id), labels.get(relation.tgt_id)
            if src and tgt and src != tgt:
                entry.relation_sources.setdefault((src, tgt), []).append(chunk_key)

    unextracted = sorted(doc for doc in seen if doc not in by_document)
    return ([by_document[doc] for doc in sorted(by_document)], unextracted)


async def rebuild_graph_index(
    session: AsyncSession,
    *,
    tenant_id: int | None = None,
    document_id: int | None = None,
    embed: EmbedFn | None = None,
    dry_run: bool = False,
) -> GraphIndexReport:
    """Replay a scope's stored extraction into the graph and the graph vector index.

    Idempotent: the projection merges nodes on ``entity_id`` and edges on
    ``(src, tgt, owning file_path)``, and every vector point id is
    ``sha256(workspace + md5-seeded LightRAG key)`` — so a second run overwrites the same
    rows in both stores and a corpus that has not changed converges rather than growing.
    Nothing is deleted first, so a run that fails halfway leaves strictly more of the
    corpus reachable than it found.

    Both writes run per document, in the order the ``graph`` stage runs them and for the
    same reason: an entity vector whose node is not in the graph is dropped by
    ``_get_node_data`` without comment, so publishing one would raise ``points_count`` and
    change nothing a query can see.

    A document that fails is recorded and the backfill continues. The alternative — abort
    on the first Neo4j blip — leaves an operator with a partially rebuilt corpus and no
    list of what is missing, which is the state this command exists to get out of.

    Args:
        session: A session that can see the chunks in scope. Reading through the **admin**
            engine is the caller's job (see :mod:`app.ingestion.__main__`): an unbound
            serving session is scoped to no tenant, so the backfill would find nothing and
            report success.
        tenant_id: Restrict to one tenant.
        document_id: Restrict to one document.
        embed: The embedding function for the entity texts; ``None`` resolves the
            platform's, which is the same one the ``embed`` stage uses. Two widths in one
            collection is a write that succeeds and a query that never matches.
        dry_run: Read and report, write nothing and embed nothing. This is the honest way
            to ask what a backfill would cost before paying for it — unlike the dense
            rebuild, this one calls the embedding provider.

    Returns:
        The :class:`GraphIndexReport`, whose counts are what the stores confirmed.
    """
    extractions, unextracted = await document_extractions(
        session, tenant_id=tenant_id, document_id=document_id
    )
    report = GraphIndexReport(
        documents=len(extractions), unextracted=unextracted, dry_run=dry_run
    )
    if dry_run:
        for entry in extractions:
            report.entities += len(entry.entity_sources)
            report.relations += len(entry.relation_sources)
        logger.info(
            "graph index backfill (tenant=%s document=%s) would replay %d document(s); "
            "%d have no extraction on their rows",
            tenant_id,
            document_id,
            len(extractions),
            len(unextracted),
        )
        return report

    if embed is None:
        from app.retrieval.gateway import default_embed  # noqa: PLC0415 - lazy

        embed = default_embed()

    for entry in extractions:
        tenant_value = tenant_metadata_value(entry.tenant_id)
        if not entry.extractor:
            # The extractor's name is part of every entity description and therefore of
            # every vector. Guessing one would write points that do not match the ones a
            # re-ingest produces, which is a duplicate index wearing the right id.
            report.incomplete.append(
                (entry.document_id, "no extractor recorded on any chunk row")
            )
            continue
        try:
            projection = await project_document_graph(
                entry.entities,
                entry.relations,
                tenant_value=tenant_value,
                source=entry.source,
                extractor=entry.extractor,
                entity_sources=entry.entity_sources,
                relation_sources=entry.relation_sources,
            )
        except GraphProjectionError as exc:
            report.incomplete.append((entry.document_id, f"projection failed: {exc}"))
            continue
        _record_projection(report, entry, projection)
        if projection.skipped is not None or not projection.complete:
            continue

        vectors = await publish_document_graph_vectors(
            entry.entities,
            entry.relations,
            tenant_value=tenant_value,
            source=entry.source,
            extractor=entry.extractor,
            embed=embed,
            entity_sources=entry.entity_sources,
            relation_sources=entry.relation_sources,
        )
        report.entities += vectors.entities or 0
        report.relations += vectors.relations or 0
        if not vectors.complete:
            report.incomplete.append(
                (
                    entry.document_id,
                    vectors.skipped
                    or vectors.failed
                    or (
                        f"the index confirmed {vectors.entities} of "
                        f"{vectors.attempted_entities} entity and {vectors.relations} of "
                        f"{vectors.attempted_relations} relation vector(s)"
                    ),
                )
            )

    logger.info(
        "graph index backfill (tenant=%s document=%s): %s",
        tenant_id,
        document_id,
        report.describe(),
    )
    return report


def _record_projection(
    report: GraphIndexReport,
    entry: DocumentExtraction,
    projection: ProjectionResult,
) -> None:
    """Fold one document's projection outcome into ``report``.

    Args:
        report: The running report.
        entry: The document being replayed.
        projection: What the graph store confirmed.
    """
    report.projected_nodes += projection.nodes
    report.projected_edges += projection.edges
    if projection.skipped is not None:
        report.incomplete.append(
            (entry.document_id, f"projection skipped: {projection.skipped}")
        )
    elif not projection.complete:
        report.incomplete.append(
            (
                entry.document_id,
                f"the graph confirmed {projection.nodes} of "
                f"{projection.attempted_nodes} node(s) and {projection.edges} of "
                f"{projection.attempted_edges} edge(s)",
            )
        )


def eligible_status() -> str:
    """Return the document status this backfill replays.

    Exposed so a test asserts the scope against one constant rather than a literal
    repeated in the assertion and the query.

    Returns:
        The status name.
    """
    return _ELIGIBLE_STATUS
