"""The orchestrator surfaces a BudgetExceededError as a terminal event (§3.3)."""

from __future__ import annotations

import pytest

from aegis.agent import run_agent
from aegis.gateway.types import BudgetExceededError

pytestmark = pytest.mark.asyncio


async def _collect(agen):
    return [event async for event in agen]


async def test_budget_exceeded_emits_terminal_event(make_deps):
    deps = make_deps()

    async def _blocked_complete(
        role, messages, *, tools=None, temperature=0.0, response_format=None
    ):
        raise BudgetExceededError(
            scope="tenant",
            scope_id=1,
            limit_type="token_cap",
            limit=100.0,
            used=150.0,
        )

    deps.complete = _blocked_complete

    events = await _collect(
        run_agent("please act", persona="operations_lead", role="admin", deps=deps)
    )
    types = [e["type"] for e in events]

    assert "budget_exceeded" in types
    budget = next(e for e in events if e["type"] == "budget_exceeded")
    assert budget["scope"] == "tenant"
    assert budget["limit_type"] == "token_cap"
    assert budget["limit"] == 100.0
    assert budget["used"] == 150.0

    # The run ends cleanly as blocked (not a crash / error), and it is terminal.
    assert types[-1] == "run_finished"
    assert events[-1]["status"] == "blocked"
    assert "tool_result" not in types
