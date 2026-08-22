"""Project one document's extraction into the **durable** knowledge graph.

The ``graph`` stage extracts entities and relations and writes them to ``chunks.meta``
— a tenant-scoped, RLS-protected row we own. That is the right record of *what this
ingest extracted*, and it is what :mod:`app.ingestion.progress` reads. It is not,
however, the graph the product shows: ``GET /v1/graph`` reads Neo4j (the durable graph
LightRAG's storage contract defines) unioned with the in-process per-run slice, and
neither of those ever looked at ``chunks.meta``.

The measured consequence was a silent success. A real upload finished with the ``graph``
stage ``completed`` and ``{"entities": 1, "relations": 0}`` on its event, while Neo4j held
78 nodes before the upload and 78 after, and the Graph screen showed 59 nodes before and
59 after. The extraction existed; nothing the product can show did. This module is the
missing half of that stage.

It also closes the gap that made the projection necessary in the first place.
:meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.ingest_chunks` deliberately takes
the ``publish_vectors`` route for chunks that already carry their embedding of record —
which is correct (see that method's docstring: feeding finished chunks back through
``ainsert`` made LightRAG reject 36 of 37 as duplicate documents) — but it means
``ainsert`` never runs, so **LightRAG's own extractor never writes a node**. Every
non-demo node in that graph was missing because nothing was ever going to write one.

What is written, and why in exactly this shape
----------------------------------------------

Nodes and relationships are written under LightRAG's own Neo4j storage contract — the
workspace label, ``entity_id`` as the merge key, ``entity_type``, ``file_path``,
``keywords`` — because the reader is LightRAG's ``get_knowledge_graph("*")`` and a node
written any other way is a node the visualisation never sees. The formats are restated
here rather than imported for the same reason :mod:`app.demo_graph` restates them: they
are a *storage layout* this module has to produce byte-compatibly, not an API it calls.

**Provenance is the security control, not a label.** Every node and every edge carries a
tenant-tagged ``file_path`` (``t7::refund-policy.pdf``), which is the only evidence
:func:`aegis.retrieval.types.scoped_graph` has for deciding who may see it — an element
whose owner cannot be established is deliberately shown to nobody. So:

* A node's ``file_path`` **unions** its contributors, exactly as LightRAG merges an
  entity seen in several documents. A node survives if *any* owner is visible, and an
  entity name that appears in a tenant's own corpus is a name that tenant already knows.
* An edge is keyed by ``(source, target, owning file_path)`` and therefore stays
  **single-owner**. Its ``keywords`` is a phrase lifted from a specific document, so
  merging two tenants' edges would either put one tenant's sentence behind the other's
  provenance (a leak) or make the edge invisible to both under ``scoped_graph``'s
  all-owners rule (a loss). Two tenants that state the same relation get one edge each.
* Node ``description`` deliberately carries **no document prose** — only the entity's
  kind and the extractor that produced it. A merged node's description is overwritten by
  whoever writes last, and a description built from document text would hand that text to
  the other tenants who merged into the node.

``source_id`` is what makes an entity quotable
----------------------------------------------

A node also carries ``source_id``: the ``<SEP>``-joined **chunk ids** the entity was
extracted from. It is not decoration and it is not provenance for a human — it is the
single field ``lightrag.operate._find_related_text_unit_from_entities`` walks to turn a
matched entity into passages, and it reads it off the *node*, not off the vector payload.
Written without it, the graph arm found entities and returned no text at all: the function
logged ``No entities with text chunks found`` and returned ``[]``, and the arm contributed
**0 candidates** to every merged ranking. That is measured on the live deployment, after
the entity vectors were already correct — the arm reported ``Local query: 5 entites, 9
relations`` in the same run.

Like ``file_path``, a node's ``source_id`` **unions** its contributors, because a merged
entity really was extracted from all of them and LightRAG merges it the same way. That
does mean a node shared by two tenants points at both tenants' chunks — which is safe for
the reason the whole arm is safe, and only for that reason: every chunk the lookup returns
carries its own tenant-tagged ``file_path`` from the KV row
(:mod:`app.ingestion.chunk_kv`), and
:func:`aegis.retrieval.lightrag_backend._scoped_recall` drops the ones the asking tenant
does not own. The union is what LightRAG's storage contract says; the tag on each passage
is what enforces the boundary. An edge is single-owner already, so its ``source_id`` is
simply set.

**The write is verified.** Nothing here trusts that a Cypher statement that returned
without raising wrote anything: the projection reads back the nodes and edges carrying
*this* document's tag and reports what it actually found, which is the number the stage
records. That is the same rule ``index`` learned when it logged ``{"indexed": 37}``
against an empty collection.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from aegis.retrieval.graph_extract import Entity, Relation

logger = logging.getLogger(__name__)

__all__ = [
    "GraphProjectionError",
    "ProjectionResult",
    "joined_sources",
    "normalised_label",
    "project_document_graph",
    "projection_rows",
    "tagged_source",
]

#: LightRAG's joiner for a merged element's several sources. Restated, not imported: it
#: is a storage format this module must produce byte-compatibly (see the module
#: docstring), and :func:`aegis.retrieval.lightrag_backend._owners_of` splits on it to
#: recover provenance.
_GRAPH_FIELD_SEP = "<SEP>"

#: Separator between the tenant tag and the source name inside one ``file_path``.
_TENANT_TAG_SEP = "::"

#: The tenant tag marking the shared, tenant-less corpus. An *untagged* path means
#: "owner unknown" and is refused to every tenant-scoped caller, which is why the shared
#: corpus has to say so explicitly.
_SHARED_TAG = "shared"

#: The relationship type LightRAG's Neo4j storage writes and its reader matches. Ours
#: must be the same type or the edge is invisible to ``get_knowledge_graph``.
_REL_TYPE = "DIRECTED"

#: Property naming the single document+tenant an edge was extracted from. It is the
#: edge's merge key, which is what keeps an edge single-owner (see the module docstring).
_EDGE_SOURCE_PROP = "aegis_source"

#: Property recording which extractor produced an element, so a deterministically
#: extracted graph is never mistaken for an LLM-extracted one after the fact.
_EXTRACTOR_PROP = "aegis_extractor"


class GraphProjectionError(RuntimeError):
    """The graph store was expected to be writable and the projection failed anyway.

    Distinct from "the projection was not attempted": a deployment with no graph store
    (``STORES=off``, no ``NEO4J_URI``, a test process with no scratch instance) reports an
    honest *skipped* and its ingest completes. This error means the store was there, the
    write was owed, and it did not happen — which the stage must not record as a success.
    """


@dataclass(frozen=True, slots=True)
class ProjectionResult:
    """What reached the durable graph, counted by reading it back.

    Attributes:
        nodes: Entities verified present in the graph carrying this document's tag.
        edges: Relations verified present, likewise.
        attempted_nodes: Distinct entities the projection tried to write.
        attempted_edges: Distinct relations it tried to write, after dropping any whose
            endpoints this document did not also extract.
        dropped_relations: Relations discarded because an endpoint was not among the
            entities — a dangling edge is not written and is never invented.
        skipped: Why nothing was attempted, or ``None`` when the projection ran. A real
            answer: "this deployment has no durable graph" and "the graph is empty" are
            different facts and are never collapsed.
    """

    nodes: int = 0
    edges: int = 0
    attempted_nodes: int = 0
    attempted_edges: int = 0
    dropped_relations: int = 0
    skipped: str | None = None

    @property
    def complete(self) -> bool:
        """True when everything attempted was verified present afterwards."""
        return self.nodes >= self.attempted_nodes and self.edges >= self.attempted_edges


def _workspace_label() -> str:
    """Return the Neo4j label LightRAG stores this deployment's graph under.

    Computed exactly as ``lightrag.kg.neo4j_impl`` computes it, because a node written
    under a different label is a node the reader's ``MATCH`` never returns.

    That resolution has **three** steps, and this function used to implement two of
    them: ``NEO4J_WORKSPACE`` wins if set, *else the workspace LightRAG was constructed
    with* — which Aegis threads through from ``WORKSPACE`` — else ``"base"``. Dropping
    the middle step is not a cosmetic difference: a deployment that sets ``WORKSPACE``
    and not ``NEO4J_WORKSPACE`` (the shape every ``.env`` here uses to isolate a run)
    had its reader looking under that workspace's label while this writer labelled
    everything ``base``. The whole knowledge graph was written, and none of it was
    visible — ``GET /graph`` answered ``{"nodes": [], "edges": []}`` over a Neo4j
    holding 122 nodes and 272 edges.
    """
    return (
        os.environ.get("NEO4J_WORKSPACE", "").strip()
        or os.environ.get("WORKSPACE", "").strip()
        or "base"
    )


def _quoted(label: str) -> str:
    """Return ``label`` escaped for use inside Cypher backticks."""
    return label.replace("`", "``")


def tagged_source(source: str, tenant_value: str | None) -> str:
    """Return the ``file_path`` value carrying this element's owning tenant.

    Args:
        source: The real source path/filename the entity was extracted from.
        tenant_value: The owning tenant's metadata value (``t7``), or ``None`` for the
            genuinely shared corpus.

    Returns:
        The tagged path, e.g. ``"t7::refund-policy.pdf"``.
    """
    tag = _SHARED_TAG if tenant_value is None else tenant_value
    return f"{tag}{_TENANT_TAG_SEP}{source}"


def normalised_label(label: str) -> str:
    """Return the surface form the graph stores an entity under.

    One function rather than an expression inlined wherever a label is needed, because
    three separate places now have to agree on it byte-for-byte: the node's ``entity_id``
    in Neo4j, the ``entity_name`` in the vector point
    (:mod:`app.ingestion.graph_vectors`), and the key any caller attributes chunk ids
    under. LightRAG's ``local`` arm matches a vector, reads the name off it and looks
    *that string* up in the graph — a node it cannot find is dropped silently, so a lone
    extra space in one of the three is a vector that matches and contributes nothing.

    Args:
        label: The extractor's raw label.

    Returns:
        The label with internal whitespace collapsed and the ends stripped; ``""`` when
        the label was whitespace only, which the callers drop rather than store.
    """
    return " ".join(label.split())


def joined_sources(mapping: Mapping[Any, Sequence[str]] | None, key: Any) -> str:  # noqa: ANN401
    """Return the ``<SEP>``-joined chunk ids for one graph element, or ``""``.

    One function for both the node's ``source_id`` and the vector payload's, because the
    two must be the same string: :mod:`app.ingestion.graph_vectors` reads this value off
    the row this module built rather than joining a second time.

    Args:
        mapping: Chunk ids per element, or ``None`` when the caller could not attribute
            them.
        key: The element's key in that mapping.

    Returns:
        LightRAG's ``source_id`` string, duplicates collapsed in first-seen order. Empty
        rather than invented — the field names chunk ids, and anything else in that slot
        is a fabrication of shape that resolves to no passage.
    """
    if not mapping:
        return ""
    return _GRAPH_FIELD_SEP.join(dict.fromkeys(mapping.get(key) or ()))


def projection_rows(
    entities: Sequence[Entity],
    relations: Sequence[Relation],
    *,
    tenant_value: str | None,
    source: str,
    extractor: str,
    created_at: int | None = None,
    entity_sources: Mapping[str, Sequence[str]] | None = None,
    relation_sources: Mapping[tuple[str, str], Sequence[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Shape one document's extraction into the Neo4j rows to write.

    Pure, so the storage contract this module has to honour — the tenant tag, the merge
    keys, the refusal to write a dangling edge — is assertable without a graph store.

    Entities are deduplicated by their extractor id (``kind:normalised label``), so the
    same name found on nine chunks is one node; the node's own ``entity_id`` is the human
    surface form, because that is LightRAG's merge key and the label the console renders.

    Relations are addressed by entity id and rewritten to those surface forms. One whose
    endpoint is not among ``entities`` is **dropped**, not repaired: an edge to an entity
    this document never stated would be an invented fact.

    Args:
        entities: The entities extracted from the document's chunks, in any order.
        relations: The relations, referring to entities by :attr:`Entity.id`.
        tenant_value: The owning tenant's metadata value, or ``None`` for the shared
            corpus.
        source: The document's source name, as the ``index`` stage tags it.
        extractor: The extractor's honest ``name``.
        created_at: Unix seconds stamped on the written elements; defaults to now.
        entity_sources: Chunk ids per normalised entity label. Becomes the node's
            ``source_id`` — the only route from a matched entity to a passage (see the
            module docstring). A caller that cannot attribute chunks passes ``None`` and
            gets ``""``, which is an entity the arm can find and cannot quote.
        relation_sources: Chunk ids per ``(src label, tgt label)`` pair, likewise.

    Returns:
        ``(node_rows, edge_rows, dropped_relations)``.
    """
    stamp = (
        created_at
        if created_at is not None
        else int(datetime.now().timestamp())  # noqa: DTZ005 - LightRAG stores local
    )
    file_path = tagged_source(source, tenant_value)

    labels: dict[str, str] = {}
    kinds: dict[str, str] = {}
    for entity in entities:
        label = normalised_label(entity.label)
        if not label:
            continue
        # First mention wins the surface form, so a re-run over unchanged text writes the
        # same node rather than flip-flopping between two casings of the same name.
        labels.setdefault(entity.id, label)
        kinds.setdefault(entity.id, entity.kind)

    node_rows = [
        {
            "entity_id": label,
            "entity_type": kinds[entity_id],
            # No document prose, deliberately: see the module docstring.
            "description": f"{kinds[entity_id]} extracted by the {extractor} extractor",
            "file_path": file_path,
            "source_id": joined_sources(entity_sources, label),
            "created_at": stamp,
            "extractor": extractor,
        }
        for entity_id, label in labels.items()
    ]

    edge_rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    dropped = 0
    for relation in relations:
        src = labels.get(relation.src_id)
        tgt = labels.get(relation.tgt_id)
        phrase = relation.phrase.strip()
        if src is None or tgt is None or src == tgt or not phrase:
            dropped += 1
            continue
        if (src, tgt) in seen:
            continue  # one edge per pair per document; the first phrase is the record
        seen.add((src, tgt))
        edge_rows.append(
            {
                "src": src,
                "tgt": tgt,
                "keywords": phrase,
                "description": phrase,
                "file_path": file_path,
                "source_id": joined_sources(relation_sources, (src, tgt)),
                "created_at": stamp,
                "extractor": extractor,
            }
        )
    return (node_rows, edge_rows, dropped)


