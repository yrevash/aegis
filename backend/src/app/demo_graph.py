"""The demo **knowledge graph** — the half of the demo corpus that lives in Neo4j.

:mod:`app.demo` writes ninety days of *platform telemetry* into PostgreSQL. This module
writes the *domain knowledge* the console's Graph screen renders, into the same Neo4j
store LightRAG's entity/relationship extractor writes, and it exists because that store
was measured holding **two nodes and one relationship** — a pair of entities left over
from an unrelated ingested paper. The screen was not broken; there was nothing in it.

Why the graph is written at all, rather than extracted
-----------------------------------------------------

Entities and relationships are normally produced by LightRAG during ingestion, and
ingestion is a Temporal workflow. Two things make that the wrong path for a *demo*
corpus: the workflow needs a worker that a laptop demo may not have running, and the
extractor is an LLM loop that spends real money per document. So the graph is written
the way :mod:`app.demo` writes the ledger — computed directly, from the domain's own
records, with the gateway never called.

Where the content comes from — and why this module names none of it
-------------------------------------------------------------------

Every entity here is derived at run time from the **adapter** (:mod:`app.adapter`), the
same seam the rest of the platform reads the domain through:

* :func:`~app.adapter.generate_synthetic_sync` — the record world, offline and seeded, so
  the graph is deterministic and no LLM is involved;
* :func:`~app.adapter.load_seed_corpus` — the hand-written documents the domain ships.

Nothing below spells a record type, a field name or a category. The derivation is a set
of **structural** rules applied to whatever pydantic models the adapter declares:

============================== ================================================
structure found on a record    what it becomes
============================== ================================================
the record itself              an entity, kinded by its model's class name
a ``*_id`` field resolving to
another record                 a relation, labelled with the field's own name
an ``Enum``-valued field       a taxonomy entity, kinded by the enum's class
a low-cardinality scalar field a dimension entity (an SLA target, a team, …)
a list of strings              one topic entity per element
============================== ================================================

That is what keeps the module retarget-clean: on swap day the new domain's records
produce the new domain's graph, with nothing here edited — and
``aegis.conformance``'s vocabulary check, which forbids any core module from containing
the shipped domain's words, stays satisfied.

Provenance, and what a tenant is shown
--------------------------------------

Neo4j has no row-level security. The boundary is applied by the backend, from each
element's ``file_path`` — LightRAG's own provenance property, which
:func:`aegis.retrieval.lightrag_backend._owners_of` reads and
:func:`aegis.retrieval.types.scoped_graph` filters on. This module therefore writes real
``file_path`` values in exactly LightRAG's format (``t<id>::<source>``, several joined by
``<SEP>`` when an entity was seen in more than one place), and assigns them the way the
domain implies:

* a record entity is owned by the tenant of the record that *defines* it, and by every
  tenant whose records *reference* it — so a specialist working both tenants' cases is
  one node, visible to both, exactly as a merged extraction would be;
* a **relation** carries only the referencing record's own source, so it is owned by one
  tenant and can never carry another tenant's sentence across the boundary
  (:func:`~aegis.retrieval.types.scoped_graph` requires *every* owner of an edge to be
  visible, and only *any* owner of a node);
* the hand-written seed documents are written to the **shared** corpus (``shared::``),
  because deployment-level knowledge belongs to no tenant and every scope may read it.

The tag, and why ``--wipe`` is safe
-----------------------------------

Two marks, both on the element itself:

* a property ``demo_tag`` = :data:`DEMO_GRAPH_TAG`, on every node and relationship, and
* an extra Neo4j label :data:`DEMO_NODE_LABEL` beside LightRAG's workspace label, so the
  corpus is one ``MATCH`` away in Neo4j Browser.

The wipe deletes a node only when it carries the tag **and** every source in its
``file_path`` is a demo source. The second half is not belt-and-braces: LightRAG merges
an entity by name, so if a later real ingestion extracted an entity this corpus already
created, that node would carry real provenance too — and deleting it would destroy
extracted knowledge. Such a node is kept, and named in the summary. The wipe can
therefore only ever remove nodes whose every contributor was this module.

A seed is refused for the same reason in the other direction: an entity name that
already exists in the graph *without* the tag is left alone and never written into.

What is **not** written, and why
--------------------------------

``lightrag_vdb_entities`` and ``lightrag_vdb_relationships`` in Qdrant stay empty. A
point in those collections is a 3072-dimension embedding, and the only honest way to
produce one is to call the embedding model — real spend, and the rule this corpus holds
itself to is that the gateway is never called. Writing random or zero vectors instead
would put meaningless neighbours into every graph-mode retrieval, silently, forever. An
empty collection is visibly empty; a poisoned one is not.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)

__all__ = [
    "DEMO_GRAPH_TAG",
    "DEMO_NODE_LABEL",
    "DemoGraph",
    "GraphSummary",
    "GraphUnavailableError",
    "GraphWipeSummary",
    "build_graph",
    "prune_graph_source",
    "seed_graph",
    "wipe_graph",
]


#: Value written to the ``demo_tag`` property of every node and relationship. It opens
#: with :data:`app.demo.DEMO_PREFIX` — restated rather than imported, because
#: :mod:`app.demo` imports *this* module and the constant is a stored format, not a
#: shared runtime value. ``tests/test_demo_graph.py`` asserts the two still agree.
DEMO_GRAPH_TAG = "demo-graph"

#: The extra Neo4j label every demo node carries, beside LightRAG's workspace label.
#: A label rather than only a property because it makes the corpus selectable in one
#: ``MATCH (n:AegisDemo)`` from a console, without knowing this module exists.
DEMO_NODE_LABEL = "AegisDemo"

#: LightRAG's own joiner for a merged element's several sources. Restated (not imported)
#: because it is a *storage format* this module has to produce byte-compatibly, not an
#: API it calls.
_GRAPH_FIELD_SEP = "<SEP>"

#: Separator between the tenant tag and the source name inside one ``file_path``.
_TENANT_TAG_SEP = "::"

#: The tenant tag marking the shared, tenant-less corpus.
_SHARED_TAG = "shared"

#: Prefix every demo *source name* carries inside its ``file_path``. This is what makes
#: "is every contributor to this node a demo source?" answerable in Cypher.
_SOURCE_PREFIX = "demo-"

#: The RNG seed handed to the adapter's generator. Fixed, so the graph a screenshot shows
#: today is the graph the database rebuilds tomorrow.
GRAPH_RNG_SEED = 20260830

#: How much of the generator's default world the graph is derived from. Applied to
#: whatever positive integer knobs :class:`~app.adapter.GeneratorConfig` declares, rather
#: than by naming them, so a retargeted adapter with different collections is scaled
#: without this module knowing what they count. Small on purpose: a knowledge graph is
#: read by eye, and a force layout turns a few hundred nodes into a hairball that shows
#: less than seventy do.
_GENERATOR_SCALE = 0.5

#: No collection contributes more than this many records however large the generator's
#: default is, so a domain that ships a thousand of something cannot swamp the screen.
_MAX_RECORDS_PER_COLLECTION = 20

#: A scalar field becomes a *dimension* entity when its whole collection holds between
#: two and this many distinct values. One value is not a dimension (it separates
#: nothing); too many and the field is an identifier or a measurement, not a facet.
_MIN_DIMENSION_CARDINALITY = 2
_MAX_DIMENSION_CARDINALITY = 8

#: Field names that are an entity's own identity or prose, never a facet of it. Matched
#: by *role*, not by domain meaning: these are the four names pydantic record models use
#: for "the key", "the display name" and "the body text" across any domain.
_IDENTITY_FIELDS = frozenset({"id", "name", "title"})
_PROSE_FIELDS = frozenset({"description", "body", "summary", "text", "content"})

#: Upper bound on a written description, so one long document body cannot make a node's
#: property larger than the rest of the graph put together.
_DESCRIPTION_CHARS = 240


class GraphUnavailableError(RuntimeError):
    """The Neo4j graph store could not be reached, so nothing was read or written."""


# ─────────────────────────────────────────────────────────────────────────────
# The graph — pure, deterministic, and computed with no store in sight
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class DemoEntity:
    """One node, in the shape LightRAG's Neo4j storage stores a node.

    Attributes:
        entity_id: The name, which is also LightRAG's merge key and the label the
            console renders.
        kind: ``entity_type`` — what the Graph screen colours and counts by.
        description: The prose the graph carries for this entity.
        sources: Tagged ``file_path`` parts, one per contributor.
    """

    entity_id: str
    kind: str
    description: str
    sources: tuple[str, ...]

    def properties(self, *, created_at: int) -> dict[str, Any]:
        """Return the Neo4j property map for this node."""
        return {
            "entity_id": self.entity_id,
            "entity_type": self.kind,
            "description": self.description,
            "file_path": _GRAPH_FIELD_SEP.join(self.sources),
            "created_at": created_at,
            "demo_tag": DEMO_GRAPH_TAG,
        }


@dataclass(frozen=True, slots=True)
class DemoRelation:
    """One directed, labelled relationship between two :class:`DemoEntity` names.

    Attributes:
        source: ``entity_id`` of the tail.
        target: ``entity_id`` of the head.
        keywords: The relation label the console renders on the edge.
        description: The prose the graph carries for this relation.
        sources: Tagged ``file_path`` parts — for an edge, always exactly one.
        weight: LightRAG's edge weight.
    """

    source: str
    target: str
    keywords: str
    description: str
    sources: tuple[str, ...]
    weight: float = 1.0

    def properties(self, *, created_at: int) -> dict[str, Any]:
        """Return the Neo4j property map for this relationship."""
        return {
            "keywords": self.keywords,
            "description": self.description,
            "file_path": _GRAPH_FIELD_SEP.join(self.sources),
            "weight": self.weight,
            "created_at": created_at,
            "demo_tag": DEMO_GRAPH_TAG,
        }


@dataclass(slots=True)
class DemoGraph:
    """The whole demo graph, before anything has touched a store."""

    entities: list[DemoEntity] = field(default_factory=list)
    relations: list[DemoRelation] = field(default_factory=list)

    def kinds(self) -> dict[str, int]:
        """Return the entity count per ``kind``, which is what the screen charts."""
        counts: dict[str, int] = {}
        for entity in self.entities:
            counts[entity.kind] = counts.get(entity.kind, 0) + 1
        return dict(sorted(counts.items()))


@dataclass(slots=True)
class _Builder:
    """Accumulates entities and their provenance while the records are walked.

    An entity may be reached many times — a taxonomy value by every record that carries
    it, a specialist by every case assigned to them — and each arrival contributes one
    more source. Sources are therefore merged here rather than at write time, exactly as
    LightRAG merges them across documents.
    """

    _entities: dict[str, dict[str, Any]] = field(default_factory=dict)
    _relations: dict[tuple[str, str, str], DemoRelation] = field(default_factory=dict)

    def entity(
        self, *, entity_id: str, kind: str, description: str, source: str
    ) -> str:
        """Record (or merge into) one entity and return its ``entity_id``."""
        held = self._entities.get(entity_id)
        if held is None:
            self._entities[entity_id] = {
                "kind": kind,
                "description": description,
                "sources": [source],
            }
            return entity_id
        if source not in held["sources"]:
            held["sources"].append(source)
        # A record definition (which carries prose) wins over the bare mention that a
        # reference creates, whichever order they arrive in.
        if len(description) > len(held["description"]):
            held["description"] = description
        return entity_id

    def relation(
        self, *, source_id: str, target_id: str, keywords: str, description: str, origin: str
    ) -> None:
        """Record one relationship, keyed so a repeat cannot duplicate it."""
        key = (source_id, target_id, keywords)
        held = self._relations.get(key)
        if held is None:
            self._relations[key] = DemoRelation(
                source=source_id,
                target=target_id,
                keywords=keywords,
                description=description,
                sources=(origin,),
            )
            return
        # A repeated edge is a stronger edge, and its provenance stays single-owner:
        # merging a second tenant's source into it would make the edge invisible to
        # both under ``scoped_graph``'s all-owners rule.
        self._relations[key] = DemoRelation(
            source=held.source,
            target=held.target,
            keywords=held.keywords,
            description=held.description,
            sources=held.sources,
            weight=held.weight + 1.0,
        )

    def build(self) -> DemoGraph:
        """Return the accumulated graph, with every dangling relation dropped."""
        entities = [
            DemoEntity(
                entity_id=entity_id,
                kind=held["kind"],
                description=held["description"],
                sources=tuple(held["sources"]),
            )
            for entity_id, held in self._entities.items()
        ]
        names = {entity.entity_id for entity in entities}
        relations = [
            relation
            for relation in self._relations.values()
            if relation.source in names and relation.target in names
        ]
        return DemoGraph(entities=entities, relations=relations)


def _snake(name: str) -> str:
    """Return ``CamelCase`` as ``snake_case`` — how a class name becomes a kind."""
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _phrase(name: str) -> str:
    """Return a field name as the human phrase an edge or a facet is labelled with."""
    return name.replace("_", " ").strip()


def _tagged(tenant_tag: str | None, source: str) -> str:
    """Return the ``file_path`` for ``source`` owned by ``tenant_tag``."""
    tag = tenant_tag if tenant_tag else _SHARED_TAG
    return f"{tag}{_TENANT_TAG_SEP}{_SOURCE_PREFIX}{source}"


def _record_collections(dataset: BaseModel) -> dict[str, list[BaseModel]]:
    """Return every list-of-records field on ``dataset``, keyed by field name.

    Structural, so a retargeted adapter whose dataset holds different collections is
    walked without this module being told what they are called.
    """
    collections: dict[str, list[BaseModel]] = {}
    for name in type(dataset).model_fields:
        value = getattr(dataset, name, None)
        if isinstance(value, list) and value and all(isinstance(v, BaseModel) for v in value):
            collections[name] = list(value)
    return collections


def _field_value(record: BaseModel, names: Iterable[str]) -> str | None:
    """Return the first non-empty string field of ``record`` named in ``names``."""
    for name in names:
        value = getattr(record, name, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _identity(record: BaseModel) -> str | None:
    """Return the record's own key — the field a foreign key would point at."""
    return _field_value(record, ("id",))


