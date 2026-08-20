"""One passage is one source, whichever arm recalled it.

RRF merges candidates by ``Candidate.id`` and by nothing else. So two arms that recall
the same passage under two different ids do not fuse — they survive as two entries with
the same text and the same score, and the console shows the same citation twice, which
halves the apparent variety of a run's evidence while doubling its length.

That is what was happening, and it was measured on a live ``POST /v1/query`` rather than
inferred: six ``scored_sources`` that were three passages, each once as
``t1:7c7d81de42767c69`` and once as ``"42"``. The first spelling is what
``app.ingestion.stages.index_stage`` publishes into the dense index; the second was
``chunks.id``, which ``_keyword_candidate`` used to hand back raw.

The fix is that both spellings now come out of one function,
:func:`aegis.retrieval.types.chunk_source_id`. These tests are about the *consequence* of
that rather than about the function: the arms are run for real against PostgreSQL, the
real fusion is applied, and the assertion is on how many sources come out.

Each fusion assertion is paired with a **control** that reconstructs the old id and shows
the same fusion producing two. Without it, "one candidate came out" would also be true of
a fusion that had simply lost an arm.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from aegis.retrieval.fusion import ORIGIN_METADATA_KEY, RankedList, reciprocal_rank_fusion
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.models import Candidate
from aegis.retrieval.types import RetrievalOrigin, RetrievalScope, chunk_source_id

from .conftest import RecordingComplete, SequenceEmbed

#: A tenant far outside the ranges the rest of the suite seeds.
_TENANT = 70801

#: The passage both arms will recall, and the query that finds it.
_PASSAGE = "Clause 9.4.1 sets the ULTRAMARINE-QUOKKA-8823 escalation window at 48 hours."
_QUERY = "what is the ULTRAMARINE-QUOKKA-8823 escalation window?"

#: The chunk's content hash, as ``app.ingestion.stages`` writes it into ``chunks.meta``
#: under ``content_id`` — sixteen hex characters of
#: :meth:`aegis.retrieval.chunker.ChunkPiece.indexed_id`. The exact value is arbitrary;
#: that it is *stored on the row* is not, because it is the only thing that lets the
#: lexical arm name a chunk the way the dense index already does.
_CONTENT_ID = "b17c4d90fe225a3e"


async def _seed(owner_engine, sessionmaker) -> int:
    """Insert one document and one chunk carrying a real ``content_id``.

    Written over the **owner** connection so the fixture does not depend on the policy
    that other tests in this package exist to prove.

    Returns:
        The ``chunks.id`` primary key — the id the lexical arm used to return.
    """
    from .._seed import ensure_tenants

    await ensure_tenants(sessionmaker, _TENANT)

    async with owner_engine.begin() as conn:
        document_id = (
            await conn.execute(
                text(
                    "INSERT INTO documents "
                    "(tenant_id, filename, content_sha256, mime_type, size_bytes, status) "
                    "VALUES (:tenant, 'escalation-policy.pdf', :sha, 'application/pdf', "
                    "        2048, 'SUCCEEDED') RETURNING id"
                ),
                {"tenant": _TENANT, "sha": f"{_TENANT:064d}"},
            )
        ).scalar_one()
        return (
            await conn.execute(
                text(
                    "INSERT INTO chunks "
                    "(tenant_id, document_id, content, embedding, meta) "
                    "VALUES (:tenant, :document, :content, '[]'::jsonb, "
                    "        CAST(:meta AS jsonb)) RETURNING id"
                ),
                {
                    "tenant": _TENANT,
                    "document": document_id,
                    "content": _PASSAGE,
                    "meta": (
                        '{"content_id": "' + _CONTENT_ID + '", '
                        '"source": "escalation-policy.pdf", "ordinal": 3}'
                    ),
                },
            )
        ).scalar_one()


def _backend(sessionmaker) -> LightRAGBackend:
    """The real backend, with only its lexical arm pointed at a live database."""
    return LightRAGBackend(
        complete=RecordingComplete("{}"),
        embed=SequenceEmbed([1.0, 0.0]),
        session_factory=sessionmaker,
    )


def _dense_candidate() -> Candidate:
    """The same passage as the dense arm returns it.

    The id is built the way ``index_stage`` published it — through
    :func:`chunk_source_id` — because that is what the dense index actually holds. Using
    a literal here instead would let this test agree with a fusion that had drifted away
    from the index.
    """
    return Candidate(
        id=chunk_source_id(_TENANT, _CONTENT_ID),
        text=_PASSAGE,
        metadata={"file_path": "escalation-policy.pdf", "tenant_id": f"t{_TENANT}"},
    )


async def test_the_lexical_arm_names_a_chunk_the_way_the_index_does(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """The identity itself, read off a real row through the real arm.

    Asserted against ``chunk_source_id`` *and* against the literal shape, because the
    first alone would pass if both the writer and this reader were changed together into
    something the dense index does not hold.
    """
    row_id = await _seed(pg_owner_engine, pg_sessionmaker)

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _QUERY, top_k=10, scope=RetrievalScope(tenant_id=_TENANT)
    )

    assert hits, "the lexical arm matched nothing, so there is no id to check"
    assert hits[0].id == chunk_source_id(_TENANT, _CONTENT_ID)
    assert hits[0].id == f"t{_TENANT}:{_CONTENT_ID}"
    assert hits[0].id != str(row_id), "the arm is still returning the raw primary key"
    assert hits[0].metadata["chunk_id"] == row_id, (
        "the row's own key is the join back to the database and must not be lost in "
        "the course of fixing the id"
    )


async def test_a_passage_both_arms_recall_fuses_into_one_source(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """The defect, and its control, on the real fusion.

    The control runs first and reconstructs the pre-fix lexical id — the bare
    ``chunks.id`` — so the same passage, the same fusion and the same two arms are shown
    producing **two** sources. Only then is the real arm fused, and only then does one
    source mean the ids agree rather than an arm having gone missing.
    """
    row_id = await _seed(pg_owner_engine, pg_sessionmaker)
    dense = RankedList(origins=(RetrievalOrigin.VECTOR,), candidates=[_dense_candidate()])

    lexical_before = RankedList(
        origins=(RetrievalOrigin.BM25,),
        candidates=[Candidate(id=str(row_id), text=_PASSAGE)],
    )
    duplicated = reciprocal_rank_fusion([dense, lexical_before])
    assert len(duplicated) == 2, (
        "the control did not reproduce the duplicate, so one source below would prove "
        "nothing about the ids"
    )
    assert {c.text for c in duplicated} == {_PASSAGE}, (
        "the control's two entries are not the same passage twice"
    )

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _QUERY, top_k=10, scope=RetrievalScope(tenant_id=_TENANT)
    )
    lexical = RankedList(origins=(RetrievalOrigin.BM25,), candidates=hits)

    fused = reciprocal_rank_fusion([dense, lexical])

    assert len(fused) == 1, (
        f"one passage still reaches the console as {len(fused)} sources: "
        f"{[c.id for c in fused]}"
    )
    assert fused[0].id == chunk_source_id(_TENANT, _CONTENT_ID)
    assert fused[0].metadata[ORIGIN_METADATA_KEY] == ["vector", "bm25"], (
        "the two arms did not merge into one candidate — they were deduplicated by "
        "something other than RRF, which would drop the second arm's evidence"
    )
    assert fused[0].score == pytest.approx(2 / 61), (
        "a fused candidate accumulates one 1/(k+rank) term per arm; a single term here "
        "would mean only one arm reached the fusion at all"
    )


async def test_a_row_whose_meta_predates_content_ids_is_still_named_honestly(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """The documented limit of the fix, asserted rather than left implied.

    A legacy chunk whose ``meta`` carries no ``content_id`` falls back to its primary
    key. That id is stable and tenant-prefixed, so it is honest — but it is *not* what
    the dense index holds for that passage, so such a row would still show twice. Pinning
    it here means the day someone backfills ``content_id`` this test says so.
    """
    async with pg_owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE chunks SET meta = '{}'::jsonb WHERE tenant_id = :tenant"),
            {"tenant": _TENANT},
        )
    row_id = await _seed(pg_owner_engine, pg_sessionmaker)
    async with pg_owner_engine.begin() as conn:
        await conn.execute(
            text("UPDATE chunks SET meta = '{}'::jsonb WHERE id = :id"), {"id": row_id}
        )

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _QUERY, top_k=10, scope=RetrievalScope(tenant_id=_TENANT)
    )

    assert hits[0].id == f"t{_TENANT}:{row_id}"
    assert hits[0].id == chunk_source_id(_TENANT, row_id)