def _unavailable() -> str | None:
    """Return why the durable graph must not be written, or ``None`` when it may be.

    Three refusals, and each is a deliberate configuration rather than a failure:

    * **A test process.** Every other store the suite touches is isolated per run; there
      is no scratch Neo4j, so a test that projected here would write into whatever graph
      the developer was looking at. :mod:`app.demo_graph` refuses for the same reason.
    * **Databaseless mode.** ``STORES=off`` serves the in-process graph slice and has no
      durable graph to project into.
    * **No configured graph.** ``NEO4J_URI`` empty, or the driver not installed.
    """
    if "PYTEST_CURRENT_TEST" in os.environ:
        return "test process (no scratch Neo4j to isolate the write)"
    from app.config import get_settings  # noqa: PLC0415 - runtime-only dependency

    settings = get_settings()
    if not settings.stores_enabled:
        return "STORES=off (this deployment has no durable graph)"
    if not settings.neo4j_uri:
        return "NEO4J_URI is not configured"
    try:
        import neo4j  # noqa: F401, PLC0415 - presence check only
    except ImportError as exc:  # pragma: no cover - the driver is a hard dependency
        return f"the neo4j driver is not installed: {exc}"
    return None


def _driver() -> Any:  # noqa: ANN401 - the neo4j AsyncDriver, imported lazily
    """Return an async Neo4j driver built from the platform's own settings."""
    from neo4j import AsyncGraphDatabase  # noqa: PLC0415 - runtime-only dependency

    from app.config import get_settings  # noqa: PLC0415

    settings = get_settings()
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


