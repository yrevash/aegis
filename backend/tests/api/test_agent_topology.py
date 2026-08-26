"""``GET /agent/topology`` — the served agent-graph shape must BE the real graph.

The console's orchestration map used to hardcode its own DAG and drifted badly from
:mod:`aegis.agent.graph`: nine nodes instead of the real set, and a human-approval
branch drawn out of a step that never decided anything, even though the graph gates on
**tool risk** in ``gate``. These tests pin the two properties that made that drift
possible — the full node set, and where the gate's branches actually go — plus the
offline snapshot the web console falls back to when the backend is unreachable.

Everything here is offline: the topology is compiled over inert deps, so no node body
ever runs and nothing reaches the wire.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from aegis.agent import graph_topology
from aegis.agent.graph import NODE_LABELS

from app.core.security import create_access_token

pytestmark = pytest.mark.asyncio


#: Every executable node of the real graph. Written out longhand on purpose: a test
#: that derived this from the graph would pass no matter how the graph changed.
REAL_NODE_IDS = {
    "guard_input",
    "route",
    "answer_memory",
    "recall_memory",
    # The adaptive multi-agent lane: allocate the width, run the agents concurrently
    # INSIDE ``run_team`` (an asyncio.gather in one node, not a subgraph), then merge.
    "plan_team",
    "run_team",
    "synthesize",
    "retrieve",
    "plan",
    "gate",
    "approval",
    "act",
    # ``verify`` sits between them: the round is judged against something
    # outside the model before ``reflect`` decides whether to go round again.
    "verify",
    "reflect",
    "generate",
    "guard_output",
    "stream",
    "persist_memory",
}

#: The web console's offline fallback snapshot, which must equal the served topology.
_SNAPSHOT = (
    Path(__file__).resolve().parents[3] / "web" / "src" / "config" / "graphTopology.json"
)


def _headers(role: str = "client") -> dict[str, str]:
    """Auth header for a principal minted from a *fine* role (coarse derived)."""
    token = create_access_token(user_id=None, username="topology", role=role, tenant_id=1)
    return {"Authorization": f"Bearer {token}"}


async def test_topology_serves_every_real_node_with_its_label(client):
    resp = await client.get("/agent/topology", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()

    served = {n["id"] for n in body["nodes"]}
    assert served == REAL_NODE_IDS, "served topology must be the real 18-node graph"
    # Labels come from the ONE table the ``_timed`` wrapper streams from.
    for node in body["nodes"]:
        assert node["label"] == NODE_LABELS[node["id"]]
        assert node["label"]
    # The run enters at the input rail and can only finish at a blocked input or
    # after the memory-persist tail.
    assert {n["id"] for n in body["nodes"] if n["entry"]} == {"guard_input"}
    assert {n["id"] for n in body["nodes"] if n["terminal"]} == {
        "guard_input",
        "persist_memory",
    }


async def test_human_gate_branches_out_of_gate_and_nowhere_else(client):
    """The human gate hangs off ``gate`` — the tool-risk decision — and only there."""
    resp = await client.get("/agent/topology", headers=_headers())
    edges = resp.json()["edges"]

    out_of_gate = {e["target"] for e in edges if e["source"] == "gate"}
    assert out_of_gate == {"approval", "act"}
    assert all(e["conditional"] for e in edges if e["source"] == "gate")

    # Retrieval hands straight to the planner: nothing sits between them any more.
    assert {e["target"] for e in edges if e["source"] == "retrieve"} == {"plan"}
    # Nothing but ``gate`` can reach the human approval node, and only ``gate`` and
    # ``approval`` can reach ``act`` — so no path executes an action ungated.
    assert not [e for e in edges if e["target"] == "approval" and e["source"] != "gate"]
    assert {e["source"] for e in edges if e["target"] == "act"} == {"gate", "approval"}


async def test_topology_requires_authentication(client):
    assert (await client.get("/agent/topology")).status_code == 401


async def test_web_offline_snapshot_matches_the_real_topology():
    """The console's mock/offline fallback must not become a second stale copy.

    The console renders from the served topology, but falls back to a checked-in
    snapshot when the backend is unreachable (mock mode). That snapshot is the last
    place drift could hide, so it is pinned byte-for-byte against the real graph.
    """
    if not _SNAPSHOT.is_file():  # pragma: no cover - backend checked out standalone
        pytest.skip(f"web snapshot not present at {_SNAPSHOT}")
    assert json.loads(_SNAPSHOT.read_text()) == graph_topology()
