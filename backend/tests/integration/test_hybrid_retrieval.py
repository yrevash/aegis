"""E2E (d): hybrid retrieval surfaces RRF, multi-origin provenance through ``/query``.

Wires the agent's ``retrieve`` capability to the **real** hybrid pipeline
(:class:`~app.retrieval.pipeline.Retriever` over the databaseless
:class:`~app.retrieval.memory.InMemoryKnowledgeBackend`) — genuine vector + graph + BM25
recall fused by Reciprocal Rank Fusion — and asserts the streamed ``provenance`` event
reports ``fusion == "rrf"`` over multiple origins. Only the embedding and reranker are
deterministic local fakes, so the run is offline; the fusion under test is the real one.
"""

from __future__ import annotations

import json

import pytest

from app.api import routes as api_routes
from app.core.llm import LLMResult, Usage
from app.main import app
from app.retrieval.cache import SemanticCache
from app.retrieval.memory import InMemoryKnowledgeBackend, InMemoryRedis, _local_embed
from app.retrieval.models import Chunk
from app.retrieval.pipeline import RetrievalConfig, Retriever

pytestmark = pytest.mark.asyncio


def _real_retriever() -> Retriever:
    """Build an offline hybrid retriever over a tiny, overlapping corpus."""
    chunks = [
        Chunk(
            id="closures#0",
            doc_id="closures",
            ordinal=0,
            text=(
                "A closure is confirmed by the original approver within 5 to 7 "
                "business days after the request work is verified."
            ),
        ),
        Chunk(
            id="escalation#0",
            doc_id="escalation",
            ordinal=0,
            text=(
                "Escalate a request to Tier-2 when its SLA is at risk; closures for "
                "enterprise requests are approved one tier earlier."
            ),
        ),
        Chunk(
            id="login#0",
            doc_id="login",
            ordinal=0,
            text="Login failures returning HTTP 500 should be linked to an auth incident.",
        ),
    ]
    backend = InMemoryKnowledgeBackend(chunks)

    async def fake_complete(
        role, messages, *, tools=None, temperature=0.0, response_format=None
    ):  # noqa: ANN001
        return LLMResult(content="", usage=Usage())  # rerank falls back to RRF order

    async def fake_embed(texts):  # noqa: ANN001
        return [_local_embed(t) for t in texts]

    cache = SemanticCache(InMemoryRedis(), similarity_threshold=0.99)
    return Retriever(
        backend=backend, cache=cache, complete=fake_complete, embed=fake_embed,
        config=RetrievalConfig(),
    )


async def test_query_emits_rrf_multi_origin_provenance(
    client, db, admin_headers, make_deps, parse_sse
):
    retriever = _real_retriever()
    deps = make_deps(propose_tool=False)  # pure Q&A — no gate, exercises retrieval
    deps.retrieve = retriever.retrieve
    app.dependency_overrides[api_routes.get_agent_deps] = lambda: deps

    resp = await client.post(
        "/query",
        json={"query": "How long does a closure take to reach the original approver?"},
        headers=admin_headers,
    )
    assert resp.status_code == 200
    events = parse_sse(resp.text)
    types = [e["event"] for e in events]
    assert "provenance" in types

    prov = json.loads(next(e for e in events if e["event"] == "provenance")["data"])
    assert prov["fusion"] == "rrf"  # explicit Reciprocal Rank Fusion, not a shortcut
    assert len(prov["origins"]) >= 2  # genuinely multi-origin (vector + graph/bm25)
    assert set(prov["origins"]).issubset({"vector", "graph", "bm25", "cache"})
    assert prov["cache_hit"] is False  # fresh recall, not served from cache
    assert types[-1] == "run_finished"