def _label(record: BaseModel) -> str | None:
    """Return the record's display name, falling back to its key."""
    return _field_value(record, ("name", "title", "id"))


def _prose(record: BaseModel) -> str | None:
    """Return the record's own text, truncated, if it carries any."""
    text = _field_value(record, sorted(_PROSE_FIELDS))
    if text is None:
        return None
    flat = " ".join(text.split())
    return flat if len(flat) <= _DESCRIPTION_CHARS else f"{flat[:_DESCRIPTION_CHARS].rstrip()}…"


def _is_scalar(value: Any) -> bool:  # noqa: ANN401 - any pydantic field value
    """Return whether ``value`` is a facet-shaped scalar (not a date, not a container)."""
    if isinstance(value, Enum):
        return True
    if isinstance(value, bool | datetime | date):
        return False
    return isinstance(value, str | int | float)


def _dimension_fields(records: Sequence[BaseModel], *, keys: set[str]) -> set[str]:
    """Return the field names of ``records`` that behave like a facet.

    A facet is a scalar column whose whole collection holds a handful of distinct
    values. That rule is what turns a contractual target, a team name or a satisfaction
    score into a node people can pivot the graph on, without this module knowing that
    any of those three exist. Enum fields are facets unconditionally — an enum *is* a
    declared taxonomy, however few of its members a small sample happens to use.
    """
    if not records:
        return set()
    found: set[str] = set()
    for name in type(records[0]).model_fields:
        if name in _IDENTITY_FIELDS or name in _PROSE_FIELDS or name in keys:
            continue
        values = [getattr(record, name, None) for record in records]
        present = [v for v in values if v is not None and _is_scalar(v)]
        if len(present) != len(values):
            continue
        if any(isinstance(v, Enum) for v in present):
            found.add(name)
            continue
        distinct = {str(v) for v in present}
        if len(distinct) == len(present):
            continue  # a column with no repeats is a key or a measurement, not a facet
        if _MIN_DIMENSION_CARDINALITY <= len(distinct) <= _MAX_DIMENSION_CARDINALITY:
            found.add(name)
    return found


