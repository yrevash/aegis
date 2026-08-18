"""An ingest bumps the tenant's corpus version, and only that tenant's (task 4.8).

The failure this file exists to prevent is the one that looks exactly like the ingest not
working: a tenant uploads a document, asks the question it answers, and is served the
answer that was cached *before* the upload. Both caches in front of the agent hold entries
for up to an hour and neither can know the corpus moved underneath them, so the only thing
standing between that tenant and a wrong answer is the corpus version folded into the
cache key — and the only thing that moves the version is the ingest itself.

So this is an end-to-end test rather than a unit one. A real upload through the real route
into a real PostgreSQL, the real stage handlers, the real close-out activity, and then the
shipped :class:`~aegis.retrieval.cache.SemanticCache` asked for the entry that was cached
first. Only the three collaborators that would cost money or need a running vector store
are injected.

Two tenants throughout, and every assertion is made about both. "The bump is per tenant"
is a claim about the *neighbour*, and a test that only looks at the tenant who uploaded
would pass just as happily if the bump were global — which would quietly throw away the
cache of every other tenant on the platform on every upload.
"""

from __future__ import annotations

import pgsupport
import pytest
from aegis.governance.models import Budget, BudgetScope, BudgetWindow
from aegis.jobs import Document, JobStatus
from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.corpus import corpus_version, reset_corpus_versions
from aegis.retrieval.graph_extract import Entity, Relation
from aegis.retrieval.models import RetrievalResult, Source
from aegis.retrieval.types import RetrievalScope
from sqlalchemy import select
from tests.retrieval.conftest import FakeRedis

from app.api.schemas import Role
from app.core.security import TENANT_ADMIN, create_access_token
from app.data import Tenant, User, get_sessionmaker, set_tenant_scope
from app.ingestion.stages import IngestDependencies, set_ingest_dependencies
from app.jobs.activities import finish_ingest, run_stage
from app.jobs.flows.contracts import FinishInput, StageInput

from .conftest import FIXTURE

pytestmark = pytest.mark.asyncio

_TENANT_A = 1
_TENANT_B = 2
_USER_A = 11
_USER_B = 22

#: The stages an ingest runs over an already-parsed document. ``parse`` is excluded here
#: only because the fixture's parse artifact is seeded directly — the point of this file
#: is what happens *after* the corpus changes, not how it was read.
_STAGES = ("chunk", "enrich", "embed", "index", "graph")


@pytest.fixture(autouse=True)
def _clean_corpus_versions():
    """Keep the process-wide counters out of neighbouring tests, and out of each other."""
    reset_corpus_versions()
    yield
    reset_corpus_versions()


class _StubExtractor:
    """A graph extractor with no spaCy and no model behind it."""

    name = "stub-extractor"

    async def extract(self, chunk_text: str) -> tuple[list[Entity], list[Relation]]:
        """Return one entity per chunk and no relations."""
        return ([Entity.make("Transformer", "product")], [])


async def _embed(texts: list[str]) -> list[list[float]]:
    """A deterministic embedder — same text, same vector, no provider."""
    return [[float(len(text)), float(len(text) % 7)] for text in texts]


async def _publish(chunks) -> None:  # noqa: ANN001 - Sequence[RetrievalChunk]
    """Accept a publish without a vector store behind it."""
    return None


def _headers(*, tenant_id: int, username: str, user_id: int) -> dict[str, str]:
    """A tenant-admin bearer for one tenant."""
    token = create_access_token(
        user_id=user_id, username=username, role=TENANT_ADMIN, tenant_id=tenant_id
    )
    return {"Authorization": f"Bearer {token}"}