#: Merge one entity into the durable graph, unioning its provenance.
#:
#: ``ON MATCH`` never overwrites another writer's classification and never *replaces* a
#: ``file_path``: it appends this document's tag when it is not already there. Replacing
#: it would silently transfer a node another tenant contributed to this one.
#:
#: ``source_id`` unions the same way, one *chunk id at a time* rather than one string at a
#: time — ``row.source_id`` is itself a ``<SEP>``-joined list, so the membership test has
#: to look inside it. ``reduce`` does that in plain Cypher; the alternative was APOC, which
#: is a plugin this deployment must not require. Replacing instead of unioning would drop
#: an earlier document's chunks from a shared entity, and the passage it can no longer
#: quote is exactly the failure this field exists to end.
_MERGE_NODES = """
UNWIND $rows AS row
MERGE (n:`{label}` {{entity_id: row.entity_id}})
ON CREATE SET n.entity_type = row.entity_type,
              n.description = row.description,
              n.file_path = row.file_path,
              n.source_id = row.source_id,
              n.created_at = row.created_at,
              n.{extractor_prop} = row.extractor
ON MATCH SET  n.entity_type = coalesce(n.entity_type, row.entity_type),
              n.description = coalesce(n.description, row.description),
              n.file_path = CASE
                  WHEN n.file_path IS NULL OR n.file_path = '' THEN row.file_path
                  WHEN row.file_path IN split(n.file_path, $sep) THEN n.file_path
                  ELSE n.file_path + $sep + row.file_path
              END,
              n.source_id = CASE
                  WHEN row.source_id IS NULL OR row.source_id = '' THEN n.source_id
                  WHEN n.source_id IS NULL OR n.source_id = '' THEN row.source_id
                  ELSE reduce(
                      acc = n.source_id, s IN split(row.source_id, $sep) |
                      CASE WHEN s = '' OR s IN split(acc, $sep)
                           THEN acc ELSE acc + $sep + s END
                  )
              END
"""

