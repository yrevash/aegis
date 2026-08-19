"""Cross-tenant isolation of the **graph** arm — nodes, edges, and untagged rows.

``test_tenant_isolation.py`` pins the candidate path: cache partitions, collections, the
keyword predicate. Nothing pinned the graph half of a recall, and it was open in three
independent ways:

* ``_scoped_recall`` filtered candidates and passed ``nodes``/``edges`` straight through,
  justified as "entity labels for the visualisation, not document content". An edge's
  ``relation`` is LightRAG's relationship *description* — a sentence the extractor wrote
  from the source document's text — so a whole clause of another tenant's document
  reached the browser on the SSE ``retrieval.done`` event.
* the lite backend's ``_graph_slice`` drew edges from ``self.relations``, one set merged
  across every tenant in the process, keeping any edge whose two endpoints this scope's
  chunks happened to touch.
* an untagged stored ``file_path`` was read as the **shared** corpus, which every tenant
  may read, and was stamped ``tenant_attributed: True`` on the way out — so ownership was
  asserted rather than established and the loud refusal built for exactly that case could
  never fire.

The secrets below exist nowhere else in this repository, so an assertion that one of them
did not reach a tenant is an assertion about this path and not about a coincidence.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.retrieval.graph_extract import Entity, Relation
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.memory import InMemoryKnowledgeBackend
from aegis.retrieval.models import Chunk
from aegis.retrieval.types import (
    TENANT_METADATA_KEY,
    GraphEdge,
    GraphNode,
    RetrievalScope,
    scoped_graph,
)

from .conftest import RecordingComplete, SequenceEmbed

_TENANT_A = 60601
_TENANT_B = 60602

#: Strings that exist nowhere else in this repository.
_SECRET = "AUDITA-NARWHAL-8842"
_SECRET_LITE = "AUDITA-OKAPI-5591"


def _backend(rag: object) -> LightRAGBackend:
    backend = LightRAGBackend(
        complete=RecordingComplete("{}"), embed=SequenceEmbed([1.0, 0.0])
    )
    backend._rag = rag
    return backend


class _RagWithTwoTenants:
    """A LightRAG whose one shared graph holds both tenants' extractions.

    That is the real shape: one instance means one working directory, one vector index
    and one Neo4j graph for every tenant in the process, and LightRAG exposes no
    per-query metadata predicate to push a tenant into.
    """

    async def aquery(self, query: str, param: object) -> object:
        return SimpleNamespace(
            context="",
            raw_data={
                "data": {
                    "chunks": [
                        {
                            "id": "a1",
                            "content": "Tenant A's own passage about refunds.",
                            "file_path": f"t{_TENANT_A}::terms.pdf",
                        },
                        {
                            "id": "b1",
                            "content": f"Tenant B: the {_SECRET} acquisition closes soon.",
                            "file_path": f"t{_TENANT_B}::board-pack.pdf",
                        },
                    ],
                    "entities": [
                        {
                            "entity": "Tenant A Ltd",
                            "entity_type": "organization",
                            "file_path": f"t{_TENANT_A}::terms.pdf",
                        },
                        {
                            "entity": _SECRET,
                            "entity_type": "organization",
                            "file_path": f"t{_TENANT_B}::board-pack.pdf",
                        },
                    ],
                    "relationships": [
                        {
                            "src_id": "Tenant A Ltd",
                            "tgt_id": _SECRET,
                            "description": (
                                f"{_SECRET} is being acquired for 400 million, per the "
                                "board pack."
                            ),
                            "file_path": f"t{_TENANT_B}::board-pack.pdf",
                        }
                    ],
                }
            },
        )


class _RagWithUnattributedGraph(_RagWithTwoTenants):
    """The same graph, with LightRAG reporting no provenance for it at all."""

    async def aquery(self, query: str, param: object) -> object:
        raw = await super().aquery(query, param)
        for element in raw.raw_data["data"]["entities"]:
            element.pop("file_path")
        for element in raw.raw_data["data"]["relationships"]:
            element.pop("file_path")
        return raw


class _RagWithAnUntaggedChunk:
    """LightRAG returns a chunk whose ``file_path`` carries no tenant tag.

    Every way that happens is real: a corpus loaded into the working directory by hand
    (which is how the demo box's LightRAG store was populated before Phase 4 existed), a
    row written before ``_tag_file_path`` did, or a LightRAG version that normalises the
    path it stores.
    """

    async def aquery(self, query: str, param: object) -> object:
        return SimpleNamespace(
            context="",
            raw_data={
                "data": {
                    "chunks": [
                        {
                            "id": "u1",
                            "content": f"Untagged: the {_SECRET} settlement figure.",
                            "file_path": "board-pack.pdf",
                        },
                        {
                            "id": "a1",
                            "content": "Tenant A's own tagged passage.",
                            "file_path": f"t{_TENANT_A}::terms.pdf",
                        },
                    ]
                }
            },
        )


# ─────────────────────────────────────────────────────────────────────────────
# The full backend's graph arm
# ─────────────────────────────────────────────────────────────────────────────


async def test_the_graph_arm_does_not_return_another_tenants_entity_labels() -> None:
    """An entity name and a relation phrase are document content, and are filtered."""
    recall = await _backend(_RagWithTwoTenants()).recall_ranked(
        "what is happening?", top_k=10, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    texts = [c.text for lst in recall.lists for c in lst.candidates]
    assert not any(_SECRET in text for text in texts), (
        f"the candidate filter itself failed: {texts}"
    )

    leaked = [node.label for node in recall.nodes if _SECRET in node.label]
    leaked += [edge.relation for edge in recall.edges if _SECRET in edge.relation]
    assert leaked == [], (
        "the graph arm returned another tenant's entity/relation text to a "
        f"tenant-scoped request: {leaked}"
    )
    # Non-vacuity: this scope's *own* node survives, so the filter is not simply
    # emptying the graph.
    assert [node.label for node in recall.nodes] == ["Tenant A Ltd"]


async def test_a_graph_element_with_no_provenance_is_shown_to_no_tenant() -> None:
    """Unknown ownership fails closed, exactly as an unattributable chunk does."""
    recall = await _backend(_RagWithUnattributedGraph()).recall_ranked(
        "what is happening?", top_k=10, scope=RetrievalScope(tenant_id=_TENANT_A)
    )
    assert recall.nodes == [] and recall.edges == []


async def test_an_unscoped_run_still_sees_its_whole_graph() -> None:
    """A single-tenant/eval run has no boundary to cross, so nothing is withheld."""
    recall = await _backend(_RagWithUnattributedGraph()).recall_ranked(
        "what is happening?", top_k=10, scope=RetrievalScope(tenant_id=None)
    )
    assert {node.label for node in recall.nodes} == {"Tenant A Ltd", _SECRET}


async def test_a_chunk_whose_tenant_tag_is_missing_is_not_served_to_every_tenant() -> None:
    """An untagged row reads as the shared corpus, which every tenant may read.

    Contrast the lexical arm: ``chunks.tenant_id`` is ``NOT NULL``, so a row whose owner
    is unknown cannot exist. That arm fails closed; this one failed open. The refusal is
    per row, not per request — the tenant's own tagged passage still comes back.
    """
    recall = await _backend(_RagWithAnUntaggedChunk()).recall_ranked(
        "settlement", top_k=10, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    texts = [c.text for lst in recall.lists for c in lst.candidates]
    assert not any(_SECRET in text for text in texts), (
        "a chunk with no recoverable owner was served to tenant "
        f"{_TENANT_A} as if it were shared-corpus knowledge: {texts}"
    )
    assert any("own tagged passage" in text for text in texts), (
        "the refusal took the tenant's own rows with it"
    )


# ─────────────────────────────────────────────────────────────────────────────
# The lite backend's graph slice
# ─────────────────────────────────────────────────────────────────────────────


class _Extractor:
    """Names two shared entities everywhere; the edge only in the secret-bearing chunk."""

    name = "audit-extractor"

    async def extract(self, text: str):
        acme = Entity.make("Acme", "organization")
        beta = Entity.make("Beta", "organization")
        if _SECRET_LITE in text:
            return ([acme, beta], [Relation(acme.id, beta.id, _SECRET_LITE)])
        return ([acme, beta], [])


def _two_tenant_chunks() -> list[Chunk]:
    return [
        Chunk(
            id="a1",
            doc_id="a",
            ordinal=0,
            text="Acme and Beta are both mentioned in tenant A's handbook.",
            metadata={TENANT_METADATA_KEY: f"t{_TENANT_A}"},
        ),
        Chunk(
            id="b1",
            doc_id="b",
            ordinal=0,
            text=f"Acme {_SECRET_LITE} Beta — tenant B's private merger note.",
            metadata={TENANT_METADATA_KEY: f"t{_TENANT_B}"},
        ),
    ]


async def test_the_lite_graph_slice_does_not_expose_a_foreign_relation() -> None:
    """``self.relations`` is process-global; an edge needs its own tenant predicate."""
    backend = InMemoryKnowledgeBackend(_two_tenant_chunks(), extractor=_Extractor())

    recall = await backend.recall_ranked(
        "Acme Beta", top_k=10, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    texts = [c.text for lst in recall.lists for c in lst.candidates]
    assert not any(_SECRET_LITE in text for text in texts), (
        f"the candidate filter itself failed: {texts}"
    )
    leaked = [edge.relation for edge in recall.edges if _SECRET_LITE in edge.relation]
    assert leaked == [], (
        "tenant A's graph slice carries a relation phrase extracted only from tenant "
        f"B's chunk: {leaked}"
    )
    # Non-vacuity: the entities both tenants' own chunks mention are still drawn.
    assert {node.label for node in recall.nodes} == {"Acme", "Beta"}


async def test_the_lite_graph_slice_keeps_an_edge_the_scope_own_chunk_produced() -> None:
    """The owner of the edge sees it — the predicate is provenance, not suppression."""
    backend = InMemoryKnowledgeBackend(_two_tenant_chunks(), extractor=_Extractor())

    recall = await backend.recall_ranked(
        "Acme Beta", top_k=10, scope=RetrievalScope(tenant_id=_TENANT_B)
    )
    assert [edge.relation for edge in recall.edges] == [_SECRET_LITE]


# ─────────────────────────────────────────────────────────────────────────────
# The rule itself
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("owners", "kept"),
    [
        (("t1",), True),  # own
        ((None,), True),  # the shared corpus
        (("t1", "t2"), True),  # merged: this scope's own corpus carries the name too
        (("t2",), False),  # another tenant's alone
        (None, False),  # provenance unknown → refused
    ],
)
def test_a_node_survives_when_any_of_its_owners_is_visible(owners, kept) -> None:
    """A node's label is an entity *name*, so one visible owner is enough to show it."""
    nodes, _ = scoped_graph(
        [GraphNode(id="n", label="N", kind="organization", owners=owners)],
        [],
        visible=["t1", None],
    )
    assert bool(nodes) is kept


@pytest.mark.parametrize(
    ("owners", "kept"),
    [
        (("t1",), True),
        ((None,), True),
        (("t1", "t2"), False),  # the description is prose merged across both sources
        (("t2",), False),
        (None, False),
    ],
)
def test_an_edge_survives_only_when_every_owner_is_visible(owners, kept) -> None:
    """An edge's ``relation`` is synthesised prose, so one foreign source condemns it."""
    nodes = [
        GraphNode(id="a", label="A", kind="organization", owners=("t1",)),
        GraphNode(id="b", label="B", kind="organization", owners=("t1",)),
    ]
    _, edges = scoped_graph(
        nodes,
        [GraphEdge(source="a", target="b", relation="r", owners=owners)],
        visible=["t1", None],
    )
    assert bool(edges) is kept


def test_an_edge_whose_endpoint_was_dropped_does_not_dangle() -> None:
    """A viz cannot lay out an edge to a node the boundary removed."""
    nodes = [
        GraphNode(id="a", label="A", kind="organization", owners=("t1",)),
        GraphNode(id="b", label="B", kind="organization", owners=("t2",)),
    ]
    kept_nodes, kept_edges = scoped_graph(
        nodes,
        [GraphEdge(source="a", target="b", relation="r", owners=("t1",))],
        visible=["t1", None],
    )
    assert [n.id for n in kept_nodes] == ["a"]
    assert kept_edges == []


def test_an_unrestricted_read_is_something_a_caller_has_to_ask_for() -> None:
    """``visible=None`` is the deliberate platform-wide read, and it is not the default."""
    nodes = [GraphNode(id="a", label="A", kind="organization", owners=("t2",))]
    edges = [GraphEdge(source="a", target="a", relation="r", owners=None)]
    assert scoped_graph(nodes, edges, visible=None) == (nodes, edges)


def test_provenance_never_reaches_the_wire() -> None:
    """``owners`` is an isolation input, not a field that tells a tenant who else exists."""
    dumped = GraphNode(id="a", label="A", kind="organization", owners=("t2",)).model_dump()
    assert dumped == {"id": "a", "label": "A", "kind": "organization"}
