"""Phase 0 contract tests: RetrievalResult.provenance (§4.3).

The new ``provenance`` field must default to an empty, non-fused, no-cache shape so
existing code that builds a :class:`RetrievalResult` is unaffected, and must carry
per-source origins, a fusion method, and cache lineage when populated.
"""

from __future__ import annotations

from aegis.retrieval.models import (
    CacheProvenance,
    Provenance,
    RetrievalResult,
)
from aegis.retrieval.types import FusionMethod, RetrievalOrigin


def test_provenance_defaults_empty_and_unaffecting():
    result = RetrievalResult(answer_context="ctx")
    assert result.provenance.origins == []
    assert result.provenance.fusion is FusionMethod.NONE
    assert result.provenance.cache is None
    # Existing fields untouched.
    assert result.cache_hit is False
    assert result.sources == []


def test_provenance_populated_fused():
    result = RetrievalResult(
        answer_context="ctx",
        provenance=Provenance(
            origins=[RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH],
            fusion=FusionMethod.RRF,
        ),
    )
    assert result.provenance.origins == [RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH]
    assert result.provenance.fusion is FusionMethod.RRF


def test_provenance_cache_lineage():
    prov = Provenance(
        origins=[RetrievalOrigin.CACHE],
        cache=CacheProvenance(
            kind="cache-exact",
            original_query="prior question",
            cached_at="2026-08-05T11:00:00Z",
        ),
    )
    assert prov.cache is not None
    assert prov.cache.kind == "cache-exact"
    assert prov.cache.original_query == "prior question"
