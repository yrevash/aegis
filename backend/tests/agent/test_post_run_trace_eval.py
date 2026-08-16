"""Post-run trace-eval kickoff — a completed run persists EvalResult rows off the hot path.

Drives :func:`app.agent.run_agent` end-to-end with fake deps + the shared scratch
PostgreSQL database, then
awaits the tracked background grade and asserts one ``EvalResult`` row per graded facet,
all keyed by the real ``run_id``. The judge callable is a fake ``app.core.llm.complete``
(the seam the kickoff imports lazily) returning parseable JSON scores, so the grade runs
fully offline. Also asserts the kickoff never blocks the stream and is gated on stores.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from app.agent import orchestrator as orch
from app.agent import run_agent
from app.api.schemas import RunStatus
from app.core.llm import LLMResult, Usage
from app.data.models import EvalResult

pytestmark = pytest.mark.asyncio

# ``db`` is the shared scratch-PostgreSQL fixture from ``tests/conftest.py``.


async def _judge_complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
    """A fake gateway complete: returns JSON usable by BOTH the answer + cheap judges."""
    return LLMResult(
        content='{"score": 0.8, "groundedness": 0.9, "relevance": 0.85}',
        usage=Usage(),
    )


async def _drain(agen) -> list:
    return [event async for event in agen]


async def test_completed_run_writes_eval_rows_keyed_by_run_id(db, make_deps, monkeypatch):
    # The trace-eval grade imports app.core.llm.complete lazily — point it at a fake so
    # the off-hot-path judge runs offline and actually writes rows.
    monkeypatch.setattr("app.core.llm.complete", _judge_complete)
    orch._TRACE_EVAL_TASKS.clear()

    deps = make_deps(propose_tool=True, high_risk=False)
    events = await _drain(
        run_agent(
            "Please resolve request R1",
            persona="operations_lead",
            role="admin",
            deps=deps,
            run_id="run-eval-1",
        )
    )
    # The stream completed cleanly and was never blocked by the grade.
    assert events[-1].type == "run_finished"
    assert events[-1].status is RunStatus.COMPLETED

    # The kickoff scheduled a tracked background task; await it to completion.
    assert orch._TRACE_EVAL_TASKS, "post-run trace-eval was not scheduled"
    await asyncio.gather(*list(orch._TRACE_EVAL_TASKS))

    async with db() as session:
        rows = list(
            (
                await session.execute(
                    select(EvalResult).where(EvalResult.run_id == "run-eval-1")
                )
            ).scalars().all()
        )
    metrics = {r.metric for r in rows}
    # The answer plus the trajectory step facets the run actually produced.
    assert "answer" in metrics
    assert "step:retrieval" in metrics  # retrieval produced context
    assert "step:tool" in metrics       # a tool executed
    assert "step:guardrail" in metrics  # input/output rails emitted verdicts
    # Every row is keyed by the real run id and carries a graded score.
    assert rows and all(r.run_id == "run-eval-1" for r in rows)
    assert all(0.0 <= r.score <= 1.0 for r in rows)


async def test_kickoff_gated_on_stores_disabled(db, make_deps, monkeypatch):
    # Stores off → the grade needs the DB, so the kickoff is a silent no-op.
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "stores", "off")
    monkeypatch.setattr("app.core.llm.complete", _judge_complete)
    orch._TRACE_EVAL_TASKS.clear()

    deps = make_deps(propose_tool=True, high_risk=False)
    events = await _drain(
        run_agent(
            "Please resolve request R1",
            persona="operations_lead",
            role="admin",
            deps=deps,
            run_id="run-eval-2",
        )
    )
    assert events[-1].type == "run_finished"
    assert not orch._TRACE_EVAL_TASKS  # nothing scheduled when stores are off