#: Merge one relation, keyed by its single owning source so it can never become
#: multi-owner (an all-owners edge is visible to nobody; see the module docstring).
_MERGE_EDGES = """
UNWIND $rows AS row
MATCH (a:`{label}` {{entity_id: row.src}})
MATCH (b:`{label}` {{entity_id: row.tgt}})
MERGE (a)-[r:{rel_type} {{{source_prop}: row.file_path}}]->(b)
SET r.keywords = row.keywords,
    r.description = row.description,
    r.file_path = row.file_path,
    r.source_id = row.source_id,
    r.weight = 1.0,
    r.created_at = row.created_at,
    r.{extractor_prop} = row.extractor
"""

#: Read back the nodes that genuinely carry this document's tag. The evidence, as
#: opposed to the write call having returned.
_COUNT_NODES = """
UNWIND $rows AS row
MATCH (n:`{label}` {{entity_id: row.entity_id}})
WHERE row.file_path IN split(coalesce(n.file_path, ''), $sep)
RETURN count(n) AS present
"""

_COUNT_EDGES = """
UNWIND $rows AS row
MATCH (a:`{label}` {{entity_id: row.src}})
      -[r:{rel_type} {{{source_prop}: row.file_path}}]->
      (b:`{label}` {{entity_id: row.tgt}})
RETURN count(r) AS present
"""


