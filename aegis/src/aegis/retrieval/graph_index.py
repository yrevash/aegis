r"""Publish entity and relation vectors LightRAG's graph-aware arm can actually read.

The defect this module ends, stated once
----------------------------------------

Hybrid retrieval runs two arms and fuses them
(:meth:`aegis.retrieval.lightrag_backend.LightRAGBackend.recall_ranked`): ``naive`` over
the dense chunk vectors, and ``local`` over the entity neighbourhood. The ``local`` arm
begins by querying ``entities_vdb`` (``lightrag.operate._get_node_data``), and on the
live deployment that collection held **0 points** while ``lightrag_vdb_chunks`` held 164.
``_get_node_data`` returns ``[], []`` for an empty result set, so ``_build_query_context``
returned ``None`` and the graph arm contributed nothing to every query ever asked. Half
of hybrid retrieval was inert, and nothing said so — the arm that found nothing and the
arm that could not run look identical from outside.

The knowledge graph itself was never missing: Neo4j holds it (written by
:mod:`app.ingestion.graph_projection`, and read by the Graph screen). What was missing
were the **vectors** that let a query *find* a node in it. Nothing wrote them, because
:meth:`~aegis.retrieval.lightrag_backend.LightRAGBackend.publish_vectors` deliberately
bypasses ``ainsert`` — for reasons that are correct and evidence-backed, see its docstring
— and ``ainsert`` is the only thing in LightRAG that would have written entity vectors.
:mod:`aegis.retrieval.chunk_index` is the equivalent treatment for chunks; this module is
the equivalent treatment for entities and relations.

Speaking LightRAG's dialect, deliberately
-----------------------------------------

Everything here is addressed and shaped the way *LightRAG* addresses and shapes it,
because the reader is LightRAG's own ``local``/``global`` query. Verified against the
installed ``lightrag`` rather than its docs:

* the collections and their payload fields — ``lightrag/lightrag.py:1451-1457``, which
  declares ``entities_vdb`` with ``meta_fields={"entity_name", "source_id", "content",
  "file_path"}`` and ``relationships_vdb`` with ``{"src_id", "tgt_id", "source_id",
  "content", "file_path"}``;
* the point keys — ``compute_mdhash_id(entity_name, prefix="ent-")`` and
  ``compute_mdhash_id(src + tgt, prefix="rel-")`` (``lightrag/operate.py:1675`` and
  ``:3168``), i.e. a prefix plus the MD5 of the seed;
* the text that gets embedded — ``f"{entity_name}\n{description}"`` for an entity and
  ``f"{keywords}\t{src}\n{tgt}\n{description}"`` for a relation (same two sites);
* the payload envelope — ``{id, workspace_id, created_at, **meta_fields}``, built by
  ``lightrag/kg/qdrant_impl.py:729-737``;
* the Qdrant point id — ``sha256(workspace + key)`` read as a UUID, which is
  :func:`aegis.retrieval.chunk_index.lightrag_point_id` and is reused rather than
  restated a second time.

A key or a content string that is *slightly* off is the worst outcome available here: the
points exist, ``points_count`` rises, every write returns cleanly, and the arm still finds
nothing — the same shape of silence this module was written to end. So the two seeds and
the two content strings are pinned by tests against LightRAG's own functions rather than
by comment.

Why the relation key sorts its endpoints
----------------------------------------

LightRAG sorts ``(src, tgt)`` lexicographically before hashing, and stores the sorted pair
in ``src_id``/``tgt_id`` and in the embedded content (``operate.py:3157-3175``: "Sort
src/tgt for a consistent VDB record identity … kept separate from the src/tgt used for the
graph edge write below, which must use the caller's original direction"). One relation is
therefore one vector record whichever way round the extractor stated it, while the graph
edge keeps its real direction. Writing the unsorted pair would give two records for one
edge on a re-extraction that flipped the phrasing — and neither of them would match what
LightRAG's own writer would later produce for the same edge.

What ``source_id`` carries, and what it does not
------------------------------------------------

``source_id`` is a ``<SEP>``-joined list of the **chunk ids** an element was extracted
from. It is the field ``lightrag.operate._find_related_text_unit_from_entities`` walks to
turn a matched entity back into passages — but it reads it off the *graph node*, not off
this payload, so what is written here is provenance for the store rather than an input to
retrieval. It is carried anyway, at the chunk granularity the field means: a document id
in a chunk-id slot would be a fabrication of shape, so a caller that cannot attribute
chunks passes an empty string rather than something of the wrong kind.

The tenant travels the same way it does for chunks
--------------------------------------------------

``file_path`` carries the ``t<id>::`` owner tag, exactly as
:mod:`aegis.retrieval.chunk_index` writes it for chunks, and ``workspace_id`` is
LightRAG's own partition key. One honest caveat, because the two are not equally
load-bearing here: at read time an entity's owner is taken off the **graph node's**
``file_path`` (``operate.py:4994`` reads it from the node record, which
:mod:`app.ingestion.graph_projection` unions across contributing documents), not off this
payload. The tag written here is therefore the store's provenance, and the tenant boundary
on the graph arm is enforced where the union lives. Writing it correctly still matters:
these payloads must be indistinguishable from the ones LightRAG's own extractor writes, or
a future reader that *does* attribute from them inherits an unattributable row.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from aegis.retrieval.chunk_index import (
    _require_client,  # noqa: PLC2701 - the sibling's client check, reused not copied
    effective_workspace,
    lightrag_point_id,
)

__all__ = [
    "DEFAULT_ENTITY_COLLECTION",
    "DEFAULT_RELATION_COLLECTION",
    "GRAPH_FIELD_SEP",
    "EntityPoint",
    "RelationPoint",
    "confirmed_point_keys",
    "entity_content",
    "entity_key",
    "ordered_endpoints",
    "publish_entity_points",
    "publish_relation_points",
    "relation_content",
    "relation_key",
]

#: The collection LightRAG's ``local`` arm queries first. Named as a default rather than
#: hardcoded at the call sites for the same reason as
#: :data:`aegis.retrieval.chunk_index.DEFAULT_CHUNK_COLLECTION`: a deployment running a
#: suffixed collection passes its own, and the string lives in one greppable place.
DEFAULT_ENTITY_COLLECTION = "lightrag_vdb_entities"

#: The collection the ``global``/``hybrid`` arms query for relations. Written by the same
#: pass that writes the entities, because an entity index whose relations are missing is a
#: half-built graph arm and a later reader has no way to tell which half it got.
DEFAULT_RELATION_COLLECTION = "lightrag_vdb_relationships"

#: LightRAG's joiner for a merged element's several sources (``lightrag.constants``).
#: Restated rather than imported for the same reason :mod:`app.ingestion.graph_projection`
#: restates it: it is a storage format that must be produced byte-compatibly, not an API.
GRAPH_FIELD_SEP = "<SEP>"

#: The payload keys LightRAG's entity store writes, in the order it writes them:
#: ``{id, workspace_id, created_at}`` from the envelope, then the declared meta fields.
_ENTITY_PAYLOAD_KEYS = ("id", "workspace_id", "created_at", "entity_name", "source_id",
                        "content", "file_path")

#: The same, for relations.
_RELATION_PAYLOAD_KEYS = ("id", "workspace_id", "created_at", "src_id", "tgt_id",
                          "source_id", "content", "file_path")


def _mdhash(seed: str, *, prefix: str) -> str:
    """Return ``prefix`` plus the MD5 of ``seed``, as ``lightrag.utils`` computes it.

    ``compute_mdhash_id`` delegates to ``compute_args_hash``, whose single-argument path
    is documented to preserve the legacy plain-``str`` behaviour precisely so persisted
    ids stay valid across upgrades — so this is a stable contract to replicate, not an
    implementation detail caught mid-change.

    MD5 is LightRAG's choice and is used here for addressing only, never for a security
    decision; the tenant boundary is enforced elsewhere (see the module docstring).

    Args:
        seed: The exact string LightRAG hashes.
        prefix: ``"ent-"`` or ``"rel-"``.

    Returns:
        The element's LightRAG-side key.

    Raises:
        ValueError: If ``seed`` is empty. Every such element would otherwise collide on
            one key, so a document's whole extraction would index as a single point.
    """
    if not seed:
        raise ValueError(
            "a non-empty seed is required to address a graph vector point; an empty one "
            "would collapse every entity or relation onto a single point id"
        )
    digest = hashlib.md5(seed.encode("utf-8"), usedforsecurity=False).hexdigest()
    return f"{prefix}{digest}"


def entity_key(name: str) -> str:
    """Return the ``entities_vdb`` key LightRAG gives the entity called ``name``.

    Args:
        name: The entity's surface form — the same string stored as ``entity_id`` on the
            graph node, because that is what ``_get_node_data`` looks the node up by.

    Returns:
        ``"ent-" + md5(name)``.
    """
    return _mdhash(name, prefix="ent-")


def ordered_endpoints(src: str, tgt: str) -> tuple[str, str]:
    """Return ``(src, tgt)`` in LightRAG's vector-record order (lexicographic).

    See the module docstring: the vector record is direction-independent while the graph
    edge keeps the direction the extractor stated.

    Args:
        src: The relation's source entity name.
        tgt: The relation's target entity name.

    Returns:
        The pair, smaller string first.
    """
    return (tgt, src) if src > tgt else (src, tgt)


def relation_key(src: str, tgt: str) -> str:
    """Return the ``relationships_vdb`` key for the relation between ``src`` and ``tgt``.

    Args:
        src: The source entity name, in either order.
        tgt: The target entity name.

    Returns:
        ``"rel-" + md5(smaller + larger)``.
    """
    first, second = ordered_endpoints(src, tgt)
    return _mdhash(first + second, prefix="rel-")


def entity_content(name: str, description: str) -> str:
    r"""Return the text LightRAG embeds for an entity.

    Args:
        name: The entity's surface form.
        description: Its description, as stored on the graph node.

    Returns:
        ``"{name}\n{description}"``.
    """
    return f"{name}\n{description}"


def relation_content(keywords: str, src: str, tgt: str, description: str) -> str:
    r"""Return the text LightRAG embeds for a relation.

    ``src``/``tgt`` are ordered as :func:`ordered_endpoints` orders them, so the embedded
    text and the key describe the same record.

    Args:
        keywords: The relation's keyword phrase.
        src: The source entity name, in either order.
        tgt: The target entity name.
        description: The relation's description.

    Returns:
        ``"{keywords}\t{src}\n{tgt}\n{description}"``.
    """
    first, second = ordered_endpoints(src, tgt)
    return f"{keywords}\t{first}\n{second}\n{description}"


@dataclass(frozen=True, slots=True)
class EntityPoint:
    """One entity, the vector of its LightRAG-shaped text, and its provenance.

    Attributes:
        name: The entity's surface form. Must equal the graph node's ``entity_id``: the
            ``local`` arm reads names out of this index and looks them up in the graph,
            and a name that matches nothing there is dropped by ``_get_node_data`` — which
            is a vector that exists, matches, and still contributes nothing.
        description: The description embedded alongside the name.
        source_id: ``<SEP>``-joined chunk ids, or ``""`` when the caller cannot attribute
            chunks (see the module docstring — never a document id in this slot).
        file_path: The tenant-tagged source path (``"t1::policy.pdf"``).
        vector: The embedding of :attr:`content`. Empty means "not embedded", which
            :func:`publish_entity_points` refuses rather than publishes.
    """

    name: str
    description: str
    file_path: str
    source_id: str = ""
    vector: Sequence[float] = field(default_factory=tuple)

    @property
    def key(self) -> str:
        """The ``entities_vdb`` key LightRAG addresses this entity by."""
        return entity_key(self.name)

    @property
    def content(self) -> str:
        """The text LightRAG embeds for this entity."""
        return entity_content(self.name, self.description)

    def payload(self, *, workspace: str, created_at: int) -> dict[str, Any]:
        """Return the payload LightRAG's entity reader expects.

        Args:
            workspace: The effective workspace to tag the point with.
            created_at: Unix seconds to stamp.

        Returns:
            A payload holding exactly :data:`_ENTITY_PAYLOAD_KEYS`.
        """
        return {
            "id": self.key,
            "workspace_id": workspace,
            "created_at": created_at,
            "entity_name": self.name,
            "source_id": self.source_id,
            "content": self.content,
            "file_path": self.file_path,
        }


@dataclass(frozen=True, slots=True)
class RelationPoint:
    """One relation, the vector of its LightRAG-shaped text, and its provenance.

    Attributes:
        src: The source entity name as extracted. Reordered for the record identity, not
            for the graph edge (see the module docstring).
        tgt: The target entity name as extracted.
        keywords: The relation's keyword phrase — the first field of the embedded text.
        description: The relation's description.
        source_id: ``<SEP>``-joined chunk ids, or ``""``.
        file_path: The tenant-tagged source path.
        vector: The embedding of :attr:`content`.
    """

    src: str
    tgt: str
    keywords: str
    description: str
    file_path: str
    source_id: str = ""
    vector: Sequence[float] = field(default_factory=tuple)

    @property
    def endpoints(self) -> tuple[str, str]:
        """The endpoints in LightRAG's vector-record order."""
        return ordered_endpoints(self.src, self.tgt)

    @property
    def key(self) -> str:
        """The ``relationships_vdb`` key LightRAG addresses this relation by."""
        return relation_key(self.src, self.tgt)

    @property
    def content(self) -> str:
        """The text LightRAG embeds for this relation."""
        return relation_content(self.keywords, self.src, self.tgt, self.description)

    def payload(self, *, workspace: str, created_at: int) -> dict[str, Any]:
        """Return the payload LightRAG's relation reader expects.

        Args:
            workspace: The effective workspace to tag the point with.
            created_at: Unix seconds to stamp.

        Returns:
            A payload holding exactly :data:`_RELATION_PAYLOAD_KEYS`.
        """
        first, second = self.endpoints
        return {
            "id": self.key,
            "workspace_id": workspace,
            "created_at": created_at,
            "src_id": first,
            "tgt_id": second,
            "source_id": self.source_id,
            "content": self.content,
            "file_path": self.file_path,
        }