def _scaled_counts(config_model: type[BaseModel]) -> dict[str, int]:
    """Return the generator's own count knobs, scaled down to a readable world.

    Every positive integer default on the config is a "how many of these to make" knob —
    that is the only shape such a field has — so scaling them all is how this module asks
    for a smaller world without naming a single collection. A field whose default is
    already at or below the cap is still scaled, and never falls below one: a collection
    with no records contributes no entities and would quietly hollow the graph out.
    """
    counts: dict[str, int] = {}
    for name, declared in config_model.model_fields.items():
        default = declared.default
        if isinstance(default, bool) or not isinstance(default, int) or default <= 0:
            continue
        counts[name] = max(1, min(_MAX_RECORDS_PER_COLLECTION, round(default * _GENERATOR_SCALE)))
    return counts


def _foreign_keys(records: Sequence[BaseModel]) -> set[str]:
    """Return the field names that look like a reference to another record."""
    if not records:
        return set()
    return {name for name in type(records[0]).model_fields if name.endswith("_id")}


def build_graph(
    tenant_tags: Sequence[str],
    *,
    seed: int = GRAPH_RNG_SEED,
) -> DemoGraph:
    """Derive the demo knowledge graph from the adapter's own records.

    Args:
        tenant_tags: The tenant metadata values (``"t1"``, ``"t2"``, …) the record
            collections are distributed across, in a stable order. Ownership is
            *assigned*, not invented: a record's tenant is the tenant of the record it
            references, so a case and its customer are never split apart, and the
            relations between them stay single-owner and therefore visible.
        seed: RNG seed for the adapter's offline generator.

    Returns:
        The graph, ready to write. Nothing here has touched a store.

    Raises:
        ValueError: If ``tenant_tags`` is empty — a graph owned by nobody would be
            invisible to every tenant-scoped caller, which is a silent failure.
    """
    from app.adapter import (  # noqa: PLC0415 - the domain seam, read at call time
        GeneratorConfig,
        generate_synthetic_sync,
        load_seed_corpus,
    )

    if not tenant_tags:
        raise ValueError("the demo graph needs at least one tenant to be owned by")

    dataset = generate_synthetic_sync(
        GeneratorConfig(seed=seed, use_llm=False, **_scaled_counts(GeneratorConfig))
    )
    collections = _record_collections(dataset)

    # The hand-written documents ship with the domain and belong to the deployment, not
    # to a tenant. They join whichever collection already holds records of their type,
    # so a retargeted adapter that ships no documents simply contributes none.
    shared: list[BaseModel] = list(load_seed_corpus())
    shared_collection: str | None = None
    if shared:
        for name, records in collections.items():
            if isinstance(records[0], type(shared[0])):
                shared_collection = name
                break
        if shared_collection is None:
            shared_collection = _snake(type(shared[0]).__name__) + "s"
            collections[shared_collection] = []

    builder = _Builder()

    # Pass 1: every record's own entity, and the tenant that owns it. A record with a
    # resolvable foreign key inherits its referent's tenant; the rest are dealt round
    # robin, deterministically by position.
    owner_of: dict[str, str | None] = {}  # record key → tenant tag (None = shared)
    entity_of: dict[str, str] = {}  # record key → entity_id
    source_of: dict[str, str] = {}  # record key → tagged file_path
    named: dict[str, str] = {}  # display name → the record key that claimed it first

    keys_by_collection = {
        name: _foreign_keys(records) for name, records in collections.items()
    }
    all_records: list[tuple[str, int, BaseModel, bool]] = []
    for name, records in collections.items():
        for index, record in enumerate(records):
            all_records.append((name, index, record, False))
    if shared_collection is not None:
        for index, record in enumerate(shared):
            all_records.append((shared_collection, index, record, True))

    # Referents first, so a referring record can inherit an owner that already exists.
    for depth in (0, 1):
        for name, _index, record, is_shared in all_records:
            key = _identity(record)
            if key is None or key in owner_of:
                continue
            referenced = [
                value
                for field_name in sorted(keys_by_collection.get(name, set()))
                if isinstance(value := getattr(record, field_name, None), str)
            ]
            if depth == 0 and referenced:
                continue  # settle the records this one points at first
            if is_shared:
                owner = None
            else:
                inherited = [owner_of[ref] for ref in referenced if ref in owner_of]
                owner = (
                    inherited[0]
                    if inherited
                    else tenant_tags[len(owner_of) % len(tenant_tags)]
                )
            owner_of[key] = owner
            kind = _snake(type(record).__name__)
            source_of[key] = _tagged(owner, f"{name}.jsonl" if not is_shared else f"{key}.md")
            # Two distinct records may share a display name. Merging them — which is what
            # keying on the name alone would do — would silently drop one of them and
            # give the survivor both tenants' provenance, so the second one is qualified
            # by its own key. Its *first* holder keeps the clean name, which is why the
            # qualifier is added on collision rather than always.
            label = _label(record) or key
            if named.setdefault(label, key) != key:
                label = f"{label} ({key})"
            entity_of[key] = builder.entity(
                entity_id=label,
                kind=kind,
                description=_prose(record)
                or f"A {_phrase(kind)} in the Aegis demo corpus.",
                source=source_of[key],
            )

    # Pass 2: the edges — references between records, and the facets each record shows.
    for name, _index, record, is_shared in all_records:
        key = _identity(record)
        if key is None:
            continue
        origin = source_of[key]
        subject = entity_of[key]
        keys = keys_by_collection.get(name, set()) if not is_shared else _foreign_keys([record])

        for field_name in sorted(keys):
            value = getattr(record, field_name, None)
            if not isinstance(value, str) or value not in entity_of:
                continue
            relation = _phrase(field_name.removesuffix("_id"))
            builder.relation(
                source_id=subject,
                target_id=entity_of[value],
                keywords=relation,
                description=(
                    f"{subject} is linked to {entity_of[value]} as its {relation}."
                ),
                origin=origin,
            )

        collection_records = collections.get(name, [])
        sample = collection_records if not is_shared else shared
        for field_name in sorted(_dimension_fields(sample, keys=keys)):
            value = getattr(record, field_name, None)
            if value is None:
                continue
            facet_kind, facet_label = _facet(field_name, value)
            facet = builder.entity(
                entity_id=facet_label,
                kind=facet_kind,
                description=(
                    f"The {_phrase(facet_kind)} '{facet_label}' in the Aegis demo corpus."
                ),
                source=origin,
            )
            builder.relation(
                source_id=subject,
                target_id=facet,
                keywords=_phrase(field_name),
                description=f"{subject} has {_phrase(field_name)} {facet_label}.",
                origin=origin,
            )

        for field_name in sorted(type(record).model_fields):
            values = getattr(record, field_name, None)
            if not isinstance(values, list) or not values:
                continue
            for item in values:
                if not _is_scalar(item):
                    continue
                item_kind, item_label = _facet(field_name, item, member=True)
                member = builder.entity(
                    entity_id=item_label,
                    kind=item_kind,
                    description=(
                        f"The {_phrase(item_kind)} '{item_label}' in the Aegis demo corpus."
                    ),
                    source=origin,
                )
                builder.relation(
                    source_id=subject,
                    target_id=member,
                    keywords=_phrase(field_name),
                    description=f"{subject} is tagged {item_label}.",
                    origin=origin,
                )

    return builder.build()


