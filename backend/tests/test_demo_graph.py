"""The demo knowledge graph — read back through the code that will actually read it.

The graph :mod:`app.demo_graph` writes is only worth anything if ``GET /graph`` serves
it, and what that endpoint serves is not what was written: it is what
:func:`aegis.retrieval.types.scoped_graph` leaves after applying the tenant boundary to
the provenance :func:`aegis.retrieval.lightrag_backend._owners_of` reads off each
element's ``file_path``. So every assertion here goes through **those two functions**,
not through a restatement of the ownership rules — the failure this guards against is a
corpus whose ``file_path`` values are subtly the wrong shape, which no amount of
self-consistent checking inside the builder can see.

Three claims, and only three:

* **a tenant sees a real graph** — enough nodes and edges to be worth rendering, with no
  isolated node and no dangling edge;
* **a tenant never sees another's records** — the boundary holds in both directions,
  and the two tenants' case sets are genuinely disjoint rather than nominally split;
* **no relation carries two owners.** ``scoped_graph`` requires *every* owner of an edge
  to be visible. An edge that merged two tenants' provenance would therefore vanish from
  both views, taking the graph's structure with it and leaving a screen full of unlinked
  nodes — the exact failure that looks like "the data is fine, the viz is broken".

Nothing here needs a database. The builder is pure.
"""

from __future__ import annotations

from aegis.retrieval.lightrag_backend import _owners_of
from aegis.retrieval.types import GraphEdge, GraphNode, scoped_graph

from app.demo import DEMO_PREFIX
from app.demo_graph import DEMO_GRAPH_TAG, build_graph

#: The tenants the corpus is built for here — the two ``app.seed`` creates.
_TAGS = ("t1", "t2")


def _as_read(tag: str | None) -> tuple[list[GraphNode], list[GraphEdge]]:
    """Return the graph as ``GET /graph`` would serve it to ``tag``'s principal.

    Args:
        tag: The tenant metadata value, or ``None`` for a platform-wide read.

    Returns:
        The ``(nodes, edges)`` that scope may see.
    """
    graph = build_graph(_TAGS)
    nodes = [
        GraphNode(
            id=entity.entity_id,
            label=entity.entity_id,
            kind=entity.kind,
            owners=_owners_of("<SEP>".join(entity.sources)),
        )
        for entity in graph.entities
    ]
    edges = [
        GraphEdge(
            source=relation.source,
            target=relation.target,
            relation=relation.keywords,
            owners=_owners_of("<SEP>".join(relation.sources)),
        )
        for relation in graph.relations
    ]
    # ``None`` in the visible list is the shared corpus, exactly as
    # ``RetrievalScope.visible_tenant_values`` composes it for a tenant-scoped caller.
    return scoped_graph(nodes, edges, visible=None if tag is None else [tag, None])


def test_the_graph_tag_opens_with_the_corpus_prefix():
    """The Neo4j marker and the SQL marker are one convention, not two.

    Asserted because they are declared in different modules and cannot import each
    other: a graph tagged ``graph-demo`` would still wipe correctly and would still be
    a second, undocumented convention for the same thing.
    """
    assert DEMO_GRAPH_TAG.startswith(DEMO_PREFIX)


def test_the_graph_is_deterministic():
    """Two builds are identical, so a screenshot survives a rebuild."""
    first, second = build_graph(_TAGS), build_graph(_TAGS)
    assert [e.entity_id for e in first.entities] == [e.entity_id for e in second.entities]
    assert [(r.source, r.target, r.keywords) for r in first.relations] == [
        (r.source, r.target, r.keywords) for r in second.relations
    ]


def test_every_relation_has_exactly_one_owner():
    """No edge merges two tenants' provenance — the property that keeps edges visible."""
    graph = build_graph(_TAGS)
    assert graph.relations, "a graph with no relations is a list, not a graph"
    for relation in graph.relations:
        owners = _owners_of("<SEP>".join(relation.sources))
        assert owners is not None, f"{relation.keywords} has unattributable provenance"
        assert len(owners) == 1, f"{relation.keywords} is owned by {owners}"


def test_each_tenant_is_served_a_whole_graph():
    """What a tenant is shown is connected, non-trivial, and free of dangling edges."""
    for tag in _TAGS:
        nodes, edges = _as_read(tag)
        assert len(nodes) >= 30, f"{tag} sees only {len(nodes)} nodes"
        assert len(edges) >= 30, f"{tag} sees only {len(edges)} edges"

        ids = {node.id for node in nodes}
        assert all(edge.source in ids and edge.target in ids for edge in edges)

        touched = {edge.source for edge in edges} | {edge.target for edge in edges}
        assert ids == touched, f"{tag} sees isolated nodes: {sorted(ids - touched)}"


def test_a_tenant_is_never_shown_another_tenants_records():
    """The boundary holds: each tenant's own records are private to it.

    Measured against the platform-wide read, so the claim is "these nodes exist and are
    withheld" rather than "these nodes were never built" — the second is not a boundary.
    """
    everything, _ = _as_read(None)
    first, _ = _as_read(_TAGS[0])
    second, _ = _as_read(_TAGS[1])

    all_ids = {node.id for node in everything}
    first_ids = {node.id for node in first}
    second_ids = {node.id for node in second}

    assert first_ids < all_ids and second_ids < all_ids
    assert first_ids | second_ids <= all_ids
    # The shared corpus and the taxonomy are in both views by design; the *records* are
    # not, and there must be some of each on both sides or the split proved nothing.
    private_first = first_ids - second_ids
    private_second = second_ids - first_ids
    assert private_first and private_second
    assert not (private_first & private_second)
