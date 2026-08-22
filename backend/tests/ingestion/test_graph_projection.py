"""What the ``graph`` stage hands the durable graph, and who is allowed to see it.

These are the pure half of the projection: the row shapes written into Neo4j. They are
worth asserting without a store because the shapes *are* the security boundary —
``GET /v1/graph`` decides visibility from the tenant tag inside ``file_path`` and from
nothing else (:func:`aegis.retrieval.types.scoped_graph`), so a projection that mistags an
element, or that merges two tenants' provenance onto one edge, is a cross-tenant leak
rather than a cosmetic defect.

The store half is deliberately not tested here. There is no scratch Neo4j — a test that
wrote into it would be writing into whatever graph the developer was looking at — which is
exactly why :func:`~app.ingestion.graph_projection.project_document_graph` refuses to run
in a test process at all, and why the stage records that refusal as an explicit *skipped*
rather than as a projection of zero.
"""

from __future__ import annotations

from aegis.retrieval.graph_extract import Entity, Relation

from app.ingestion.graph_projection import ProjectionResult, projection_rows


def test_every_projected_element_carries_the_tenant_that_owns_it() -> None:
    """The tag in ``file_path`` is the only evidence ``scoped_graph`` has.

    An element that reaches the graph without it is not "slightly less well described";
    it is unattributable, and an unattributable element is shown to nobody — so a
    projection that lost the tag would silently stop working rather than fail.
    """
    policy = Entity.make("Refund Escalation Policy", "policy")
    lead = Entity.make("team lead", "person")
    nodes, edges, _ = projection_rows(
        [policy, lead],
        [Relation(policy.id, lead.id, "escalates to")],
        tenant_value="t7",
        source="refund-policy.pdf",
        extractor="llm-cached",
    )

    assert {node["file_path"] for node in nodes} == {"t7::refund-policy.pdf"}
    assert [edge["file_path"] for edge in edges] == ["t7::refund-policy.pdf"]
    # LightRAG's merge key is the human name, which is also the label the console
    # renders — a node written under the extractor's internal `kind:normalised` id would
    # show up on the screen as "policy:refund escalation policy".
    assert {node["entity_id"] for node in nodes} == {
        "Refund Escalation Policy",
        "team lead",
    }
    assert [edge["keywords"] for edge in edges] == ["escalates to"]
    # No document prose on a node: a merged node's description belongs to whoever wrote
    # last, and prose there would hand one tenant's text to every other contributor.
    assert all("Refund" not in node["description"] for node in nodes[1:])


def test_an_entity_seen_on_many_chunks_is_one_node() -> None:
    """Nine mentions of one name are one node, and the first surface form is the record."""
    first = Entity.make("SLA clock", "system")
    again = Entity.make("sla CLOCK", "system")
    nodes, _, _ = projection_rows(
        [first, again, first],
        [],
        tenant_value="t1",
        source="policy.pdf",
        extractor="spacy",
    )

    assert [node["entity_id"] for node in nodes] == ["SLA clock"]


def test_a_relation_whose_endpoint_was_not_extracted_is_dropped_not_repaired() -> None:
    """A dangling edge is never written, and never invented.

    An edge to an entity this document did not state would be a fact the corpus does not
    contain — the one failure mode a knowledge graph must not have.
    """
    agent = Entity.make("support agent", "person")
    nodes, edges, dropped = projection_rows(
        [agent],
        [
            Relation(agent.id, "person:someone we never extracted", "assigns case to"),
            Relation(agent.id, agent.id, "relates to itself"),
        ],
        tenant_value="t1",
        source="policy.pdf",
        extractor="llm-cached",
    )

    assert len(nodes) == 1
    assert edges == []
    assert dropped == 2


def test_a_projection_that_landed_short_is_not_complete() -> None:
    """The flag the stage refuses to report a success on.

    ``nodes`` is what the graph store confirmed holding, never what it was handed; the two
    were the same number for the whole of the time the graph was not growing.
    """
    assert ProjectionResult(nodes=10, edges=3, attempted_nodes=10, attempted_edges=3).complete
    assert not ProjectionResult(nodes=0, edges=0, attempted_nodes=10).complete
    assert not ProjectionResult(nodes=10, edges=0, attempted_nodes=10, attempted_edges=3).complete


def test_the_writer_labels_nodes_where_lightrags_reader_looks(monkeypatch):
    """The writer's label must follow LightRAG's own three-step resolution.

    ``lightrag.kg.neo4j_impl`` resolves ``NEO4J_WORKSPACE`` → else the workspace it was
    constructed with (Aegis threads ``WORKSPACE`` into it) → else ``"base"``. This
    implemented only the first and last, so a deployment setting ``WORKSPACE`` alone —
    the shape every ``.env`` here uses to isolate a run — wrote every node under
    ``base`` while the reader matched on the run's own label. The graph was fully
    written and entirely invisible.
    """
    from app.ingestion.graph_projection import _workspace_label

    monkeypatch.delenv("NEO4J_WORKSPACE", raising=False)
    monkeypatch.delenv("WORKSPACE", raising=False)
    assert _workspace_label() == "base", "no workspace at all is still LightRAG's base"

    # The step that was missing: WORKSPACE alone must reach the label.
    monkeypatch.setenv("WORKSPACE", "run1")
    assert _workspace_label() == "run1"

    # NEO4J_WORKSPACE still wins, exactly as it does inside LightRAG.
    monkeypatch.setenv("NEO4J_WORKSPACE", "explicit")
    assert _workspace_label() == "explicit"