def _facet(
    field_name: str, value: Any, *, member: bool = False  # noqa: ANN401 - field value
) -> tuple[str, str]:
    """Return the ``(kind, label)`` a facet value becomes.

    An enum carries its own taxonomy name, so the kind is the enum's class and the label
    is the member — the graph then shows one node per declared member, shared by every
    record that has it. A bare scalar has no declared taxonomy, so the field it came from
    supplies the kind, and a numeric label has to name the field too: ``48.0`` alone
    would be an unreadable node, and two numeric facets could collide on it.

    Args:
        field_name: The field the value was read from.
        value: The value itself.
        member: Whether the value came from *inside* a list field, in which case the
            field names the collection and the kind is its singular.
    """
    kind = _singular(field_name) if member else field_name
    if isinstance(value, Enum):
        return _snake(type(value).__name__), str(value.value)
    if isinstance(value, str):
        return kind, value.strip()
    return kind, f"{_phrase(field_name)} {value}"


def _singular(name: str) -> str:
    """Return a collection-shaped field name as the kind of one of its members."""
    stem = name.rstrip()
    return stem[:-1] if stem.endswith("s") and not stem.endswith("ss") else stem


# ─────────────────────────────────────────────────────────────────────────────
# Persistence
# ─────────────────────────────────────────────────────────────────────────────


