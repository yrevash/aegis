"""The backfill must reproduce the graph stage's inputs, or it rebuilds a different graph.

One claim, one failure mode.

The claim: a document ingested *before* the graph arm's indexes existed can be given them
without re-extracting it. Measured the day those indexes landed: 8 entity vectors, all of
them from **one** of 17 succeeded documents, so the arm was invisible to the entire
existing corpus.

The failure mode is a backfill that writes something *plausible but different*. Everything
the graph arm depends on is an exact string — the node's ``entity_id`` is what a matched
vector is looked up by, the node's ``source_id`` is a list of chunk ids that must equal the
ids the ``index`` stage published the passages under, and the extractor's name is embedded
inside every entity's vector. Rebuild any of them a little differently and the write
succeeds, the counts look right, and the arm matches entities whose nodes, or whose
passages, are not there. That is the same silence the indexes were built to end, with a
higher points_count.

So the test below is not a test of the reader. It runs the **real** ``graph`` stage against
a real PostgreSQL, records what the stage handed the projector, and then asks the backfill
to reconstruct the same thing from ``chunks.meta`` alone. Equality of those two is the only
statement worth making about this module: what the backfill replays is what an ingest
would have written.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from app.data import get_sessionmaker, set_tenant_scope
from app.ingestion.graph_backfill import document_extractions, eligible_status
from app.ingestion.graph_vectors import GraphVectorResult
from app.ingestion.stages import (
    IngestDependencies,
    graph_stage,
    set_ingest_dependencies,
)

from .test_downstream_stages import (
    _TENANT,
    _chunked_document,
    _FixedExtractor,
    _handler,
    _RecordingGraphIndexer,
    _RecordingProjector,
    _seed_tenant,
)

pytestmark = pytest.mark.asyncio


async def _mark_succeeded(document_id: int) -> None:
    """Put the document into the state a finished ingest leaves it in.

    The backfill deliberately replays only ``SUCCEEDED`` documents: a ``PENDING`` one has
    no extraction and a ``FAILED`` one has an extraction the platform never accepted, and
    projecting either would put entities into the graph for a document the product does not
    consider ingested.
    """
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        await session.execute(
            text("UPDATE documents SET status = :status WHERE id = :id"),
            {"status": eligible_status(), "id": document_id},
        )
        await session.commit()


async def test_the_backfill_reconstructs_exactly_what_the_stage_wrote(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """What ``chunks.meta`` replays is what the ingest handed the two graph writers.

    Asserted against the stage's own call log rather than against a fixture, because a
    fixture would only prove the backfill is self-consistent. The four things compared are
    the four the graph arm's correctness rests on: the entity list (its labels become node
    ids and vector ``entity_name``s), the relation list, the per-entity **chunk ids** (the
    node's ``source_id``, and the only route from a matched entity to a passage), and the
    extractor's name (embedded in every entity vector, so a corpus replayed under the wrong
    one indexes points a re-ingest would never produce).

    Neo4j could not have answered this. It holds the projection *after* merging, with no
    extractor ids on the entities and no record of which chunk anything came from — so the
    relations could not be re-attached and ``source_id`` could not be rebuilt at all. That
    is why the source is the chunk rows.
    """
    projector = _RecordingProjector()
    set_ingest_dependencies(
        IngestDependencies(
            store=store,
            extractor=_FixedExtractor(),
            project=projector,
            index_graph=_RecordingGraphIndexer(GraphVectorResult(entities=2, relations=1)),
        )
    )
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)
    await _handler(db, graph_stage, document_id, "graph")
    await _mark_succeeded(document_id)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        extractions, unextracted = await document_extractions(
            session, tenant_id=_TENANT
        )

    assert unextracted == [], "a document with an extraction was reported as having none"
    assert len(extractions) == 1
    replayed = extractions[0]
    stage_call = projector.calls[0]

    assert replayed.document_id == document_id
    assert replayed.tenant_id == _TENANT
    assert replayed.source == stage_call["source"]
    assert replayed.extractor == stage_call["extractor"]
    assert [(e.id, e.label, e.kind) for e in replayed.entities] == [
        (e.id, e.label, e.kind) for e in stage_call["entities"]
    ]
    assert [(r.src_id, r.tgt_id, r.phrase) for r in replayed.relations] == [
        (r.src_id, r.tgt_id, r.phrase) for r in stage_call["relations"]
    ]
    # The load-bearing one. These strings are chunk ids the ``index`` stage published the
    # passages under; a backfill that rebuilt them any other way would write a node whose
    # source_id names rows that are not in the chunk KV, and the entity would resolve to no
    # passage — which is the state this whole feature exists to leave.
    assert replayed.entity_sources == stage_call["entity_sources"]
    assert replayed.relation_sources == stage_call["relation_sources"]


async def test_a_document_the_graph_stage_never_ran_is_named_not_skipped(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """An eligible document with no extraction on its rows is reported, not rounded away.

    The same rule ``unembedded`` follows in the dense rebuild. A backfill that quietly
    covers 12 of 17 documents reads exactly like one that covers all 17, and the operator's
    only signal that the graph arm is still blind for five of them would be an answer that
    did not cite them.
    """
    set_ingest_dependencies(IngestDependencies(store=store))
    await _seed_tenant()
    document_id = await _chunked_document(client, parsed_artifact, store)
    await _mark_succeeded(document_id)

    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT)
        extractions, unextracted = await document_extractions(session)

    assert extractions == []
    assert unextracted == [document_id]
