"""Phase 0 contract tests: the new SSE stream-event variants (§1.3/2.3/3.3/4.3).

The four additive variants must validate and round-trip through the discriminated
``StreamEvent`` union on their ``type`` tag, and the existing variants must be
unaffected.
"""

from __future__ import annotations

from pydantic import TypeAdapter

from app.api.schemas import (
    ApprovalQueued,
    ApprovalRequired,
    AutonomyBand,
    BudgetExceeded,
    FusionMethod,
    ProvenanceEvent,
    RetrievalOrigin,
    RiskLevel,
    StreamEvent,
)

_adapter: TypeAdapter = TypeAdapter(StreamEvent)


def _roundtrip(event):
    """Serialise an event and re-validate it through the discriminated union."""
    return _adapter.validate_json(event.model_dump_json())


def test_approval_queued_variant():
    ev = ApprovalQueued(
        run_id="run-1",
        seq=5,
        approval_id="apr-1",
        action="update_request_status",
        args={"request_id": "R1"},
        risk=RiskLevel.HIGH,
        rationale="wide interval",
        sla_deadline="2026-08-05T13:00:00Z",
        assignee_tier="tier-1",
    )
    back = _roundtrip(ev)
    assert isinstance(back, ApprovalQueued)
    assert back.type == "approval_queued"
    assert back.approval_id == "apr-1"
    assert back.sla_deadline == "2026-08-05T13:00:00Z"


def test_provenance_variant():
    ev = ProvenanceEvent(
        run_id="run-1",
        seq=7,
        origins=[RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH],
        fusion=FusionMethod.RRF,
        cache_hit=False,
    )
    back = _roundtrip(ev)
    assert isinstance(back, ProvenanceEvent)
    assert back.type == "provenance"
    assert back.origins == [RetrievalOrigin.VECTOR, RetrievalOrigin.GRAPH]
    assert back.fusion is FusionMethod.RRF


def test_provenance_variant_cache_hit_lineage():
    ev = ProvenanceEvent(
        run_id="run-1",
        seq=8,
        origins=[RetrievalOrigin.CACHE],
        fusion=FusionMethod.NONE,
        cache_hit=True,
        cache_kind="cache-near",
        original_query="prior question",
        cached_at="2026-08-05T11:00:00Z",
    )
    back = _roundtrip(ev)
    assert back.cache_hit is True
    assert back.cache_kind == "cache-near"
    assert back.original_query == "prior question"


def test_budget_exceeded_variant():
    ev = BudgetExceeded(
        run_id="run-1",
        seq=9,
        scope="tenant",
        scope_id=3,
        limit_type="usd_cap",
        limit=250.0,
        used=251.5,
        message="tenant monthly USD cap exceeded",
    )
    back = _roundtrip(ev)
    assert isinstance(back, BudgetExceeded)
    assert back.type == "budget_exceeded"
    assert back.limit_type == "usd_cap"
    assert back.scope_id == 3


def test_existing_variant_still_resolves():
    # The additive variants must not shadow existing tags in the union.
    ev = ApprovalRequired(
        run_id="run-1",
        seq=1,
        approval_id="apr-1",
        action="update_request_status",
        args={},
        risk=RiskLevel.HIGH,
        rationale="high risk",
    )
    back = _roundtrip(ev)
    assert isinstance(back, ApprovalRequired)
    assert back.type == "approval_required"


def test_enum_member_values_frozen():
    assert [b.value for b in AutonomyBand] == ["autonomous", "defer", "abstain"]
    assert [o.value for o in RetrievalOrigin] == ["vector", "graph", "bm25", "cache"]
    assert [f.value for f in FusionMethod] == ["none", "rrf", "mix"]
