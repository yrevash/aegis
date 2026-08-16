"""Integration tests for the retrieval-intelligence wiring in the agent graph.

These drive the REAL compiled LangGraph (``build_agent``) with fakes only — a scripted
``complete`` (rewrite / sufficiency-judge / generation branches) and a call-counting
``retrieve`` — to prove the three pieces the orchestrator wires in actually run:

* the bounded Self-RAG/FLARE loop re-retrieves when the first round is judged
  insufficient and MERGES the evidence (a glass-box ``reasoning`` event surfaces the
  two passes and the retrieve node accrues the judge spend into per-run telemetry);
* context-aware query rewriting changes the query actually fed to retrieval;
* the answer-level semantic cache serves a second, equivalent qa run from cache and
  skips the expensive generation call entirely.

Offline: no network, no keys, no Postgres/Neo4j/Redis (the answer cache runs against the
real ``InMemoryRedis`` fake). ``asyncio_mode = "auto"`` runs the coroutines directly.
"""

from __future__ import annotations

import dataclasses
import json
from uuid import uuid4

from aegis.retrieval.types import RetrievalScope
from tests.conftest import build_fake_deps

from app.agent import build_agent
from app.core.llm import LLMResult, Usage
from app.retrieval.answer_cache import AnswerCache
from app.retrieval.memory import InMemoryRedis
from app.retrieval.models import GraphDelta, RetrievalResult, Source

# Fixed query embedding so a repeated query is ≥ threshold similar to itself (a query is
# always cosine-1.0 to itself), exercising the answer cache's semantic tier honestly.
_QVEC = [1.0, 0.0, 0.0]


def _gen(content: str) -> LLMResult:
    """A fake GENERATION result with no tool calls (pure Q&A answer)."""
    return LLMResult(
        content=content,
        tool_calls=[],
        usage=Usage(prompt_tokens=8, completion_tokens=6, cost_usd=0.0005),
        model="fake-generation",
    )


def _json(payload: dict) -> LLMResult:
    """A fake cheap-model JSON result (rewrite / sufficiency judge)."""
    return LLMResult(
        content=json.dumps(payload),
        tool_calls=[],
        usage=Usage(prompt_tokens=5, completion_tokens=3, cost_usd=0.0001),
        model="fake-cheap",
    )


def _is_rewrite(messages: list[dict]) -> bool:
    return "rewrite a user's latest turn" in messages[0]["content"]


def _is_sufficiency(messages: list[dict]) -> bool:
    return "retrieval sufficiency judge" in messages[0]["content"]


def _result(
    context: str, source_id: str, *, score: float = 0.9, with_vec: bool = False
) -> RetrievalResult:
    """A minimal RetrievalResult with one source (and optionally a real query_vec)."""
    return RetrievalResult(
        answer_context=context,
        sources=[Source(id=source_id, text=context, score=score)],
        num_candidates=5,
        graph_delta=GraphDelta(nodes=[], edges=[]),
        cache_hit=False,
        query_vec=_QVEC if with_vec else None,
    )


@dataclasses.dataclass
class _Run:
    """Everything an assertion needs from one graph run.

    Attributes:
        values: The final checkpointed graph state.
        reasoning: The ``reasoning`` glass-box event texts, in emission order.
        retrieve_update: The ``retrieve`` node's state delta (carries the accrued
            per-run token/cost totals as of that node).
    """

    values: dict
    reasoning: list[str]
    retrieve_update: dict


async def _drive(deps) -> _Run:  # noqa: ANN001
    """Run one query through the real graph, capturing state, events, and node deltas."""
    graph = build_agent(deps)
    config = {"configurable": {"thread_id": uuid4().hex}}
    stream_input = {
        "run_id": config["configurable"]["thread_id"],
        "trace_id": "t",
        "query": "How does the refund policy work for overdue requests?",
        "persona": "operations_lead",
        "role": "user",
        "session_id": None,
        "memory_subject": None,
        "turn_index": 0,
        "messages": [],
        "tool_calls": [],
        "tool_results": [],
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost_usd": 0.0,
    }
    reasoning: list[str] = []
    retrieve_update: dict = {}
    async for mode, chunk in graph.astream(
        stream_input, config, stream_mode=["custom", "updates"]
    ):
        if mode == "custom" and isinstance(chunk, dict) and chunk.get("type") == "reasoning":
            reasoning.append(chunk["text"])
        elif mode == "updates" and isinstance(chunk, dict):
            delta = chunk.get("retrieve")
            if isinstance(delta, dict):
                retrieve_update = delta
    return _Run(
        values=graph.get_state(config).values,
        reasoning=reasoning,
        retrieve_update=retrieve_update,
    )


