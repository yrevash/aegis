"""The lexical arm, against a real PostgreSQL: corpus-wide, and it cannot cross tenants.

`LightRAGBackend` implemented `recall_ranked` and **not** `keyword_recall`, so in full
mode the pipeline took its pool-scoped branch: BM25 over the ~20 candidates the dense
arms had already returned, with IDF computed over those 20 "documents". That reorders a
pool. It cannot surface anything dense retrieval missed, which is the entire reason to
have a lexical arm — and the query it loses is the one a jury is most likely to ask,
because an exact identifier (a clause number, a case number, a part number) is what dense
embeddings are worst at.

The load-bearing test here is
:func:`test_the_keyword_arm_finds_a_passage_the_dense_arms_never_returned`: it fixes a
dense pool that does not contain the answer, proves the pool-scoped fallback cannot
return it (the control), and then finds it anyway through the corpus-wide arm. Without
that pair, "the keyword arm returned the right chunk" is equally true of the broken
implementation.

Everything runs on the suite's real PostgreSQL over the ``NOSUPERUSER NOBYPASSRLS`` role
(``tests/conftest.py``), so the ``tenant_isolation`` policy on ``chunks`` is genuinely
enforced against these connections. The isolation proof is deliberately made **twice**,
because the arm has two independent boundaries and a test that cannot tell them apart is
a test that will keep passing when one of them breaks:

* over the unprivileged role — the deployed shape, app predicate *and* RLS;
* over the owning (RLS-bypassing) role — which isolates the ``WHERE tenant_id`` predicate
  and is the assertion that fails if that predicate is ever dropped.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.lightrag_backend import LightRAGBackend
from aegis.retrieval.models import Candidate, Recall
from aegis.retrieval.pipeline import RetrievalConfig, Retriever, bm25_ranked
from aegis.retrieval.protocols import KeywordBackend
from aegis.retrieval.types import (
    RetrievalOrigin,
    RetrievalScope,
    UnresolvedTenantScopeError,
)

from .conftest import FakeBackend, FakeRedis, RecordingComplete, SequenceEmbed

#: Two tenants far outside the ranges the rest of the suite seeds, so a stray row from
#: elsewhere in the template database cannot be mistaken for one of these.
_TENANT_A = 70701
_TENANT_B = 70702

#: The query the whole file is about — an exact identifier, spelled the way a person
#: asks it rather than the way an index stores it.
_CLAUSE_QUERY = "what does clause 7.3.2 say about refunds?"

#: Tenant A's corpus. ``clause`` is the *answer*; the two decoys exist so ranking has
#: something to be wrong about, and so the dense pool below can be a real subset of the
#: corpus rather than a corpus of one.
_ANSWER = "Clause 7.3.2 caps the refund window at ninety days from delivery."
_DECOY_CLAUSE = (
    "This clause and every other clause in the appendix is superseded by the clause "
    "list held in the annex; each clause is renumbered there."
)
_DECOY_DENSE = "Shipping is dispatched within two working days of a cleared payment."
_DECOY_ANOTHER = "Warranty repairs are carried out at the regional service centre."

#: Tenant B's passage. It quotes the same clause — a lexical search has to have something
#: to match on each side of the boundary, or "tenant A saw only its own row" would be a
#: statement about the corpus rather than about the policy. The token appears nowhere
#: else in this repository, so seeing it under tenant A is unambiguous proof of a leak.
_TENANT_B_ANSWER = "Clause 7.3.2 caps ELDERFLOWER-BASILISK-4417 liability at one year."


async def _seed(owner_engine, sessionmaker) -> dict[str, int]:
    """Give both tenants a document and put the passages above into ``chunks``.

    Written over the **owner** connection: seeding through the policy would make the
    fixture depend on the very thing under test.

    Args:
        owner_engine: The table owner's engine (bypasses RLS on a stock cluster).
        sessionmaker: The unprivileged session factory, used only to create the parent
            ``tenants`` rows through the suite's shared helper.

    Returns:
        ``{passage text: chunk id}`` for every seeded chunk.
    """
    from .._seed import ensure_tenants

    await ensure_tenants(sessionmaker, _TENANT_A, _TENANT_B)

    corpus: list[tuple[int, str]] = [
        (_TENANT_A, _ANSWER),
        (_TENANT_A, _DECOY_CLAUSE),
        (_TENANT_A, _DECOY_DENSE),
        (_TENANT_A, _DECOY_ANOTHER),
        (_TENANT_B, _TENANT_B_ANSWER),
    ]
    ids: dict[str, int] = {}
    async with owner_engine.begin() as conn:
        documents = {}
        for tenant_id in (_TENANT_A, _TENANT_B):
            documents[tenant_id] = (
                await conn.execute(
                    text(
                        "INSERT INTO documents "
                        "(tenant_id, filename, content_sha256, mime_type, size_bytes, "
                        " status) "
                        "VALUES (:tenant, :filename, :sha, 'application/pdf', 1024, "
                        " 'SUCCEEDED') RETURNING id"
                    ),
                    {
                        "tenant": tenant_id,
                        "filename": f"terms-t{tenant_id}.pdf",
                        "sha": f"{tenant_id:064d}",
                    },
                )
            ).scalar_one()
        for tenant_id, content in corpus:
            ids[content] = (
                await conn.execute(
                    text(
                        "INSERT INTO chunks "
                        "(tenant_id, document_id, content, embedding, meta) "
                        "VALUES (:tenant, :document, :content, '[]'::jsonb, '{}'::jsonb) "
                        "RETURNING id"
                    ),
                    {
                        "tenant": tenant_id,
                        "document": documents[tenant_id],
                        "content": content,
                    },
                )
            ).scalar_one()
    return ids


def _backend(sessionmaker) -> LightRAGBackend:
    """Build the real backend with its lexical arm pointed at a live database.

    Only the lexical arm is exercised here, and it is the one part of this class that
    talks to PostgreSQL rather than to LightRAG — so no LightRAG instance is needed and
    none is injected. ``complete``/``embed`` are the suite's fakes because entity
    extraction and embedding belong to the arms this file is not testing.
    """
    return LightRAGBackend(
        complete=RecordingComplete("{}"),
        embed=SequenceEmbed([1.0, 0.0]),
        session_factory=sessionmaker,
    )


async def test_the_backend_now_satisfies_the_keyword_protocol(pg_sessionmaker) -> None:
    """The pipeline switches arms on this ``isinstance``, so it is asserted directly.

    ``Retriever._keyword_signal`` chooses between a real recall arm and a labelled
    re-ranking pass by testing the backend against
    :class:`~aegis.retrieval.protocols.KeywordBackend`. Before this change
    ``LightRAGBackend`` failed that test and the production path silently took the weaker
    branch — silently, because the branch it took is a legitimate one for a backend that
    genuinely cannot search its corpus.
    """
    assert isinstance(_backend(pg_sessionmaker), KeywordBackend)


async def test_the_keyword_arm_finds_a_passage_the_dense_arms_never_returned(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """The proof that this arm is corpus-wide and not pool-scoped.

    The dense pool is fixed to three passages that do **not** include the answer — the
    situation the whole arm exists for, and the one dense retrieval really is worst at,
    because "clause 7.3.2" carries almost no semantic signal to embed.

    The control comes first: the pool-scoped fallback (:func:`bm25_ranked` over exactly
    that pool, which is what the pipeline did before this change) is asserted **not** to
    return the answer. It cannot — the answer is not in the pool. Only then does the
    corpus-wide arm run, over the same query and the same tenant, and find it.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)
    scope = RetrievalScope(tenant_id=_TENANT_A)
    dense_pool = [
        Candidate(id="d0", text=_DECOY_CLAUSE),
        Candidate(id="d1", text=_DECOY_DENSE),
        Candidate(id="d2", text=_DECOY_ANOTHER),
    ]

    pool_scoped = bm25_ranked(_CLAUSE_QUERY, dense_pool)
    assert _ANSWER not in [c.text for c in pool_scoped], (
        "the answer leaked into the dense pool, so the corpus-wide assertion below "
        "would be satisfied by the pool-scoped implementation this task replaced"
    )

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _CLAUSE_QUERY, top_k=10, scope=scope
    )

    assert _ANSWER in [c.text for c in hits], (
        "the keyword arm did not surface a passage the dense arms never returned — it "
        f"is still scoring a pool rather than the corpus: {[c.text for c in hits]}"
    )