@dataclass(slots=True)
class GraphSummary:
    """What one graph seed did.

    Attributes:
        nodes: Nodes created.
        relationships: Relationships created.
        existing: Demo nodes already present, so the run was a no-op.
        refused: Entity names skipped because a **real** node already holds them.
        kinds: Entity count per kind, for the report.
    """

    nodes: int = 0
    relationships: int = 0
    existing: int = 0
    refused: tuple[str, ...] = ()
    kinds: dict[str, int] = field(default_factory=dict)

    def lines(self) -> list[str]:
        """Return the human-readable report lines."""
        out = [
            f"  neo4j nodes    {self.nodes:>6} written, {self.existing:>6} already present",
            f"  neo4j edges    {self.relationships:>6} written",
        ]
        if self.kinds:
            shown = ", ".join(f"{k}={v}" for k, v in self.kinds.items())
            out.append(f"  entity kinds   {shown}")
        if self.refused:
            out.append(
                f"  skipped        {len(self.refused)} name(s) already held by real "
                f"extracted entities: {', '.join(self.refused[:5])}"
            )
        return out


@dataclass(slots=True)
class GraphWipeSummary:
    """What one graph wipe removed, and what it deliberately would not.

    Attributes:
        nodes: Demo nodes deleted (with every relationship attached to them).
        kept: Tagged nodes left alone because a real ingestion had merged into them.
    """

    nodes: int = 0
    kept: tuple[str, ...] = ()

    def lines(self) -> list[str]:
        """Return the human-readable report lines."""
        out = [f"  neo4j nodes    {self.nodes:>6} deleted"]
        if self.kept:
            out.append(
                f"  kept           {len(self.kept)} tagged node(s) that real ingestion "
                f"has since contributed to: {', '.join(self.kept[:5])}"
            )
        return out