# ── (a) insufficient first round triggers a second, merged retrieval ─────────
async def test_insufficient_first_round_triggers_second_retrieval():
    base = build_fake_deps()
    retrieve_calls: list[str] = []

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
        retrieve_calls.append(query)
        # Distinct sources per round so a genuine MERGE is observable. Round 2 scores
        # higher so it survives the first-round-capped dedupe (proving the union ran).
        n = len(retrieve_calls)
        return _result(f"round-{n} context", f"kb-{n}", score=0.9 + 0.01 * n)

    judge_calls = {"n": 0}

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if _is_sufficiency(messages):
            judge_calls["n"] += 1
            if judge_calls["n"] == 1:
                # First context insufficient → drive a focused follow-up retrieval.
                return _json(
                    {
                        "sufficient": False,
                        "reason": "missing detail",
                        "followup_query": "refund policy exceptions for overdue requests",
                    }
                )
            return _json({"sufficient": True, "reason": "enough", "followup_query": None})
        return _gen("Refunds on overdue requests follow the standard policy.")

    deps = dataclasses.replace(
        base,
        retrieve=retrieve,
        complete=complete,
        config=dataclasses.replace(
            base.config,
            agentic_retrieval_enabled=True,
            query_rewrite_enabled=False,
            answer_cache_enabled=False,
            agentic_retrieval_max_rounds=2,
        ),
    )

    run = await _drive(deps)
    final = run.values

    # The loop ran a second retrieval pass on the insufficient verdict.
    assert len(retrieve_calls) == 2
    assert retrieve_calls[1] == "refund policy exceptions for overdue requests"
    # The agentic rounds are now surfaced as a glass-box reasoning event (real
    # consumption) rather than write-only state; the follow-up query is visible in it.
    agentic_note = next(r for r in run.reasoning if "Agentic retrieval ran" in r)
    assert "2 rounds" in agentic_note
    assert "refund policy exceptions for overdue requests" in agentic_note
    # The two judge calls' spend is accrued into the run's per-run telemetry by the
    # retrieve node (2 calls × 5 prompt / 3 completion tokens each). Retrieve runs
    # before any generation, so its delta carries exactly the judge spend.
    assert run.retrieve_update["prompt_tokens"] == 10
    assert run.retrieve_update["completion_tokens"] == 6
    assert run.retrieve_update["cost_usd"] > 0.0
    # The two rounds' distinct sources were merged: round 2's higher-scored source
    # survives the union (the spotlight assembler substitutes spaces, so match the id).
    assert "round-2" in final["context"]
    assert final["answer"]


# ── (b) query rewrite changes the query fed to retrieval ─────────────────────
async def test_query_rewrite_changes_retrieval_query():
    base = build_fake_deps()
    retrieve_calls: list[str] = []
    rewritten = "standalone: how the refund policy handles overdue requests"

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
        retrieve_calls.append(query)
        return _result("policy context", "kb-1")

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        if _is_rewrite(messages):
            return _json({"rewritten": rewritten, "reason": "resolved back-reference"})
        return _gen("Here is how refunds work for overdue requests.")

    deps = dataclasses.replace(
        base,
        retrieve=retrieve,
        complete=complete,
        config=dataclasses.replace(
            base.config,
            agentic_retrieval_enabled=False,  # isolate the rewrite-then-retrieve path
            query_rewrite_enabled=True,
            answer_cache_enabled=False,
        ),
    )

    run = await _drive(deps)
    final = run.values

    # Retrieval was performed with the REWRITTEN query, not the raw turn.
    assert retrieve_calls == [rewritten]
    # The rewrite is surfaced as a glass-box reasoning event (real consumption of what
    # used to be write-only state), carrying the rewritten query.
    rewrite_note = next(r for r in run.reasoning if "Rewrote the query" in r)
    assert rewritten in rewrite_note
    # The single rewrite call's spend is accrued into per-run telemetry by retrieve
    # (usage 5 prompt / 3 completion tokens from the fake cheap-model JSON result).
    assert run.retrieve_update["prompt_tokens"] == 5
    assert run.retrieve_update["completion_tokens"] == 3
    assert run.retrieve_update["cost_usd"] > 0.0
    assert final["answer"]


# ── (c) answer cache serves a second equivalent run, skipping generation ─────
async def test_answer_cache_serves_second_run_without_generation():
    base = build_fake_deps()
    generation_calls = {"n": 0}
    cache = AnswerCache(InMemoryRedis(), ttl_seconds=60, similarity_threshold=0.9)

    async def retrieve(query: str, *, scope: RetrievalScope) -> RetrievalResult:
        return _result("policy context", "kb-1", with_vec=True)

    async def complete(role, messages, *, tools=None, temperature=0.0, response_format=None):  # noqa: ANN001
        # Only generation calls reach here (rewrite/agentic/judge are disabled).
        generation_calls["n"] += 1
        return _gen("Refunds are issued within five business days.")

    deps = dataclasses.replace(
        base,
        retrieve=retrieve,
        complete=complete,
        answer_cache=cache,
        config=dataclasses.replace(
            base.config,
            agentic_retrieval_enabled=False,
            query_rewrite_enabled=False,
            answer_cache_enabled=True,
        ),
    )

    first = (await _drive(deps)).values
    assert not first.get("answer_cached")
    assert first["answer"] == "Refunds are issued within five business days."
    assert generation_calls["n"] == 1  # first run paid for generation

    second = (await _drive(deps)).values
    assert second.get("answer_cached") is True  # served from the answer cache
    assert second["answer"] == "Refunds are issued within five business days."
    assert generation_calls["n"] == 1  # NO second generation call — the cache saved it
