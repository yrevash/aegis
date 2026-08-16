"""Tests for the shared record store and the risk lookup in :mod:`app.agent.deps`.

This file used to cover ML feature-subject resolution as well; the ML step was
removed from the agent graph, so ``_default_features_for`` and its tests are gone.
The two claims kept here are not about ML and would have been lost with them:

* the process-wide record store really seeds records, so ``run_tool`` acts on a
  concrete row rather than succeeding against an empty store, and
* an unregistered tool name resolves to HIGH risk, so a hallucinated tool can never
  slip under the autonomy ceiling and skip the human gate (L2).
"""

from __future__ import annotations

import pytest

from app.adapter import InMemoryRecordStore
from app.agent import deps as agent_deps

pytestmark = pytest.mark.asyncio


async def test_shared_store_seeds_real_records_not_an_empty_coroutine(monkeypatch):
    """Regression guard: the shared store must be populated, not a bare coroutine.

    ``_get_shared_store`` once called the *async* ``generate_synthetic`` without
    awaiting it, so the store was silently EMPTY. ``_default_run_tool`` builds its
    ``ToolContext`` from this store, so an empty one means every gated action
    executes against nothing at all. Force a cold rebuild and assert it is real.
    """
    monkeypatch.setattr(agent_deps, "_shared_store", None)
    store = agent_deps._get_shared_store()
    records = store.list_requests()
    assert len(records) > 0, "shared store is empty — tool actions would hit nothing"


async def test_shared_store_looks_up_a_record_by_id(monkeypatch):
    monkeypatch.setattr(agent_deps, "_shared_store", None)
    store = agent_deps._get_shared_store()
    first = store.list_requests()[0]
    assert store.get_request(first.id) is first


async def test_empty_store_lists_no_records(monkeypatch):
    monkeypatch.setattr(agent_deps, "_shared_store", InMemoryRecordStore([]))
    assert agent_deps._get_shared_store().list_requests() == []


async def test_unregistered_tool_defaults_to_high_risk():
    # L2: a hallucinated / unregistered tool must fail SAFE (HIGH), so it can never
    # slip under the autonomy ceiling and skip the human gate.
    from app.adapter import TOOL_REGISTRY
    from app.api.schemas import RiskLevel

    assert agent_deps._default_tool_risk("definitely_not_a_real_tool") is RiskLevel.HIGH

    # A registered tool still returns its own declared risk.
    known = next(iter(TOOL_REGISTRY))
    assert agent_deps._default_tool_risk(known) is TOOL_REGISTRY[known].risk