async def test_an_exact_identifier_ranks_its_own_chunk_first(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """"clause 7.3.2" must beat a passage that says "clause" five times and nothing else.

    Ranking, not merely matching: the decoy shares the query's common term and the answer
    shares its rare one, so a ranker that only counted term hits would put the decoy on
    top. This is the query D5 names as the reason the arm exists, asserted as an ordering
    rather than as a membership.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _CLAUSE_QUERY, top_k=10, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    assert hits, "the exact-identifier query matched nothing at all"
    assert hits[0].text == _ANSWER, (
        "the passage carrying the exact identifier did not rank first: "
        f"{[c.text for c in hits]}"
    )
    assert hits[0].metadata["file_path"] == f"terms-t{_TENANT_A}.pdf", (
        "the hit carries no usable provenance, so it cannot be cited"
    )
    assert hits[0].metadata["ts_rank"] > 0.0


async def test_a_lexical_hit_cannot_cross_tenants(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """Both tenants quote clause 7.3.2; neither may read the other's passage.

    Run over the suite's ``NOSUPERUSER NOBYPASSRLS`` role, so this is the deployed shape:
    the query's tenant predicate and the ``tenant_isolation`` policy both apply. Asserted
    on **content** rather than on counts — "each tenant saw one row" is also what a
    policy returning the *wrong* row produces.

    The non-vacuity check reads both passages over the owner connection first, so the
    query is known to match on both sides of the boundary before the scoped reads mean
    anything.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)
    async with pg_owner_engine.connect() as conn:
        both = (
            await conn.execute(
                text(
                    "SELECT content FROM chunks WHERE search_vector @@ "
                    "replace(plainto_tsquery('english', :q)::text, '&', '|')::tsquery "
                    "ORDER BY content"
                ),
                {"q": _CLAUSE_QUERY},
            )
        ).scalars().all()
    assert _ANSWER in both and _TENANT_B_ANSWER in both, (
        "the full-text query does not match both tenants' passages, so a scoped read "
        f"returning one of them would prove nothing: {both}"
    )

    backend = _backend(pg_sessionmaker)
    for tenant_id, own, foreign in (
        (_TENANT_A, _ANSWER, _TENANT_B_ANSWER),
        (_TENANT_B, _TENANT_B_ANSWER, _ANSWER),
    ):
        hits = await backend.keyword_recall(
            _CLAUSE_QUERY, top_k=50, scope=RetrievalScope(tenant_id=tenant_id)
        )
        texts = [c.text for c in hits]
        assert own in texts, f"tenant {tenant_id} cannot read its own passage: {texts}"
        assert foreign not in texts, (
            f"the keyword arm returned another tenant's passage to tenant {tenant_id}: "
            f"{texts}"
        )
        assert {c.metadata["tenant_id"] for c in hits} == {f"t{tenant_id}"}


