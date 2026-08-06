"""Tests for the ML feature-subject resolution in :mod:`app.agent.deps` (L1).

A query that names a concrete record must be featurised against *that* record via
the store's public accessor — not always against the first synthetic record.
"""

from __future__ import annotations

import pytest

from app.adapter import (
    GeneratorConfig,
    InMemoryRecordStore,
    features_for_request,
    generate_synthetic,
)
from app.agent import deps as agent_deps

pytestmark = pytest.mark.asyncio


async def _seeded_store(monkeypatch) -> InMemoryRecordStore:
    """Build a deterministic, LLM-free store and install it as the shared store."""
    dataset = await generate_synthetic(GeneratorConfig(seed=7, use_llm=False))
    store = InMemoryRecordStore.from_dataset(dataset)
    monkeypatch.setattr(agent_deps, "_shared_store", store)
    return store


async def test_features_resolve_the_referenced_record(monkeypatch):
    store = await _seeded_store(monkeypatch)
    records = store.list_requests()
    assert len(records) > 1

    first_feats = features_for_request(records[0], agent=None, customer=None)
    target = next(
        (
            r
            for r in records[1:]
            if features_for_request(r, agent=None, customer=None) != first_feats
        ),
        None,
    )
    assert target is not None, "expected at least one record distinct from the first"

    result = agent_deps._default_features_for(
        f"How long until {target.id} is resolved?", None
    )
    assert result == features_for_request(target, agent=None, customer=None)
    assert result != first_feats


async def test_features_fall_back_to_first_record_without_an_id(monkeypatch):
    store = await _seeded_store(monkeypatch)
    records = store.list_requests()

    result = agent_deps._default_features_for("what is the refund policy?", None)
    assert result == features_for_request(records[0], agent=None, customer=None)


async def test_features_empty_when_store_has_no_records(monkeypatch):
    monkeypatch.setattr(agent_deps, "_shared_store", InMemoryRecordStore([]))
    assert agent_deps._default_features_for("anything about req-000001", None) == {}


async def test_shared_store_seeds_real_records_not_an_empty_coroutine(monkeypatch):
    """Regression guard on the CRITICAL dead-ML bug.

    ``_get_shared_store`` previously called the *async* ``generate_synthetic`` without
    awaiting it, so the store was silently EMPTY and the whole ML solution-signal
    never ran (``features_for`` returned ``{}`` → ``predict_explain`` never called).
    Forcing a cold rebuild of the real accessor must now yield a populated store, so
    the agent's ML path is genuinely alive.
    """
    monkeypatch.setattr(agent_deps, "_shared_store", None)
    store = agent_deps._get_shared_store()
    records = store.list_requests()
    assert len(records) > 0, "shared store is empty — the ML path would be dead"
    # And a query resolves real features (the input to the live prediction).
    feats = agent_deps._default_features_for(f"resolve {records[0].id}", None)
    assert feats, "features_for empty — ml_predict would early-return"


async def test_unregistered_tool_defaults_to_high_risk():
    # L2: a hallucinated / unregistered tool must fail SAFE (HIGH), so it can never
    # slip under the autonomy ceiling and skip the human gate.
    from app.adapter import TOOL_REGISTRY
    from app.api.schemas import RiskLevel

    assert agent_deps._default_tool_risk("definitely_not_a_real_tool") is RiskLevel.HIGH

    # A registered tool still returns its own declared risk.
    known = next(iter(TOOL_REGISTRY))
    assert agent_deps._default_tool_risk(known) is TOOL_REGISTRY[known].risk