async def project_document_graph(
    entities: Sequence[Entity],
    relations: Sequence[Relation],
    *,
    tenant_value: str | None,
    source: str,
    extractor: str,
    entity_sources: Mapping[str, Sequence[str]] | None = None,
    relation_sources: Mapping[tuple[str, str], Sequence[str]] | None = None,
) -> ProjectionResult:
    """Write one document's extraction into the durable graph and verify it landed.

    Idempotent: nodes merge on ``entity_id`` and edges on
    ``(source, target, owning file_path)``, so a re-ingest of unchanged text converges on
    the same graph rather than accumulating duplicates.

    Args:
        entities: Every entity the document's chunks yielded (duplicates fine).
        relations: Every relation, referring to entities by :attr:`Entity.id`.
        tenant_value: The owning tenant's metadata value, or ``None`` for the shared
            corpus. This is what decides who may see the result; it is never omitted.
        source: The document's source name, tagged into ``file_path``.
        extractor: The extractor's honest ``name``, recorded on every element.
        entity_sources: Chunk ids per normalised entity label, unioned into each node's
            ``source_id``. Omitted, the entity is findable and not quotable — see the
            module docstring for the measured symptom.
        relation_sources: Chunk ids per ``(src label, tgt label)`` pair, likewise.

    Returns:
        What was attempted and what was verified present afterwards.

    Raises:
        GraphProjectionError: When the graph store is configured and reachable-in-
            principle but the write or the read-back failed. Never raised for a
            deployment that has no durable graph — that is reported as ``skipped``.
    """
    reason = _unavailable()
    if reason is not None:
        return ProjectionResult(skipped=reason)

    node_rows, edge_rows, dropped = projection_rows(
        entities,
        relations,
        tenant_value=tenant_value,
        source=source,
        extractor=extractor,
        entity_sources=entity_sources,
        relation_sources=relation_sources,
    )
    if not node_rows:
        return ProjectionResult(dropped_relations=dropped)

    label = _quoted(_workspace_label())
    fmt = {
        "label": label,
        "rel_type": _REL_TYPE,
        "source_prop": _EDGE_SOURCE_PROP,
        "extractor_prop": _EXTRACTOR_PROP,
    }
    driver = _driver()
    try:
        async with driver.session() as session:
            await (
                await session.run(_MERGE_NODES.format(**fmt), rows=node_rows, sep=_GRAPH_FIELD_SEP)
            ).consume()
            if edge_rows:
                await (
                    await session.run(_MERGE_EDGES.format(**fmt), rows=edge_rows)
                ).consume()
            nodes_present = (
                await (
                    await session.run(
                        _COUNT_NODES.format(**fmt), rows=node_rows, sep=_GRAPH_FIELD_SEP
                    )
                ).single()
            )["present"]
            edges_present = 0
            if edge_rows:
                edges_present = (
                    await (
                        await session.run(_COUNT_EDGES.format(**fmt), rows=edge_rows)
                    ).single()
                )["present"]
    except Exception as exc:  # noqa: BLE001 - every driver failure is the same outcome
        raise GraphProjectionError(
            f"projecting {len(node_rows)} entities and {len(edge_rows)} relations of "
            f"'{source}' into the knowledge graph failed: {exc}"
        ) from exc
    finally:
        await driver.close()

    return ProjectionResult(
        nodes=int(nodes_present),
        edges=int(edges_present),
        attempted_nodes=len(node_rows),
        attempted_edges=len(edge_rows),
        dropped_relations=dropped,
    )