async def test_the_tenant_predicate_holds_without_row_level_security(
    pg_owner_engine,
) -> None:
    """The ``WHERE tenant_id`` clause is load-bearing on its own — proved by removing RLS.

    The test above runs over a role the policy applies to, so it would keep passing if
    the query's tenant predicate were deleted: the database would filter the rows the
    query forgot to. This one runs the identical arm over the **owning** role, which on a
    stock cluster is a superuser and therefore bypasses row security entirely. Nothing but
    the ``WHERE`` clause stands between this query and the other tenant's passage.

    That is not a hypothetical shape either. A host that points the retrieval path at an
    admin DSN — which is exactly what this platform did before the serving/owner split —
    gets this configuration, and it must still not leak.
    """
    sessionmaker = async_sessionmaker(pg_owner_engine, expire_on_commit=False)
    await _seed(pg_owner_engine, sessionmaker)

    async with pg_owner_engine.connect() as conn:
        privileged = (
            await conn.execute(text("SELECT rolsuper OR rolbypassrls FROM pg_roles "
                                    "WHERE rolname = current_user"))
        ).scalar_one()
    assert privileged, (
        "the owning role is subject to row security, so this test is just a second copy "
        "of the one above and proves nothing about the query's own predicate"
    )

    hits = await _backend(sessionmaker).keyword_recall(
        _CLAUSE_QUERY, top_k=50, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    texts = [c.text for c in hits]
    assert _ANSWER in texts, f"tenant A cannot read its own passage: {texts}"
    assert _TENANT_B_ANSWER not in texts, (
        "with row security bypassed the query returned tenant B's passage — the arm's "
        f"own tenant predicate is missing or wrong: {texts}"
    )


async def test_an_unscoped_request_reads_no_tenants_rows(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """A null tenant is not a wildcard, and here it is not even a corpus.

    ``chunks.tenant_id`` is ``NOT NULL``, so the shared, tenant-less corpus owns no rows
    in this table at all: an unscoped request has nothing to read and gets nothing.
    Stating it as a test rather than as a comment matters because the *other* obvious
    reading of "no tenant" — search everything — is the leak this whole layer exists to
    prevent, and it is one dropped predicate away.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)

    hits = await _backend(pg_sessionmaker).keyword_recall(
        _CLAUSE_QUERY, top_k=50, scope=RetrievalScope(tenant_id=None)
    )

    assert hits == []


async def test_an_unresolvable_scope_raises_rather_than_searching(
    pg_sessionmaker,
) -> None:
    """A tenant id that lost its type is a defect, not an empty result.

    ``RetrievalScope(tenant_id="70701")`` is what a scope looks like when it arrived from
    a header or a half-built request object. Coercing it would search a real tenant's
    corpus; returning ``[]`` would hide the bug behind an honest-looking answer. Neither
    is acceptable, so the arm routes through ``resolved_tenant_id`` like every other one.
    """
    with pytest.raises(UnresolvedTenantScopeError):
        await _backend(pg_sessionmaker).keyword_recall(
            _CLAUSE_QUERY, top_k=5, scope=RetrievalScope(tenant_id=str(_TENANT_A))
        )


async def test_a_query_with_no_searchable_terms_matches_nothing(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """An empty ``tsquery`` returns no rows — never the whole corpus, weakly.

    Every term in this query is a stop word, so ``plainto_tsquery`` produces nothing to
    match with. The failure mode being excluded is a predicate that degenerates to "true"
    and hands the reranker the tenant's entire corpus while the arm reports a clean run.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)

    hits = await _backend(pg_sessionmaker).keyword_recall(
        "and then the of it", top_k=50, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    assert hits == []


async def test_the_pipeline_reports_a_corpus_wide_arm_and_fuses_what_it_found(
    pg_owner_engine, pg_sessionmaker
) -> None:
    """End to end: the passage reaches the answer, and provenance says which arm found it.

    The dense backend is fixed to a recall that does not contain the answer, so every
    appearance of it downstream is attributable to the lexical arm. Two things are then
    asserted together, because either alone is satisfiable by the broken implementation:
    the answer is among the sources, **and** ``observability.keyword`` reports
    ``scope="corpus"`` with ``adds_recall=True`` — the honest label the pipeline refuses
    to apply to a pool-scoped pass.
    """
    await _seed(pg_owner_engine, pg_sessionmaker)
    dense = Recall(
        candidates=[
            Candidate(id="d0", text=_DECOY_DENSE),
            Candidate(id="d1", text=_DECOY_ANOTHER),
        ]
    )

    class _KeywordOverDense(FakeBackend):
        """The canned dense/graph recall, with the real Postgres lexical arm beside it."""

        def __init__(self, keyword_backend: LightRAGBackend) -> None:
            super().__init__(dense)
            self._keyword = keyword_backend

        async def keyword_recall(self, query, *, top_k, scope):
            return await self._keyword.keyword_recall(query, top_k=top_k, scope=scope)

    retriever = Retriever(
        backend=_KeywordOverDense(_backend(pg_sessionmaker)),
        cache=SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.95),
        complete=RecordingComplete("{}"),
        embed=SequenceEmbed([1.0, 0.0]),
        # Rerank off so the assertion is about what recall produced, not about what a
        # canned model response reordered.
        config=RetrievalConfig(recall_top_k=10, final_top_k=6, rerank_enabled=False),
    )

    result = await retriever.retrieve(
        _CLAUSE_QUERY, scope=RetrievalScope(tenant_id=_TENANT_A)
    )

    assert _ANSWER in [s.text for s in result.sources], (
        "the answer never reached the assembled sources, so the lexical arm's hit was "
        f"dropped between recall and assembly: {[s.text for s in result.sources]}"
    )
    keyword = result.observability.keyword
    assert (keyword.ran, keyword.scope, keyword.adds_recall) == (True, "corpus", True)
    assert RetrievalOrigin.BM25 in result.provenance.origins
