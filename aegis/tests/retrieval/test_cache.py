"""Tests for the two-tier semantic cache (exact + embedding NN)."""

from __future__ import annotations

from aegis.retrieval.cache import SemanticCache
from aegis.retrieval.models import Provenance, RetrievalResult, Source
from aegis.retrieval.types import FusionMethod, RetrievalOrigin

from .conftest import FakeRedis


def _result(text: str) -> RetrievalResult:
    return RetrievalResult(answer_context=text, sources=[Source(id="s", text=text)])


def _cache(threshold: float = 0.95) -> SemanticCache:
    return SemanticCache(FakeRedis(), ttl_seconds=60, similarity_threshold=threshold)


async def test_exact_miss_then_hit_sets_cache_hit_flag():
    cache = _cache()
    assert await cache.get_exact("what is x?", None) is None
    await cache.set("what is x?", None, [1.0, 0.0], _result("answer"))
    hit = await cache.get_exact("What is X?", None)  # normalisation: case/space-insensitive
    assert hit is not None
    assert hit.cache_hit is True
    assert hit.answer_context == "answer"


async def test_exact_scoped_by_persona():
    cache = _cache()
    await cache.set("q", "alice", [1.0, 0.0], _result("a"))
    assert await cache.get_exact("q", "bob") is None
    assert await cache.get_exact("q", "alice") is not None


async def test_semantic_hit_above_threshold():
    cache = _cache(threshold=0.9)
    await cache.set("original query", None, [1.0, 0.0, 0.0], _result("cached"))
    near = [0.99, 0.14, 0.0]  # cosine ~0.99 with the stored vector
    hit = await cache.get_semantic(near, None)
    assert hit is not None
    assert hit.cache_hit is True


async def test_semantic_miss_below_threshold():
    cache = _cache(threshold=0.95)
    await cache.set("original query", None, [1.0, 0.0], _result("cached"))
    orthogonal = [0.0, 1.0]
    assert await cache.get_semantic(orthogonal, None) is None


async def test_semantic_respects_persona():
    cache = _cache(threshold=0.5)
    await cache.set("q", "alice", [1.0, 0.0], _result("a"))
    assert await cache.get_semantic([1.0, 0.0], "bob") is None


async def test_default_threshold_is_near_exact():
    # The cache defaults to conservative near-exact substitution (§4.3, D4).
    assert SemanticCache(FakeRedis())._threshold >= 0.985


async def test_near_exact_only_substitutes_at_985():
    cache = _cache(threshold=0.985)
    await cache.set("original query", None, [1.0, 0.0], _result("cached"))
    # cosine ~0.95 with [1,0] → below the near-exact bar → NO substitution.
    assert await cache.get_semantic([0.95, 0.3122], None) is None
    # cosine ~0.99 → clears the bar → served as a near-exact hit.
    hit = await cache.get_semantic([0.99, 0.141], None)
    assert hit is not None
    assert hit.cache_hit is True


async def test_exact_hit_tags_cache_provenance_and_preserves_fusion():
    cache = _cache()
    stored = RetrievalResult(
        answer_context="a",
        provenance=Provenance(
            origins=[RetrievalOrigin.VECTOR, RetrievalOrigin.BM25],
            fusion=FusionMethod.RRF,
        ),
    )
    await cache.set("what is x?", None, [1.0, 0.0], stored)

    hit = await cache.get_exact("what is x?", None)
    assert hit is not None
    assert hit.provenance.cache is not None
    assert hit.provenance.cache.kind == "cache-exact"
    assert hit.provenance.cache.original_query == "what is x?"
    assert hit.provenance.cache.cached_at  # write timestamp recorded
    # Served-from-cache origin is prepended; original fused origins preserved.
    assert hit.provenance.origins[0] is RetrievalOrigin.CACHE
    assert RetrievalOrigin.VECTOR in hit.provenance.origins
    assert hit.provenance.fusion is FusionMethod.RRF  # fusion preserved
