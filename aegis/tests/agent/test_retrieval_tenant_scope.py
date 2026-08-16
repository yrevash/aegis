"""Graph-level proof that the request's tenant actually reaches retrieval.

This is the defect these tests exist for: the graph already resolved the tenant (it used
it for the answer-cache partition) and then called ``deps.retrieve`` without it, so the
isolation available one line above was dropped on the way into retrieval. The assertions
below are on the scope the retrieve node *passes*, not on plumbing shape, and they cover
every branch of that node — single-shot, rewrite-then-retrieve, and the agentic loop's
follow-up rounds — because the leak only needs one of them to forget.
"""

from __future__ import annotations

import pytest

from aegis.agent import run_agent
from aegis.retrieval.corpus import bump_corpus_version, reset_corpus_versions
from aegis.retrieval.types import RetrievalScope


@pytest.fixture(autouse=True)
def _clean_corpus_versions():
    """Keep the process-wide corpus-version counters out of neighbouring tests."""
    reset_corpus_versions()
    yield
    reset_corpus_versions()


def _record_scopes(deps) -> list[RetrievalScope]:
    """Wrap ``deps.retrieve`` so every scope it is called with is captured."""
    seen: list[RetrievalScope] = []
    inner = deps.retrieve

    async def recording_retrieve(query, *, scope):
        seen.append(scope)
        return await inner(query, scope=scope)

    deps.retrieve = recording_retrieve
    return seen


@pytest.mark.asyncio
async def test_retrieve_node_passes_the_requests_tenant(make_deps):
    deps = make_deps(propose_tool=False)
    deps.current_tenant_id = lambda: 42
    seen = _record_scopes(deps)

    async for _ in run_agent("what is the refund policy?", deps=deps):
        pass

    assert seen, "the retrieve node must have run"
    assert all(scope.tenant_id == 42 for scope in seen)


@pytest.mark.asyncio
async def test_scope_carries_the_persona_and_the_tenants_corpus_version(make_deps):
    deps = make_deps(propose_tool=False)
    deps.current_tenant_id = lambda: 42
    bump_corpus_version(42)  # tenant 42 ingested something
    bump_corpus_version(7)  # ...and so did an unrelated tenant
    seen = _record_scopes(deps)

    async for _ in run_agent(
        "what is the refund policy?", deps=deps, persona="operations_lead"
    ):
        pass

    assert seen[0].persona == "operations_lead"
    assert seen[0].corpus_version == 1, "the tenant's OWN corpus version, not a neighbour's"


@pytest.mark.asyncio
async def test_the_rewrite_branch_is_scoped_too(make_deps):
    """The retrieve node has three branches; a leak needs only one of them to forget.

    (The agentic loop's *follow-up* rounds are covered where they can actually be driven
    to iterate: ``tests/retrieval/test_agentic.py``.)
    """
    deps = make_deps(propose_tool=False)
    deps.config.agentic_retrieval_enabled = False
    deps.config.query_rewrite_enabled = True
    deps.current_tenant_id = lambda: 42
    seen = _record_scopes(deps)

    async for _ in run_agent("what is the refund policy?", deps=deps):
        pass

    assert seen and all(scope.tenant_id == 42 for scope in seen)


@pytest.mark.asyncio
async def test_the_single_shot_branch_is_scoped_too(make_deps):
    """Neither rewrite nor agentic loop: the plainest path still carries the tenant."""
    deps = make_deps(propose_tool=False)
    deps.config.agentic_retrieval_enabled = False
    deps.config.query_rewrite_enabled = False
    deps.current_tenant_id = lambda: 42
    seen = _record_scopes(deps)

    async for _ in run_agent("what is the refund policy?", deps=deps):
        pass

    assert seen and all(scope.tenant_id == 42 for scope in seen)


@pytest.mark.asyncio
async def test_an_ungoverned_run_is_scoped_to_no_tenant_not_to_someone_elses(make_deps):
    """The default tenant provider yields ``None`` — unscoped, which reads shared rows only."""
    deps = make_deps(propose_tool=False)
    seen = _record_scopes(deps)

    async for _ in run_agent("what is the refund policy?", deps=deps):
        pass

    assert seen and all(scope.tenant_id is None for scope in seen)


class _RecordingAnswerCache:
    """An answer cache that records the opaque scope string it is consulted under."""

    def __init__(self) -> None:
        self.reads: list[str] = []

    async def get(self, embedding, *, scope):  # noqa: ANN001 - host AnswerHit shape
        self.reads.append(scope)
        return None  # always a miss, so the run proceeds normally

    async def set(self, *, query, embedding, answer, scope, sources):  # noqa: ANN001
        return None


async def _answer_cache_scope(deps) -> str:
    """Run the agent once and return the scope string the answer cache was consulted under."""
    cache = _RecordingAnswerCache()
    deps.answer_cache = cache
    deps.config.answer_cache_enabled = True
    deps.config.query_rewrite_enabled = False
    deps.config.agentic_retrieval_enabled = False
    inner = deps.retrieve

    async def retrieve_with_vector(query, *, scope):
        result = await inner(query, scope=scope)
        result.query_vec = [0.1, 0.2, 0.3]  # the answer cache only fires with a vector
        return result

    deps.retrieve = retrieve_with_vector
    async for _ in run_agent("what is the refund policy?", deps=deps):
        pass
    assert cache.reads, "the answer cache must have been consulted"
    return cache.reads[0]


@pytest.mark.asyncio
async def test_the_answer_cache_and_the_retrieval_cache_agree_on_tenant_and_corpus(make_deps):
    """The two caches must invalidate together, or one serves a pre-ingest answer.

    They key differently on purpose (the answer cache adds the routed specialist role),
    but the parts that *must* match — the tenant and that tenant's corpus version — are
    derived from the same sources, and this pins that.
    """
    deps = make_deps(propose_tool=False)
    deps.current_tenant_id = lambda: 42
    before = await _answer_cache_scope(deps)
    assert before.startswith("42:")
    assert before.endswith(":c0")

    bump_corpus_version(42)
    deps = make_deps(propose_tool=False)
    deps.current_tenant_id = lambda: 42
    after = await _answer_cache_scope(deps)

    assert after.endswith(":c1")
    assert after != before, "an ingest must make the tenant's cached answers unreachable"


@pytest.mark.asyncio
async def test_the_answer_cache_scope_still_separates_tenants(make_deps):
    deps_a = make_deps(propose_tool=False)
    deps_a.current_tenant_id = lambda: 42
    deps_b = make_deps(propose_tool=False)
    deps_b.current_tenant_id = lambda: 43

    assert await _answer_cache_scope(deps_a) != await _answer_cache_scope(deps_b)
