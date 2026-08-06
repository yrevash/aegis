"""Live trace-eval tests: grade a run's answer + steps and persist EvalResults.

Covers the three contract points:

* online (injected fake ``complete``) writes an ``answer`` row **and** ``step:*``
  rows keyed by the ``run_id``, with ``overall`` == the mean of written scores;
* offline (``complete=None``) degrades to deterministic proxies only, writes
  rows, never calls a model, and never raises;
* a judge that raises on one metric doesn't sink the others (partial failure).
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.models import ModelRole
from app.data.models import EvalResult
from app.data.session import bootstrap, configure_engine, get_sessionmaker
from app.ops.trace_eval import RunEval, evaluate_run

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def db(tmp_path) -> async_sessionmaker:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ops.db'}")
    configure_engine(engine)
    await bootstrap(engine)
    yield get_sessionmaker()
    await engine.dispose()


@dataclass
class _FakeResult:
    """Mimics an ``LLMResult`` — only ``.content`` is read by the graders."""

    content: str


def _fake_complete(*, calls: list[ModelRole] | None = None, raise_on_cheap: bool = False):
    """Build a scripted ``complete`` that records roles and returns canned JSON.

    REASONING (the answer judge) → groundedness/relevance; CHEAP (per-step
    judges) → a single ``score``. Optionally raises on the CHEAP path to exercise
    partial-failure robustness.
    """

    async def complete(role, messages, *, temperature=0.0, response_format=None):  # noqa: ANN001
        if calls is not None:
            calls.append(role)
        if role is ModelRole.REASONING:
            return _FakeResult('{"groundedness": 0.8, "relevance": 0.6}')
        if role is ModelRole.CHEAP:
            if raise_on_cheap:
                raise RuntimeError("cheap judge exploded")
            return _FakeResult('{"score": 0.5}')
        raise AssertionError(f"unexpected role {role}")

    return complete


_STEPS = [
    {"node": "retrieve", "kind": "RETRIEVER", "detail": {"contexts": ["alpha beta"]}},
    {"node": "guard_input", "kind": "GUARDRAIL", "detail": {"verdict": "allow", "input": "hi"}},
    {"node": "act", "kind": "TOOL", "detail": {"tool": "search", "ok": True}},
    {"node": "plan", "kind": "CHAIN", "detail": {}},  # ignored (not a graded kind)
]


async def test_online_writes_answer_and_step_rows(db):
    calls: list[ModelRole] = []
    async with db() as s:
        result = await evaluate_run(
            s,
            run_id="run-1",
            query="what is alpha",
            answer="alpha is the first letter",
            contexts=["alpha is first", "beta is second"],
            steps=_STEPS,
            complete=_fake_complete(calls=calls),
            tenant_id=7,
        )
        await s.commit()

    assert isinstance(result, RunEval)
    # answer + retrieval + guardrail + tool (CHAIN skipped) = 4 metrics.
    assert set(result.metrics) == {"answer", "step:retrieval", "step:guardrail", "step:tool"}
    # answer blends 0.8/0.6 -> 0.7; every step judge returns 0.5.
    assert result.metrics["answer"] == pytest.approx(0.7)
    assert result.metrics["step:retrieval"] == pytest.approx(0.5)
    expected_overall = (0.7 + 0.5 + 0.5 + 0.5) / 4
    assert result.overall == pytest.approx(expected_overall)
    assert result.passed is (expected_overall >= 0.6)
    # Reasoning judge for the answer + three cheap judges for the steps.
    assert calls.count(ModelRole.REASONING) == 1
    assert calls.count(ModelRole.CHEAP) == 3

    async with db() as s:
        rows = (
            await s.execute(select(EvalResult).where(EvalResult.run_id == "run-1"))
        ).scalars().all()
    assert {r.metric for r in rows} == set(result.metrics)
    assert all(r.run_id == "run-1" for r in rows)
    assert all(r.tenant_id == 7 for r in rows)
    answer_row = next(r for r in rows if r.metric == "answer")
    assert answer_row.detail["method"] == "judge"
    assert answer_row.score == pytest.approx(0.7)


async def test_offline_deterministic_only_no_model_call(db):
    sentinel_called = False

    async def exploding_complete(*args, **kwargs):  # pragma: no cover - must never run
        nonlocal sentinel_called
        sentinel_called = True
        raise AssertionError("offline path must not call a model")

    async with db() as s:
        result = await evaluate_run(
            s,
            run_id="run-2",
            query="alpha beta gamma",
            answer="alpha beta",
            contexts=["alpha beta gamma delta"],
            steps=_STEPS,
            complete=None,  # offline
        )
        await s.commit()

    assert sentinel_called is False
    assert set(result.metrics) == {"answer", "step:retrieval", "step:guardrail", "step:tool"}
    # Deterministic groundedness: both answer tokens present in context -> 1.0.
    assert result.metrics["answer"] == pytest.approx(1.0)
    # Tool ran ok -> 1.0; guardrail has a verdict -> 1.0.
    assert result.metrics["step:tool"] == pytest.approx(1.0)
    assert result.metrics["step:guardrail"] == pytest.approx(1.0)

    async with db() as s:
        rows = (
            await s.execute(select(EvalResult).where(EvalResult.run_id == "run-2"))
        ).scalars().all()
    assert len(rows) == 4
    assert all(r.detail["method"] == "deterministic" for r in rows)


async def test_one_failing_metric_does_not_sink_others(db):
    # Cheap judge raises (kills every step:* metric) but the reasoning answer
    # judge still succeeds, so the answer row survives.
    async with db() as s:
        result = await evaluate_run(
            s,
            run_id="run-3",
            query="what is alpha",
            answer="alpha",
            contexts=["alpha is first"],
            steps=_STEPS,
            complete=_fake_complete(raise_on_cheap=True),
        )
        await s.commit()

    assert "answer" in result.metrics
    assert result.metrics["answer"] == pytest.approx(0.7)
    # All step metrics were skipped by the catch — none persisted.
    assert set(result.metrics) == {"answer"}
    assert result.overall == pytest.approx(0.7)

    async with db() as s:
        rows = (
            await s.execute(select(EvalResult).where(EvalResult.run_id == "run-3"))
        ).scalars().all()
    assert len(rows) == 1
    assert rows[0].metric == "answer"


async def test_no_matching_steps_still_grades_answer(db):
    async with db() as s:
        result = await evaluate_run(
            s,
            run_id="run-4",
            query="q",
            answer="a",
            contexts=[],
            steps=[],
            complete=None,
        )
        await s.commit()
    # Only the answer metric; empty answer/context -> deterministic 0.0.
    assert set(result.metrics) == {"answer"}
    assert result.overall == pytest.approx(result.metrics["answer"])
    assert result.passed is (result.overall >= 0.6)