def _publish(
    client: object,
    points: Sequence[EntityPoint] | Sequence[RelationPoint],
    *,
    collection: str,
    workspace: str | None,
    batch_size: int,
    kind: str,
) -> int:
    """Upsert ``points`` into ``collection`` and return how many were written.

    Args:
        client: A Qdrant client.
        points: Entity or relation points, each carrying its vector.
        collection: The collection to write into.
        workspace: The workspace to tag with; ``None`` resolves the platform's.
        batch_size: Points per upsert request.
        kind: ``"entity"``/``"relation"``, for the error messages only.

    Returns:
        The number of points written.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
        LookupError: If ``collection`` does not exist.
        ValueError: If any point carries no vector.
    """
    from qdrant_client import models  # noqa: PLC0415 - lazy: heavy optional dependency

    qdrant = _require_client(client)
    if not points:
        return 0
    if not qdrant.collection_exists(collection):
        raise LookupError(
            f"vector collection {collection!r} does not exist; it is created by the "
            "process that owns its dimensionality, and creating it here at a guessed "
            "width would silently build an index no query can use"
        )

    ws = effective_workspace(workspace)
    stamp = int(time.time())
    structs = []
    seen: set[str] = set()
    for point in points:
        if not point.vector:
            raise ValueError(
                f"{kind} {point.key!r} carries no embedding; publishing it would put an "
                "unsearchable row into the graph index under a real element's id"
            )
        key = point.key
        # Two extracted elements can normalise to one LightRAG key (two entity kinds
        # sharing a surface form, or one relation stated twice). Upserting both would be
        # correct and would pay the store twice for one row; the first wins, which is the
        # same "first mention wins the surface form" rule the graph projection applies.
        if key in seen:
            continue
        seen.add(key)
        structs.append(
            models.PointStruct(
                id=lightrag_point_id(key, workspace=ws),
                vector=[float(value) for value in point.vector],
                payload=point.payload(workspace=ws, created_at=stamp),
            )
        )

    for start in range(0, len(structs), batch_size):
        qdrant.upsert(
            collection_name=collection,
            points=structs[start : start + batch_size],
            wait=True,
        )
    return len(structs)