async def _seed_tenants() -> None:
    """Two tenants with an admin each, and a budget generous enough to admit an ingest."""
    async with get_sessionmaker()() as session:
        await pgsupport.seed(
            session,
            Tenant(id=_TENANT_A, name="Tenant A"),
            Tenant(id=_TENANT_B, name="Tenant B"),
            User(id=_USER_A, username="a-admin", role=Role.ADMIN, tenant_id=_TENANT_A),
            User(id=_USER_B, username="b-admin", role=Role.ADMIN, tenant_id=_TENANT_B),
            Budget(
                tenant_id=_TENANT_A,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_A,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
            Budget(
                tenant_id=_TENANT_B,
                scope_type=BudgetScope.TENANT,
                scope_id=_TENANT_B,
                window=BudgetWindow.DAY,
                usd_cap=100.0,
            ),
        )
        await session.commit()


async def _ingest(
    client, artifact, store, *, tenant_id: int, username: str, user_id: int
) -> int:
    """Upload the fixture and run its ingest to a successful close-out.

    Args:
        client: The ASGI client.
        artifact: ``(bytes, artifact_json)`` from the session-scoped parse.
        store: The temporary document store the handlers were given.
        tenant_id: The uploading tenant.
        username: That tenant's admin.
        user_id: That admin's id.

    Returns:
        The document id, ingested and closed out exactly as the workflow would.
    """
    data, payload = artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(tenant_id=tenant_id, username=username, user_id=user_id),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    document_id = body["document_id"]
    workflow_id = f"ingest:{tenant_id}:{document_id}"
    store.put_artifact(
        tenant_id=tenant_id, sha256=body["content_sha256"], payload=payload
    )
    for stage in _STAGES:
        await run_stage(
            StageInput(
                tenant_id=tenant_id,
                workflow_id=workflow_id,
                document_id=document_id,
                stage=stage,
            )
        )
    await finish_ingest(
        FinishInput(
            tenant_id=tenant_id,
            workflow_id=workflow_id,
            document_id=document_id,
            status=JobStatus.SUCCEEDED.value,
        )
    )
    return document_id


def _cache() -> SemanticCache:
    """The shipped cache over an in-memory Redis stand-in."""
    return SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=0.99)


def _answer(text: str) -> RetrievalResult:
    """A cacheable result carrying a recognisable answer."""
    return RetrievalResult(answer_context=text, sources=[Source(id="s", text=text)])


def _scope(tenant_id: int) -> RetrievalScope:
    """The retrieval scope a request for ``tenant_id`` builds *right now*.

    Built from :func:`aegis.retrieval.corpus.corpus_version` rather than from a literal,
    because that is exactly what ``aegis.agent.graph`` does per request — a literal here
    would be testing this file's arithmetic instead of the seam.
    """
    return RetrievalScope(tenant_id=tenant_id, corpus_version=corpus_version(tenant_id))


async def _dependencies(store) -> None:
    """Install the three collaborators that would otherwise need money or a vector store."""
    set_ingest_dependencies(
        IngestDependencies(
            store=store, embed=_embed, publish=_publish, extractor=_StubExtractor()
        )
    )


# ─────────────────────────────────────────────────────────────────────────────