def _workspace_label() -> str:
    """Return the Neo4j label LightRAG stores this deployment's graph under.

    Computed exactly as ``lightrag.kg.neo4j_impl`` computes it, because writing under a
    different label would produce a graph the reader's ``MATCH`` never sees.
    """
    return os.environ.get("NEO4J_WORKSPACE", "").strip() or "base"


def _quoted(label: str) -> str:
    """Return ``label`` escaped for use inside Cypher backticks."""
    return label.replace("`", "``")


def _driver() -> Any:  # noqa: ANN401 - the neo4j AsyncDriver, imported lazily
    """Return an async Neo4j driver built from the platform's own settings.

    **A test process is refused one**, and that is not timidity. Every other store this
    corpus touches is isolated per run — the suite provisions a scratch PostgreSQL and an
    in-process vector engine — but there is no scratch Neo4j, so a test that seeded or
    wiped here would be reaching into the developer's own graph and deleting whatever
    they were looking at. The refusal travels the same path as "Neo4j is down": a note,
    a printed SKIPPED line, and a PostgreSQL corpus that is written regardless.

    Raises:
        GraphUnavailableError: If this is a test process, or the driver package or the
            credentials are absent.
    """
    from app.config import get_settings  # noqa: PLC0415 - CLI-only dependency

    if "PYTEST_CURRENT_TEST" in os.environ:
        raise GraphUnavailableError(
            "refusing to touch the shared Neo4j graph from a test process "
            "(there is no scratch instance to isolate it)"
        )
    settings = get_settings()
    try:
        from neo4j import AsyncGraphDatabase  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover - the package is a hard dependency
        raise GraphUnavailableError(f"the neo4j driver is not installed: {exc}") from exc
    if not settings.neo4j_uri:
        raise GraphUnavailableError("NEO4J_URI is not configured")
    return AsyncGraphDatabase.driver(
        settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
    )


