"""A fan-out reports the retrieval it actually performed — the same way a single pass does.

**The measured symptom.** On ``taif_run1``, ``select event_type, count(*) from run_events
where run_id = '<team run>'`` returned eleven event types and **no ``retrieval`` row at
all**, while the identical query on the single-lane path emitted four plus a
``provenance``. Retrieval genuinely happened — :class:`~aegis.agent.team.
SharedRetrievalPool` fetched the corpus once for the lanes — but the pool reduced the
result to its ``answer_context`` string and dropped the sources, scores, provenance and
graph delta on the floor. Three console surfaces read those events and only those, so
all three reported a falsehood: the Sources panel said "This run retrieved nothing, so
the answer is not grounded in a document" above an answer quoting a real policy, the
Graph screen said "0/226 traversed" on every run (it always routes to a team), and the
Grounded chip stayed dark.

Two tests, because there are two ways to get this wrong. Emitting nothing is the bug;
emitting a ``done`` with zeroes when the pool degraded would be worse, because it is a
claim about the tenant's corpus made on the strength of our own failure.
"""

from __future__ import annotations

import pytest

from aegis.retrieval.models import GraphDelta, RetrievalResult, Source
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalScope

from .test_team_fanout import DEMO_QUERY, _drive, _one, build_team_deps

pytestmark = pytest.mark.anyio


def _statuses(events: list[dict]) -> list[str]:
    return [e["status"] for e in events if e["type"] == "retrieval"]


async def test_a_team_run_emits_the_retrieval_it_performed():
    """The shared pool's ONE retrieval is reported as the funnel the console reads."""
    deps, rec = build_team_deps()

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:  # noqa: ARG001
        rec.retrievals.append(query)
        return RetrievalResult(
            answer_context="Shared corpus context for the run.",
            sources=[
                Source(id="kb-1", text="Escalation policy", score=0.9),
                Source(id="kb-2", text="Refund window", score=0.7),
            ],
            num_candidates=17,
            graph_delta=GraphDelta(
                nodes=[GraphNode(id="policy", label="Policy", kind="doc")],
                edges=[GraphEdge(source="policy", target="kb-1", relation="cites")],
            ),
            cache_hit=False,
        )

    deps.retrieve = retrieve
    events = await _drive(deps, DEMO_QUERY)

    assert _one(events, "routing")["depth"] == "team", "this test is about the fan-out"
    # The same four the single-lane ``retrieve`` node emits, in the same order.
    assert _statuses(events) == ["started", "candidates", "reranked", "done"]

    done = [e for e in events if e["type"] == "retrieval" and e["status"] == "done"][0]
    assert done["num_candidates"] == 17, "the honest pre-rerank pool size, not len(sources)"
    assert [s["id"] for s in done["scored_sources"]] == ["kb-1", "kb-2"]
    # `_update_dashboards` merges the live graph slice from exactly these two keys, and
    # notes the run as grounded from `touched_nodes`. Empty here is the dead Graph screen.
    assert [n["id"] for n in done["touched_nodes"]] == ["policy"]
    assert len(done["touched_edges"]) == 1
    assert _one(events, "provenance")["cache_hit"] is False

    assert len(rec.retrievals) == 1, "reporting the retrieval must not add a second one"


async def test_a_degraded_pool_reports_nothing_rather_than_an_empty_result():
    """A retrieval that failed is not a retrieval that found nothing.

    The pool swallows a retrieval failure so the lanes still run (context-free), which
    is the right trade — but the reporting must not launder that failure into a
    ``done`` event carrying zero candidates and zero sources. That event would tell the
    console we searched the tenant's documents and they held nothing.
    """
    deps, rec = build_team_deps()

    async def failing_retrieve(query: str, *, scope: RetrievalScope):  # noqa: ANN202, ARG001
        rec.retrievals.append(query)
        raise RuntimeError("the vector store is unreachable")

    deps.retrieve = failing_retrieve
    events = await _drive(deps, DEMO_QUERY)

    assert _statuses(events) == ["started"], "no candidates/reranked/done was fabricated"
    assert [e for e in events if e["type"] == "provenance"] == []
    # The run still answers: a failed pool is a degraded fan-out, not a failed run.
    assert _one(events, "run_finished")["status"] == "completed"
