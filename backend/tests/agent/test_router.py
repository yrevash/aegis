"""Tests for the supervisor router — deterministic classifier + the memory specialist.

Two layers are exercised, both offline (fakes only, no infra):

1. **Classifier mechanism** (``app.agent.router``): the deterministic keyword pass picks
   the right role with NO model call for clear cases; an unmatched query falls through to
   the ``qa`` default; a genuine tie between two named specialists is the ONLY thing that
   escalates to the cheap-LLM tiebreak, and even then an inconclusive reply defaults to qa.
2. **End-to-end routing** (``run_agent``): a normal question routes to ``qa`` and runs the
   full pipeline; a "what do you know about me" query routes to ``memory``, emits the
   ``routing`` hand-off with ``role='memory'``, answers straight from the recalled memory,
   and never touches retrieval / the tool+gate path.
"""

from __future__ import annotations

import pytest

from app.adapter.roster import AgentRoster, RosterSpecialist, agent_roster
from app.agent import run_agent
from app.agent.router import (
    RouterDecision,
    classify_deterministic,
    load_roster,
    route_query,
)
from app.memory.working import AssembledMemory


def _ordered_subsequence(whole: list[str], sub: list[str]) -> bool:
    it = iter(whole)
    return all(item in it for item in sub)


class _BoomComplete:
    """A ``complete`` that fails loudly if the router ever calls it (proves 'no LLM')."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, role, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        raise AssertionError("router should not have called the LLM for a clear case")


# ── 1. Deterministic classifier ───────────────────────────────────────────────


def test_deterministic_picks_memory_for_self_referential_query():
    roster = agent_roster()
    role, reason = classify_deterministic("what do you know about me?", roster)
    assert role == "memory"
    assert "memory" in reason


def test_deterministic_defaults_to_qa_when_nothing_matches():
    roster = agent_roster()
    role, reason = classify_deterministic("what is the escalation policy?", roster)
    assert role == "qa"
    assert "no specialist keywords matched" in reason


def test_deterministic_does_not_flag_incidental_my_phrases():
    # "please help with my account" must NOT be pulled to the memory specialist.
    roster = agent_roster()
    role, _ = classify_deterministic("please help with my account", roster)
    assert role == "qa"


@pytest.mark.asyncio
async def test_route_query_clear_case_uses_no_llm():
    roster = agent_roster()
    boom = _BoomComplete()

    decision = await route_query("what do you remember about me?", roster, complete=boom)

    assert isinstance(decision, RouterDecision)
    assert decision.role == "memory"
    assert decision.used_llm is False
    assert boom.calls == 0  # the deterministic pass answered; the model was untouched


# ── 2. Ambiguity → cheap-LLM tiebreak (the only escalation) ────────────────────


def _ambiguous_roster() -> AgentRoster:
    """A roster where two named specialists share a keyword, forcing a tie."""
    return AgentRoster(
        specialists=(
            RosterSpecialist(role="qa", description="default", is_default=True),
            RosterSpecialist(role="alpha", description="A", keywords=("overlap",)),
            RosterSpecialist(role="beta", description="B", keywords=("overlap",)),
        )
    )


def test_ambiguous_tie_is_reported_by_deterministic_pass():
    role, reason = classify_deterministic("this has overlap", _ambiguous_roster())
    assert role is None  # ambiguous → the caller must break the tie
    assert "ambiguous" in reason


@pytest.mark.asyncio
async def test_ambiguity_without_model_falls_back_to_qa():
    decision = await route_query("this has overlap", _ambiguous_roster(), complete=None)
    assert decision.role == "qa"
    assert decision.used_llm is False


@pytest.mark.asyncio
async def test_ambiguity_uses_cheap_llm_tiebreak():
    picked: list = []

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        picked.append(role)

        class _R:
            content = "beta"

        return _R()

    decision = await route_query("this has overlap", _ambiguous_roster(), complete=complete)
    from app.core.models import ModelRole

    assert decision.role == "beta"
    assert decision.used_llm is True
    assert picked == [ModelRole.CHEAP]  # routed on the CHEAP role, not GENERATION


@pytest.mark.asyncio
async def test_inconclusive_tiebreak_defaults_to_qa():
    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        class _R:
            content = "not-a-real-role"

        return _R()

    decision = await route_query("this has overlap", _ambiguous_roster(), complete=complete)
    assert decision.role == "qa"
    assert decision.used_llm is True


def test_load_roster_returns_routable_roles():
    roster = load_roster()
    assert "qa" in roster.roles()
    assert "memory" in roster.roles()
    assert roster.default_role == "qa"


# ── 3. End-to-end: memory specialist answers from memory, skipping RAG + tools ─


class _FakeMemory:
    """Duck-typed stand-in for ``MemoryDeps`` returning a fixed recalled block."""

    def __init__(self) -> None:
        self.assembled = AssembledMemory(
            text="## Durable facts\n- the customer prefers email\n- timezone is CET",
            recalled_fact_ids=[1, 2],
            recalled_message_ids=[7],
            tokens_used=11,
        )
        self.assemble_calls: list[dict] = []
        self.persist_calls: list[dict] = []

    async def assemble(self, *, subject_id, session_id, persona, query, query_vec):  # noqa: ANN001
        self.assemble_calls.append({"subject_id": subject_id, "query": query})
        return self.assembled

    async def persist(  # noqa: ANN001
        self, *, subject_id, session_id, turn_index, user_text, assistant_text,
        query_vec, run_id, trace_id,
    ) -> None:
        self.persist_calls.append(
            {"user_text": user_text, "assistant_text": assistant_text}
        )


@pytest.mark.asyncio
async def test_memory_query_routes_to_memory_specialist(make_deps):
    deps = make_deps(propose_tool=True, high_risk=True)  # tools WOULD gate on the qa path
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

    # Capture the extra_context handed to the prompt so we can prove the answer is
    # grounded in the recalled facts (memory fed generation), not RAG.
    seen_extra: list[str | None] = []
    base_render = deps.render_system_prompt

    def render_system_prompt(persona, extra_context=None):  # noqa: ANN001
        seen_extra.append(extra_context)
        return base_render(persona, extra_context)

    deps.render_system_prompt = render_system_prompt

    events = [
        e
        async for e in run_agent(
            "what do you know about me?",
            persona="operations_lead",
            deps=deps,
            session_id="sess-9",
            memory_subject="user:42",
        )
    ]
    types = [e.type for e in events]

    # Routed to the memory specialist — the visible, auditable hand-off.
    routing = next(e for e in events if e.type == "routing")
    assert routing.role == "memory"
    assert routing.used_llm is False

    # Distinct behaviour: it recalled memory and answered from it, skipping RAG + tools.
    assert fake_mem.assemble_calls  # the memory subsystem was consulted
    assert "retrieval" not in types  # RAG did NOT run
    assert "tool_call" not in types  # no tool/gate path (would have gated on qa)

    # The glass-box memory event carries the recalled counts.
    mem_event = next(e for e in events if e.type == "memory")
    assert mem_event.recalled_fact_count == 2
    assert mem_event.recalled_message_count == 1

    # The recalled block was fed into generation (the answer is grounded in memory).
    assert any(extra == fake_mem.assembled.text for extra in seen_extra)

    # The turn was still persisted, and the run completes cleanly.
    assert len(fake_mem.persist_calls) == 1
    assert fake_mem.persist_calls[0]["user_text"] == "what do you know about me?"
    assert types[-1] == "run_finished"


@pytest.mark.asyncio
async def test_memory_specialist_honest_when_nothing_stored(make_deps):
    """No memory deps → the specialist still answers (honestly empty), no crash."""
    deps = make_deps(propose_tool=False)  # deps.memory stays None
    assert deps.memory is None

    events = [
        e
        async for e in run_agent(
            "what do you remember about me?",
            deps=deps,
            session_id="sess-x",
            memory_subject="user:1",
        )
    ]
    types = [e.type for e in events]

    assert next(e for e in events if e.type == "routing").role == "memory"
    assert "retrieval" not in types
    assert "tool_call" not in types
    # A memory event is still emitted (all-zero counts) and an answer is produced.
    mem_event = next(e for e in events if e.type == "memory")
    assert mem_event.recalled_fact_count == 0
    assert "token" in types
    assert types[-1] == "run_finished"