#: Every contributor to this node is a demo source. The predicate the wipe turns on, and
#: the only thing standing between ``--wipe`` and a real extracted entity that happened
#: to share a name with a demo one.
_ONLY_DEMO_SOURCES = (
    "all(part IN split(coalesce(n.file_path, ''), $sep) "
    "WHERE last(split(part, $tagsep)) STARTS WITH $source_prefix)"
)

_WIPE_PARAMS = {
    "tag": DEMO_GRAPH_TAG,
    "sep": _GRAPH_FIELD_SEP,
    "tagsep": _TENANT_TAG_SEP,
    "source_prefix": _SOURCE_PREFIX,
}


async def seed_graph(tenant_tags: Sequence[str]) -> GraphSummary:
    """Write the demo knowledge graph into Neo4j, and report what it did.

    Idempotent: a second run finds the corpus already there and writes nothing, exactly
    as every other writer in :mod:`app.demo` behaves.

    Args:
        tenant_tags: The tenant metadata values the corpus is distributed across.

    Returns:
        The per-store counts.

    Raises:
        GraphUnavailableError: If Neo4j cannot be reached.
    """
    graph = build_graph(tenant_tags)
    label = _quoted(_workspace_label())
    created_at = int(datetime.now().timestamp())  # noqa: DTZ005 - LightRAG stores local
    summary = GraphSummary(kinds=graph.kinds())

    driver = _driver()
    try:
        async with driver.session() as session:
            held = {
                record["entity_id"]: record["demo_tag"]
                for record in await (
                    await session.run(
                        f"MATCH (n:`{label}`) WHERE n.entity_id IN $ids "
                        "RETURN n.entity_id AS entity_id, n.demo_tag AS demo_tag",
                        ids=[entity.entity_id for entity in graph.entities],
                    )
                ).data()
            }
            summary.existing = sum(1 for tag in held.values() if tag == DEMO_GRAPH_TAG)
            summary.refused = tuple(
                sorted(name for name, tag in held.items() if tag != DEMO_GRAPH_TAG)
            )
            if summary.existing:
                return summary

            writable = [e for e in graph.entities if e.entity_id not in held]
            if writable:
                await (
                    await session.run(
                        f"UNWIND $rows AS row CREATE (n:`{label}`:`{DEMO_NODE_LABEL}`) "
                        "SET n = row.props",
                        rows=[
                            {"props": entity.properties(created_at=created_at)}
                            for entity in writable
                        ],
                    )
                ).consume()
                summary.nodes = len(writable)

            names = {entity.entity_id for entity in writable}
            edges = [
                relation
                for relation in graph.relations
                if relation.source in names and relation.target in names
            ]
            if edges:
                await (
                    await session.run(
                        f"UNWIND $rows AS row "
                        f"MATCH (a:`{label}`:`{DEMO_NODE_LABEL}` {{entity_id: row.source}}) "
                        f"MATCH (b:`{label}`:`{DEMO_NODE_LABEL}` {{entity_id: row.target}}) "
                        "CREATE (a)-[r:DIRECTED]->(b) SET r = row.props",
                        rows=[
                            {
                                "source": relation.source,
                                "target": relation.target,
                                "props": relation.properties(created_at=created_at),
                            }
                            for relation in edges
                        ],
                    )
                ).consume()
                summary.relationships = len(edges)
    except GraphUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - every driver error is the same outcome here
        raise GraphUnavailableError(f"Neo4j write failed: {exc}") from exc
    finally:
        await driver.close()
    return summary