def publish_entity_points(
    client: object,
    points: Sequence[EntityPoint],
    *,
    collection: str = DEFAULT_ENTITY_COLLECTION,
    workspace: str | None = None,
    batch_size: int = 128,
) -> int:
    """Write entity vectors into the collection LightRAG's ``local`` arm queries.

    Idempotent, for free and by construction: the point id is
    ``sha256(workspace + "ent-" + md5(name))``, so re-ingesting a document that mentions
    the same entities overwrites the same rows. There is no delete step and therefore no
    window in which the graph arm is empty — which matters more here than for chunks,
    because an empty ``entities_vdb`` is the exact failure this module exists to end and
    it is indistinguishable from a thin neighbourhood at read time.

    Args:
        client: A ``qdrant_client.QdrantClient`` (or anything with its ``upsert``).
        points: The entities to publish, each carrying its vector.
        collection: The collection to write into.
        workspace: The workspace to tag with; ``None`` resolves the platform's.
        batch_size: Points per upsert request — LightRAG's own default, and these
            payloads are the same shape as the chunk payloads that bound was chosen for.

    Returns:
        The number of points written, after collapsing duplicate keys.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
        LookupError: If ``collection`` does not exist. Created lazily by whoever owns the
            collection's dimensionality — creating it here at a guessed width is how a
            3072-dim corpus ends up in a 1536-dim collection.
        ValueError: If any point carries no vector.
    """
    return _publish(
        client,
        points,
        collection=collection,
        workspace=workspace,
        batch_size=batch_size,
        kind="entity",
    )


