"""The in-process graph slice is scoped by TENANT, not only by persona.

`GET /graph` returns Neo4j's durable graph unioned with a per-run in-process delta.
The durable half is provenance-checked by `scoped_graph`; the delta is not, because
its nodes carry no `owners` field at all — so the bucket key is the only thing
separating two tenants there.

It used to be keyed on the persona alone. Every admin tier shares one persona, so a
live audit found tenant 1's entity names — "Refund Escalation Policy", "Northwind
Trading SLA and Escalation Runbook" — rendering in tenant 2's knowledge graph, and
growing as tenant 1 kept querying. Two accounts of the *other* tenant were affected,
including a `client`, which is the least-privileged role on the platform.
"""

from __future__ import annotations

from app.api.routes import GraphStore, graph_slice_key

_NODE = {"id": "n1", "label": "Refund Escalation Policy", "kind": "entity"}


def test_two_tenants_sharing_a_persona_do_not_share_a_slice():
    """The exact leak, as a unit: same persona, different tenants."""
    store = GraphStore()
    store.merge(graph_slice_key(1, "operations_lead"), [_NODE], [])

    mine = store.response(graph_slice_key(1, "operations_lead"))
    theirs = store.response(graph_slice_key(2, "operations_lead"))

    assert [n.label for n in mine.nodes] == ["Refund Escalation Policy"]
    assert theirs.nodes == [], "tenant 2 must not see tenant 1's live delta"


def test_the_persona_boundary_still_holds_inside_one_tenant():
    """Adding the tenant must not cost the control that was already there.

    The original claim — a `client` does not see what an operations persona retrieved —
    is still enforced; the tenant is a boundary *around* it, not a replacement for it.
    """
    store = GraphStore()
    store.merge(graph_slice_key(1, "operations_lead"), [_NODE], [])
    assert store.response(graph_slice_key(1, "client")).nodes == []


def test_an_untenanted_principal_gets_its_own_bucket_not_a_wildcard():
    """`None` is platform staff's own slice, never "every tenant's".

    A platform admin reads the whole durable graph through ALL_TENANTS, which is
    authorised and provenance-checked. The live delta is not, so pooling every tenant's
    traffic into the untenanted bucket would recreate the leak for the one principal
    best placed to mistake it for their own data.
    """
    store = GraphStore()
    store.merge(graph_slice_key(1, "operations_lead"), [_NODE], [])
    assert store.response(graph_slice_key(None, "operations_lead")).nodes == []
    assert graph_slice_key(None, "x") != graph_slice_key(1, "x")
