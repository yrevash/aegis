"""Tests for the supervisor router — deterministic classifier + the memory specialist.

Two layers, both offline (fakes only):

1. **Classifier mechanism** (``aegis.agent.router``): the deterministic keyword pass
   picks the right role with NO model call for clear cases; an unmatched query falls
   through to the roster default; a genuine tie between two named specialists is the
   ONLY thing that escalates to the cheap-LLM tiebreak, and an inconclusive reply
   defaults to the roster default.
2. **End-to-end routing** (``run_agent``): a "what do you know about me" query routes to
   ``memory``, emits the ``routing`` hand-off, answers from recalled memory, and never
   touches retrieval / the tool+gate path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace

import pytest

from aegis.agent import RouterDecision, load_roster, run_agent
from aegis.agent.router import classify_deterministic, route_query
from aegis.core.models import ModelRole

from .conftest import fake_roster


class _BoomComplete:
    """A ``complete`` that fails loudly if the router ever calls it (proves 'no LLM')."""

    def __init__(self) -> None:
        self.calls = 0

    async def __call__(self, role, messages, **kwargs):  # noqa: ANN001, ANN003
        self.calls += 1
        raise AssertionError("router should not have called the LLM for a clear case")


@dataclass(frozen=True)
class _Spec:
    role: str
    description: str = ""
    keywords: tuple[str, ...] = ()
    is_default: bool = False


@dataclass(frozen=True)
class _AmbiguousRoster:
    """A roster where two named specialists share a keyword, forcing a tie."""

    _specs: tuple[_Spec, ...] = field(
        default=(
            _Spec("qa", "default", is_default=True),
            _Spec("alpha", "A", ("overlap",)),
            _Spec("beta", "B", ("overlap",)),
        )
    )

    @property
    def default_role(self) -> str:
        return "qa"

    @property
    def specialists(self) -> tuple[_Spec, ...]:
        return self._specs

    def roles(self) -> list[str]:
        return [s.role for s in self._specs]

    def named(self) -> list[_Spec]:
        return [s for s in self._specs if s.keywords]


# ── 1. Deterministic classifier ───────────────────────────────────────────────


def test_deterministic_picks_memory_for_self_referential_query():
    role, reason = classify_deterministic("what do you know about me?", fake_roster())
    assert role == "memory"
    assert "memory" in reason


def test_deterministic_defaults_to_qa_when_nothing_matches():
    role, reason = classify_deterministic("what is the escalation policy?", fake_roster())
    assert role == "qa"
    assert "no specialist keywords matched" in reason


@pytest.mark.asyncio
async def test_route_query_clear_case_uses_no_llm():
    boom = _BoomComplete()
    decision = await route_query("what do you remember about me?", fake_roster(), complete=boom)

    assert isinstance(decision, RouterDecision)
    assert decision.role == "memory"
    assert decision.used_llm is False
    assert boom.calls == 0


def test_ambiguous_tie_is_reported_by_deterministic_pass():
    role, reason = classify_deterministic("this has overlap", _AmbiguousRoster())
    assert role is None
    assert "ambiguous" in reason


@pytest.mark.asyncio
async def test_ambiguity_without_model_falls_back_to_qa():
    decision = await route_query("this has overlap", _AmbiguousRoster(), complete=None)
    assert decision.role == "qa"
    assert decision.used_llm is False


@pytest.mark.asyncio
async def test_ambiguity_uses_cheap_llm_tiebreak():
    picked: list = []

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        picked.append(role)
        return SimpleNamespace(content="beta")

    decision = await route_query("this has overlap", _AmbiguousRoster(), complete=complete)
    assert decision.role == "beta"
    assert decision.used_llm is True
    assert picked == [ModelRole.CHEAP]


@pytest.mark.asyncio
async def test_inconclusive_tiebreak_defaults_to_qa():
    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        return SimpleNamespace(content="not-a-real-role")

    decision = await route_query("this has overlap", _AmbiguousRoster(), complete=complete)
    assert decision.role == "qa"
    assert decision.used_llm is True


def test_core_load_roster_is_qa_only_fallback():
    # The core's own load_roster is the defensive qa-only fallback; the adapter-backed
    # roster is injected host-side via deps.agent_roster.
    roster = load_roster()
    assert roster.roles() == ["qa"]
    assert roster.default_role == "qa"


# ── 2. End-to-end: memory specialist answers from memory, skipping RAG + tools ─


class _FakeMemory:
    """Recording stand-in for the concrete MemoryDeps (duck-typed to the Protocol)."""

    def __init__(self) -> None:
        self.assembled = SimpleNamespace(
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
    deps = make_deps(propose_tool=True, high_risk=True)  # tools WOULD gate on qa path
    fake_mem = _FakeMemory()
    deps.memory = fake_mem

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
    types = [e["type"] for e in events]

    routing = next(e for e in events if e["type"] == "routing")
    assert routing["role"] == "memory"
    assert routing["used_llm"] is False

    assert fake_mem.assemble_calls
    assert "retrieval" not in types
    assert "tool_call" not in types

    mem_event = next(e for e in events if e["type"] == "memory")
    assert mem_event["recalled_fact_count"] == 2
    assert mem_event["recalled_message_count"] == 1

    assert any(extra == fake_mem.assembled.text for extra in seen_extra)

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
            persona="operations_lead",
            deps=deps,
            session_id="sess-x",
            memory_subject="user:1",
        )
    ]
    types = [e["type"] for e in events]

    assert next(e for e in events if e["type"] == "routing")["role"] == "memory"
    assert "retrieval" not in types
    assert "tool_call" not in types
    mem_event = next(e for e in events if e["type"] == "memory")
    assert mem_event["recalled_fact_count"] == 0
    assert "token" in types
    assert types[-1] == "run_finished"


# ── 3. Keyword matching and the tiebreak must not be fooled by free text ──


@dataclass(frozen=True)
class _SubstringRoster:
    """A roster whose hints are short words that occur INSIDE unrelated longer words."""

    _specs: tuple[_Spec, ...] = field(
        default=(
            _Spec("qa", "default", is_default=True),
            _Spec("memory", "recall", ("memory", "remember")),
            _Spec("billing", "invoices", ("bill", "invoice")),
        )
    )

    @property
    def default_role(self) -> str:
        return "qa"

    @property
    def specialists(self) -> tuple[_Spec, ...]:
        return self._specs

    def roles(self) -> list[str]:
        return [s.role for s in self._specs]

    def named(self) -> list[_Spec]:
        return [s for s in self._specs if s.keywords]


@pytest.mark.parametrize(
    "query",
    [
        "is this a memoryless markov process",
        "the billboard campaign results",
    ],
    ids=["memory-in-memoryless", "bill-in-billboard"],
)
def test_keyword_hits_respect_word_boundaries(query: str):
    """REGRESSION: a raw substring hit made 'memory' match 'memoryless' and 'bill'
    match 'billboard', so a specialist could win on a word it has nothing to do with."""
    role, reason = classify_deterministic(query, _SubstringRoster())
    assert role == "qa"
    assert "no specialist keywords matched" in reason


def test_a_real_word_still_matches():
    """Control: boundary matching must not break the genuine hit."""
    role, _ = classify_deterministic("what do you remember about me", _SubstringRoster())
    assert role == "memory"


def test_a_duplicated_hint_does_not_inflate_a_specialists_score():
    """A roster listing the same phrase twice must not out-score one listing it once."""

    @dataclass(frozen=True)
    class _DupRoster:
        _specs: tuple[_Spec, ...] = field(
            default=(
                _Spec("qa", "default", is_default=True),
                _Spec("alpha", "A", ("overlap", "overlap")),
                _Spec("beta", "B", ("overlap",)),
            )
        )

        @property
        def default_role(self) -> str:
            return "qa"

        @property
        def specialists(self) -> tuple[_Spec, ...]:
            return self._specs

        def roles(self) -> list[str]:
            return [s.role for s in self._specs]

        def named(self) -> list[_Spec]:
            return [s for s in self._specs if s.keywords]

    role, reason = classify_deterministic("this has overlap", _DupRoster())
    assert role is None and "ambiguous" in reason


@pytest.mark.asyncio
async def test_tiebreak_does_not_pick_an_explicitly_rejected_role():
    """REGRESSION: the tiebreak substring-scanned the roster IN ORDER, so a reply of
    "not qa — use memory" returned ``qa`` (the rejected role, because it was first)."""

    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        return SimpleNamespace(content="not alpha — use beta")

    decision = await route_query("this has overlap", _AmbiguousRoster(), complete=complete)
    assert decision.role != "alpha"
    # Naming two roles is a non-answer, so the router falls back to the default.
    assert decision.role == "qa"
    assert decision.used_llm is True


@pytest.mark.asyncio
async def test_tiebreak_accepts_a_bare_role_id_however_it_is_punctuated():
    for reply in ("beta", " beta ", '"beta"', "beta."):
        async def complete(role, messages, _reply=reply, **kwargs):  # noqa: ANN001, ANN003
            return SimpleNamespace(content=_reply)

        decision = await route_query(
            "this has overlap", _AmbiguousRoster(), complete=complete
        )
        assert decision.role == "beta", reply


@pytest.mark.asyncio
async def test_tiebreak_accepts_a_single_role_mentioned_in_a_sentence():
    async def complete(role, messages, **kwargs):  # noqa: ANN001, ANN003
        return SimpleNamespace(content="I would route this to beta.")

    decision = await route_query("this has overlap", _AmbiguousRoster(), complete=complete)
    assert decision.role == "beta"