async def wipe_graph() -> GraphWipeSummary:
    """Delete every demo node whose provenance is entirely this corpus'.

    ``DETACH DELETE`` takes each node's relationships with it, and every relationship
    this module writes has a demo node at both ends, so the corpus leaves nothing behind.

    Returns:
        What was removed, and what was deliberately kept.

    Raises:
        GraphUnavailableError: If Neo4j cannot be reached.
    """
    label = _quoted(_workspace_label())
    summary = GraphWipeSummary()
    driver = _driver()
    try:
        async with driver.session() as session:
            kept = await (
                await session.run(
                    f"MATCH (n:`{label}`) WHERE n.demo_tag = $tag "
                    f"AND NOT {_ONLY_DEMO_SOURCES} RETURN n.entity_id AS entity_id",
                    **_WIPE_PARAMS,
                )
            ).data()
            summary.kept = tuple(sorted(row["entity_id"] for row in kept))
            # Counted before the delete rather than returned by it: a ``DETACH DELETE``
            # that also aggregates is easy to write and easy to write *wrong*, and this
            # count is the number the operator reads to decide the wipe was complete.
            doomed = await (
                await session.run(
                    f"MATCH (n:`{label}`) WHERE n.demo_tag = $tag AND {_ONLY_DEMO_SOURCES} "
                    "RETURN count(n) AS total",
                    **_WIPE_PARAMS,
                )
            ).single()
            summary.nodes = int(doomed["total"]) if doomed else 0
            if summary.nodes:
                await (
                    await session.run(
                        f"MATCH (n:`{label}`) WHERE n.demo_tag = $tag "
                        f"AND {_ONLY_DEMO_SOURCES} DETACH DELETE n",
                        **_WIPE_PARAMS,
                    )
                ).consume()
    except GraphUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - every driver error is the same outcome here
        raise GraphUnavailableError(f"Neo4j wipe failed: {exc}") from exc
    finally:
        await driver.close()
    return summary


async def prune_graph_source(file_path: str) -> tuple[int, tuple[str, ...]]:
    """Delete the graph elements whose **only** contributor is ``file_path``.

    This removes **real extracted knowledge**, which is why it is not part of
    ``--wipe`` and why it takes the source's exact tagged path rather than a pattern.
    It exists for the case the demo environment actually had: a document ingested during
    testing, whose two extracted entities were the entire content of the Graph screen and
    had nothing to do with the corpus on show. Deleting the document's row does not
    delete its entities — LightRAG has no such cascade — so this is the operation that
    finishes the job.

    An entity that any *other* document also contributed to is kept, because its
    description was merged across both and removing it would take the other document's
    knowledge with it.

    Args:
        file_path: The tagged path exactly as the graph stores it, e.g.
            ``"t1::example.pdf"``.

    Returns:
        ``(deleted, kept)`` — how many nodes went, and the names of the ones that were
        shared with another source and therefore stayed.

    Raises:
        GraphUnavailableError: If Neo4j cannot be reached.
    """
    label = _quoted(_workspace_label())
    driver = _driver()
    params = {"path": file_path, "sep": _GRAPH_FIELD_SEP}
    try:
        async with driver.session() as session:
            shared = await (
                await session.run(
                    f"MATCH (n:`{label}`) WHERE $path IN split(coalesce(n.file_path, ''), $sep) "
                    "AND size(split(coalesce(n.file_path, ''), $sep)) > 1 "
                    "RETURN n.entity_id AS entity_id",
                    **params,
                )
            ).data()
            doomed = await (
                await session.run(
                    f"MATCH (n:`{label}`) WHERE coalesce(n.file_path, '') = $path "
                    "RETURN count(n) AS total",
                    **params,
                )
            ).single()
            removed = int(doomed["total"]) if doomed else 0
            if removed:
                await (
                    await session.run(
                        f"MATCH (n:`{label}`) WHERE coalesce(n.file_path, '') = $path "
                        "DETACH DELETE n",
                        **params,
                    )
                ).consume()
            return (removed, tuple(sorted(row["entity_id"] for row in shared)))
    except GraphUnavailableError:
        raise
    except Exception as exc:  # noqa: BLE001 - every driver error is the same outcome here
        raise GraphUnavailableError(f"Neo4j prune failed: {exc}") from exc
    finally:
        await driver.close()