def publish_relation_points(
    client: object,
    points: Sequence[RelationPoint],
    *,
    collection: str = DEFAULT_RELATION_COLLECTION,
    workspace: str | None = None,
    batch_size: int = 128,
) -> int:
    """Write relation vectors into the collection the ``global``/``hybrid`` arms query.

    Idempotent on the same construction as :func:`publish_entity_points`, and
    direction-independent besides: the key hashes the sorted endpoint pair, so a
    re-extraction that phrased the relation the other way round overwrites the record
    rather than adding a second one.

    Args:
        client: A Qdrant client.
        points: The relations to publish, each carrying its vector.
        collection: The collection to write into.
        workspace: The workspace to tag with; ``None`` resolves the platform's.
        batch_size: Points per upsert request.

    Returns:
        The number of points written, after collapsing duplicate keys.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
        LookupError: If ``collection`` does not exist.
        ValueError: If any point carries no vector.
    """
    return _publish(
        client,
        points,
        collection=collection,
        workspace=workspace,
        batch_size=batch_size,
        kind="relation",
    )


def confirmed_point_keys(
    client: object,
    keys: Iterable[str],
    *,
    collection: str,
    workspace: str | None = None,
) -> frozenset[str]:
    """Return which of ``keys`` the store confirms holding.

    The counterweight to the publish, and the same rule
    :meth:`~aegis.retrieval.lightrag_backend.LightRAGBackend.audit_chunks` states: a
    writer's own count is a claim; a count read back out of the store is evidence.

    This retrieves the exact point ids rather than scrolling the collection the way
    :func:`aegis.retrieval.chunk_index.stored_point_ids` does, because the question is
    different. That one asks "what does this scope hold that I do not know about", which
    needs a scan and yields ``orphaned``; this one asks "did these specific rows land",
    which a keyed read answers exactly — and answers without walking a shared,
    corpus-sized entity collection once per ingested document.

    Args:
        client: A Qdrant client.
        keys: The LightRAG-side keys that were published.
        collection: The collection to read.
        workspace: The workspace the points were written under; ``None`` resolves the
            platform's. It is part of the hashed point id, so asking under the wrong one
            returns nothing — which reads exactly like a failed write.

    Returns:
        The subset of ``keys`` the store returned a point for.

    Raises:
        TypeError: If ``client`` is not a Qdrant client.
    """
    qdrant = _require_client(client)
    if not callable(getattr(qdrant, "retrieve", None)):
        raise TypeError(
            f"{client!r} exposes no callable 'retrieve', so the read-back that turns a "
            "publish into evidence cannot run against it"
        )
    wanted = [key for key in keys if key]
    if not wanted:
        return frozenset()
    if not qdrant.collection_exists(collection):
        return frozenset()

    ws = effective_workspace(workspace)
    by_id = {lightrag_point_id(key, workspace=ws): key for key in wanted}
    found: set[str] = set()
    ids = list(by_id)
    for start in range(0, len(ids), 256):
        records = qdrant.retrieve(
            collection_name=collection,
            ids=ids[start : start + 256],
            with_payload=False,
            with_vectors=False,
        )
        for record in records:
            key = by_id.get(str(record.id).replace("-", ""))
            if key is not None:
                found.add(key)
    return frozenset(found)