async def test_an_ingest_bumps_the_corpus_version_and_unseats_the_cached_answer(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The pre-upload answer becomes unreachable the moment the ingest closes out.

    The cache is written *before* the ingest, under the scope a request would have built
    then, and read *after* it under the scope a request builds now. Nothing evicts, scans
    or expires: the post-ingest key simply is not the pre-ingest key, which is why this
    holds with a cache that has no idea an ingest exists.
    """
    await _seed_tenants()
    await _dependencies(store)
    cache = _cache()

    before = _scope(_TENANT_A)
    assert before.corpus_version == 0
    await cache.set("what is multi-head attention?", before, [1.0, 0.0], _answer("stale"))
    assert await cache.get_exact("what is multi-head attention?", before) is not None

    document_id = await _ingest(
        client,
        parsed_artifact,
        store,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
    )

    after = _scope(_TENANT_A)
    assert after.corpus_version == 1, (
        "the ingest did not move the tenant's corpus version, so every answer cached "
        "before the upload is still being served over a corpus that now contains it"
    )
    assert await cache.get_exact("what is multi-head attention?", after) is None, (
        "the pre-upload answer is still reachable after the ingest"
    )
    # And the bump stands for a document that really did land in the corpus.
    async with get_sessionmaker()() as session:
        await set_tenant_scope(session, _TENANT_A)
        document = (
            await session.execute(select(Document).where(Document.id == document_id))
        ).scalar_one()
    assert document.status is JobStatus.SUCCEEDED
    assert document.chunk_count and document.chunk_count > 0


async def test_one_tenants_ingest_leaves_the_other_tenants_version_and_cache_alone(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """The invalidation is per tenant, and the neighbour is the half that proves it.

    A global counter would satisfy every assertion about tenant A above while throwing
    away tenant B's cache on every upload anybody makes — a cost that is invisible in
    correctness terms and enormous in latency and spend terms.
    """
    await _seed_tenants()
    await _dependencies(store)
    cache = _cache()

    b_scope = _scope(_TENANT_B)
    await cache.set("what is multi-head attention?", b_scope, [1.0, 0.0], _answer("b answer"))

    await _ingest(
        client,
        parsed_artifact,
        store,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
    )

    assert corpus_version(_TENANT_A) == 1
    assert corpus_version(_TENANT_B) == 0, (
        "tenant A's ingest moved tenant B's corpus version, so one tenant's upload "
        "invalidates every other tenant's cache"
    )
    assert _scope(_TENANT_B) == b_scope
    hit = await cache.get_exact("what is multi-head attention?", b_scope)
    assert hit is not None and hit.answer_context == "b answer"

    # The neighbour's own ingest moves the neighbour's version, and still not A's.
    await _ingest(
        client,
        parsed_artifact,
        store,
        tenant_id=_TENANT_B,
        username="b-admin",
        user_id=_USER_B,
    )
    assert corpus_version(_TENANT_B) == 1
    assert corpus_version(_TENANT_A) == 1
    # Asked under the scope a request builds *now*, which is the only scope that matters:
    # the old entry is still in Redis, it is simply no longer reachable by any key the
    # system will construct again.
    assert await cache.get_exact("what is multi-head attention?", _scope(_TENANT_B)) is None


async def test_replaying_the_close_out_does_not_bump_a_second_time(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """A replayed ``finish_ingest`` is a no-op, and its bump has to be one too.

    The substrate replays activities — that is measured, not hypothetical — so the bump
    is guarded by the same ``WHERE status IS DISTINCT FROM`` that guards the status write.
    Without that guard every replay would discard the tenant's whole cache for nothing.
    """
    await _seed_tenants()
    await _dependencies(store)
    document_id = await _ingest(
        client,
        parsed_artifact,
        store,
        tenant_id=_TENANT_A,
        username="a-admin",
        user_id=_USER_A,
    )
    assert corpus_version(_TENANT_A) == 1

    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT_A,
            workflow_id=f"ingest:{_TENANT_A}:{document_id}",
            document_id=document_id,
            status=JobStatus.SUCCEEDED.value,
        )
    )

    assert corpus_version(_TENANT_A) == 1


async def test_a_run_that_never_reached_the_chunk_stage_does_not_bump(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """A parse that failed changed no chunk, so it changed nothing a cache could be stale about.

    The counterpart of the rule above: over-bumping costs a cache miss, but bumping for a
    run that wrote nothing would mean every failed upload throws the tenant's cache away.
    """
    await _seed_tenants()
    await _dependencies(store)
    data, _payload = parsed_artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )
    assert res.status_code == 200, res.text
    document_id = res.json()["document_id"]

    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT_A,
            workflow_id=f"ingest:{_TENANT_A}:{document_id}",
            document_id=document_id,
            status=JobStatus.FAILED.value,
            error="the parse stage could not read the bytes",
        )
    )

    assert corpus_version(_TENANT_A) == 0


async def test_a_failed_run_that_did_write_chunks_still_bumps(
    client, db, wired, store, temporal, parsed_artifact
) -> None:
    """Chunks written by a run that later failed are in the corpus and are searchable.

    They are matched by the keyword arm the moment ``chunk`` commits, whatever happens to
    the stages after it. Bumping only on success would leave exactly this case stale — and
    a stale cache reports nothing, which is why it is asserted rather than assumed.
    """
    await _seed_tenants()
    await _dependencies(store)
    data, payload = parsed_artifact
    res = await client.post(
        "/documents",
        files={"file": (FIXTURE, data, "application/pdf")},
        headers=_headers(tenant_id=_TENANT_A, username="a-admin", user_id=_USER_A),
    )
    assert res.status_code == 200, res.text
    body = res.json()
    document_id = body["document_id"]
    store.put_artifact(
        tenant_id=_TENANT_A, sha256=body["content_sha256"], payload=payload
    )
    await run_stage(
        StageInput(
            tenant_id=_TENANT_A,
            workflow_id=f"ingest:{_TENANT_A}:{document_id}",
            document_id=document_id,
            stage="chunk",
        )
    )

    await finish_ingest(
        FinishInput(
            tenant_id=_TENANT_A,
            workflow_id=f"ingest:{_TENANT_A}:{document_id}",
            document_id=document_id,
            status=JobStatus.FAILED.value,
            error="the embed stage exhausted its attempts",
        )
    )

    assert corpus_version(_TENANT_A) == 1
