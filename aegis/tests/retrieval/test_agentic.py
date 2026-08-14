"""Tests for the bounded agentic retrieval loop (offline fakes only)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from aegis.retrieval.agentic import (
    Sufficiency,
    agentic_retrieve,
    assess_sufficiency,
)
from aegis.retrieval.models import (
    ArmReport,
    GraphDelta,
    Provenance,
    RerankReport,
    RetrievalObservability,
    RetrievalResult,
    Source,
)
from aegis.retrieval.query_rewrite import CallUsage, RewriteResult, rewrite_query
from aegis.retrieval.spotlight import DATAMARK_TOKEN
from aegis.retrieval.types import GraphEdge, GraphNode, RetrievalOrigin

# Per-call usage every fake judge/rewrite response reports, so accrual is observable.
_USAGE = SimpleNamespace(prompt_tokens=5, completion_tokens=3, cost_usd=0.0001)


class QueuedComplete:
    """Async ``complete`` fake that returns canned ``.content`` strings in order.

    Each response also carries a ``.usage`` (mirroring ``LLMResult``) so the loop's
    usage-summing can be asserted; the deterministic no-judge path makes no call.
    """

    def __init__(self, *contents: str):
        self._contents = list(contents)
        self.calls: list[dict] = []

    async def __call__(self, role, messages, *, temperature=0.0, response_format=None):
        self.calls.append({"role": role, "response_format": response_format})
        content = self._contents[min(len(self.calls) - 1, len(self._contents) - 1)]
        return SimpleNamespace(content=content, usage=_USAGE)


class MappedRetrieve:
    """Async ``retrieve_fn`` fake mapping query -> a canned ``RetrievalResult``."""

    def __init__(self, mapping: dict[str, RetrievalResult]):
        self._mapping = mapping
        self.calls: list[str] = []

    async def __call__(self, query, *, persona=None):
        self.calls.append(query)
        return self._mapping[query]


def _result(sources: list[Source], *, num_candidates: int) -> RetrievalResult:
    context = "\n".join(s.text for s in sources)
    return RetrievalResult(
        answer_context=context, sources=sources, num_candidates=num_candidates
    )


# ── assess_sufficiency ────────────────────────────────────────────────────────


async def test_assess_reports_sufficient():
    fake = QueuedComplete('{"sufficient": true, "reason": "covered", "followup_query": null}')
    out = await assess_sufficiency("q", "some context", complete=fake)

    assert isinstance(out, Sufficiency)
    assert out.sufficient is True
    assert out.followup_query is None
    assert fake.calls[0]["response_format"] == {"type": "json_object"}
    # The judge call's token spend is reported on the verdict.
    assert out.usage.prompt_tokens == 5
    assert out.usage.completion_tokens == 3


async def test_assess_reports_insufficient_with_followup():
    fake = QueuedComplete(
        '{"sufficient": false, "reason": "missing dates", "followup_query": "when did it happen"}'
    )
    out = await assess_sufficiency("q", "partial context", complete=fake)

    assert out.sufficient is False
    assert out.followup_query == "when did it happen"


async def test_assess_fallback_without_judge():
    # No judge: non-empty context is honestly treated as sufficient, empty as not.
    yes = await assess_sufficiency("q", "non-empty context", complete=None)
    no = await assess_sufficiency("q", "   ", complete=None)

    assert yes.sufficient is True and yes.followup_query is None
    assert no.sufficient is False
    # No judge call was made, so no spend is reported.
    assert yes.usage.prompt_tokens == 0 and yes.usage.cost_usd == 0.0


# ── agentic_retrieve ──────────────────────────────────────────────────────────


async def test_loop_stops_on_first_round_sufficiency():
    r1 = _result([Source(id="a", text="alpha", score=0.9)], num_candidates=5)
    retrieve = MappedRetrieve({"start": r1})
    complete = QueuedComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')

    out = await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete)

    assert out.used_rounds == 1
    assert len(out.rounds) == 1
    assert retrieve.calls == ["start"]
    assert out.result is r1


async def test_loop_respects_max_rounds():
    r1 = _result([Source(id="a", text="alpha", score=0.5)], num_candidates=3)
    r2 = _result([Source(id="b", text="beta", score=0.5)], num_candidates=4)
    retrieve = MappedRetrieve({"start": r1, "more": r2})
    # Always insufficient; the loop must still stop at max_rounds=2.
    complete = QueuedComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": "more"}'
    )

    out = await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete, max_rounds=2)

    assert out.used_rounds == 2
    assert len(out.rounds) == 2
    assert retrieve.calls == ["start", "more"]
    # Two judge calls (one per round); their usage is summed for the run's telemetry.
    assert out.usage.prompt_tokens == 10
    assert out.usage.completion_tokens == 6
    assert out.usage.cost_usd == 0.0002


async def test_loop_merges_and_dedupes_sources_across_rounds():
    r1 = _result(
        [
            Source(id="a", text="alpha", score=0.5),
            Source(id="b", text="beta-low", score=0.4),
            Source(id="d", text="delta", score=0.2),
        ],
        num_candidates=6,
    )
    r2 = _result(
        [
            Source(id="b", text="beta-high", score=0.9),  # duplicate id, higher score
            Source(id="c", text="gamma", score=0.3),
        ],
        num_candidates=4,
    )
    retrieve = MappedRetrieve({"start": r1, "q2": r2})
    complete = QueuedComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": "q2"}',
        '{"sufficient": true, "reason": "now enough", "followup_query": null}',
    )

    out = await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete, max_rounds=2)

    merged = out.result.sources
    by_id = {s.id: s for s in merged}
    # Deduped by id: 'b' appears exactly once, keeping the HIGHER score / its text.
    assert [s.id for s in merged].count("b") == 1
    assert by_id["b"].score == 0.9
    assert by_id["b"].text == "beta-high"
    # Cap = len(round-1 sources) = 3 -> top-3 by score, lowest ('d', 0.2) dropped.
    assert len(merged) == 3
    assert "d" not in by_id
    assert {"a", "b", "c"} == set(by_id)
    # num_candidates is the honest sum across rounds.
    assert out.result.num_candidates == 10
    # answer_context was rebuilt from the merged sources.
    assert "beta-high" in out.result.answer_context
    assert "beta-low" not in out.result.answer_context


async def test_loop_uses_fallback_followup_when_judge_gives_none():
    r1 = _result([Source(id="a", text="alpha", score=0.5)], num_candidates=3)
    fallback_q = "more detail and specific facts about: start"
    r2 = _result([Source(id="b", text="beta", score=0.6)], num_candidates=2)
    retrieve = MappedRetrieve({"start": r1, fallback_q: r2})
    complete = QueuedComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": null}',
        '{"sufficient": true, "reason": "enough", "followup_query": null}',
    )

    out = await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete, max_rounds=2)

    assert retrieve.calls == ["start", fallback_q]
    assert out.used_rounds == 2


async def test_loop_usage_sums_rewrite_and_judge_calls():
    # A rewrite (usage 7/4) + a single sufficient judge call (usage 5/3) → summed spend.
    r1 = _result([Source(id="a", text="alpha", score=0.9)], num_candidates=5)
    retrieve = MappedRetrieve({"rewritten start": r1})
    complete = QueuedComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')

    async def rewrite_fn(q, *, history=None):  # noqa: ANN001
        return RewriteResult(
            original=q,
            rewritten="rewritten start",
            changed=True,
            reason="resolved",
            usage=CallUsage(prompt_tokens=7, completion_tokens=4, cost_usd=0.0003),
        )

    out = await agentic_retrieve(
        "start", retrieve_fn=retrieve, complete=complete, rewrite_fn=rewrite_fn
    )

    # Retrieval used the rewritten query, and usage = rewrite(7/4) + judge(5/3).
    assert retrieve.calls == ["rewritten start"]
    assert out.usage.prompt_tokens == 12
    assert out.usage.completion_tokens == 7
    assert out.usage.cost_usd == pytest.approx(0.0004)


# ── the rewriter actually sees the conversation ───────────────────────────────


class PronounResolvingComplete:
    """A rewriter fake that can only resolve "it" if the history reaches the prompt.

    It reads the CONVERSATION block the real :func:`rewrite_query` renders: when the
    prior turns are there it resolves the pronoun against them; when the block says
    "(no prior conversation)" it honestly returns the turn unchanged — exactly what a
    real model would do. So the assertion below is a test of the *plumbing*, not of a
    fake that always answers correctly.
    """

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def __call__(self, role, messages, *, temperature=0.0, response_format=None):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        conversation = prompt.split("LATEST TURN:")[0]
        subject = "Neo4j" if "Neo4j" in conversation else None
        rewritten = (
            f"what licence does {subject} use" if subject else "what licence does it use"
        )
        return SimpleNamespace(
            content=json.dumps({"rewritten": rewritten, "reason": "resolved"}),
            usage=_USAGE,
        )


async def test_rewrite_resolves_a_pronoun_against_the_conversation_history():
    # REGRESSION: the loop called ``rewrite_fn(query, history=None)``, which overrode
    # the caller's bound history — the rewriter always saw "(no prior conversation)",
    # so the one thing it exists for (pronouns, ellipsis, back-references) never
    # happened. History must reach the rewriter.
    history = [
        {"role": "user", "content": "Which store holds the knowledge graph?"},
        {"role": "assistant", "content": "Neo4j holds it."},
    ]
    resolved = "what licence does Neo4j use"
    r1 = _result([Source(id="a", text="alpha", score=0.9)], num_candidates=5)
    retrieve = MappedRetrieve({resolved: r1})
    rewriter = PronounResolvingComplete()
    judge = QueuedComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')

    async def rewrite_fn(q, *, history=None):  # noqa: ANN001
        return await rewrite_query(q, history=history, complete=rewriter)

    out = await agentic_retrieve(
        "what licence does it use",
        retrieve_fn=retrieve,
        complete=judge,
        rewrite_fn=rewrite_fn,
        history=history,
    )

    # The conversation really reached the rewrite prompt…
    assert "Neo4j holds it." in rewriter.prompts[0]
    assert "(no prior conversation)" not in rewriter.prompts[0]
    # …so the pronoun resolved, and retrieval ran on the standalone query.
    assert retrieve.calls == [resolved]
    assert out.result.observability.rewrite.rewritten == resolved
    assert out.result.observability.rewrite.changed is True


async def test_rewrite_without_history_is_an_honest_no_op():
    # The counterpart: with genuinely no prior turns the rewriter cannot resolve
    # anything, and the loop retrieves the original turn rather than a guess.
    unresolved = "what licence does it use"
    r1 = _result([Source(id="a", text="alpha", score=0.9)], num_candidates=5)
    retrieve = MappedRetrieve({unresolved: r1})
    rewriter = PronounResolvingComplete()
    judge = QueuedComplete('{"sufficient": true, "reason": "ok", "followup_query": null}')

    async def rewrite_fn(q, *, history=None):  # noqa: ANN001
        return await rewrite_query(q, history=history, complete=rewriter)

    out = await agentic_retrieve(
        unresolved, retrieve_fn=retrieve, complete=judge, rewrite_fn=rewrite_fn
    )

    assert "(no prior conversation)" in rewriter.prompts[0]
    assert retrieve.calls == [unresolved]
    assert out.result.observability.rewrite.changed is False


# ── round 2 can actually contribute, and its evidence survives the merge ──────


async def test_round_two_is_not_truncated_away_by_round_ones_size():
    # REGRESSION: the merge cap was round 1's source count, so a small-but-high-scoring
    # first round made the second round structurally incapable of contributing — two
    # judge calls and a retrieval bought a byte-identical result reported as 2 rounds.
    r1 = _result(
        [Source(id="a", text="alpha", score=9.0), Source(id="b", text="beta", score=8.0)],
        num_candidates=4,
    )
    r2 = _result(
        [Source(id=f"n{i}", text=f"new-{i}", score=7.0) for i in range(6)],
        num_candidates=9,
    )
    retrieve = MappedRetrieve({"start": r1, "q2": r2})
    complete = QueuedComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": "q2"}',
        '{"sufficient": true, "reason": "now enough", "followup_query": null}',
    )

    out = await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete)

    ids = [s.id for s in out.result.sources]
    assert len(ids) == 6  # cap = max(2, 6), not round 1's 2
    assert ids[:2] == ["a", "b"]  # round 1 still wins on score
    assert sum(1 for i in ids if i.startswith("n")) == 4  # round 2 genuinely contributed
    assert "new-0" in out.result.answer_context
    # …and the contribution is reported per round, so a useless round would show as 0.
    assert out.rounds[0].new_sources == 2
    assert out.rounds[1].new_sources == 4
    assert out.result.observability.agentic.round_new_sources == [2, 4]


def _round_result(
    sources: list[Source],
    *,
    origins: list[RetrievalOrigin],
    arms: list[ArmReport],
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    num_candidates: int,
    spotlighted: bool,
) -> RetrievalResult:
    """A pipeline-shaped result carrying real provenance/observability for merge tests."""
    return RetrievalResult(
        answer_context="\n".join(s.text for s in sources),
        sources=sources,
        num_candidates=num_candidates,
        graph_delta=GraphDelta(nodes=nodes, edges=edges),
        provenance=Provenance(origins=origins),
        observability=RetrievalObservability(
            arms=arms,
            fused_candidates=num_candidates,
            rerank=RerankReport(ran=True, graded=True, kept=len(sources)),
            spotlight_applied=spotlighted,
        ),
    )


async def _two_round_merge(*, spotlighted: bool):
    """Drive a 2-round loop over two results with distinct provenance; return the merge."""
    r1 = _round_result(
        [Source(id="a", text="alpha", score=9.0)],
        origins=[RetrievalOrigin.VECTOR],
        arms=[ArmReport(origins=[RetrievalOrigin.VECTOR], candidates=3, fired=True)],
        nodes=[GraphNode(id="n1", label="one", kind="entity")],
        edges=[],
        num_candidates=3,
        spotlighted=spotlighted,
    )
    r2 = _round_result(
        [Source(id="b", text="beta", score=8.0), Source(id="c", text="gamma", score=7.5)],
        origins=[RetrievalOrigin.GRAPH, RetrievalOrigin.BM25],
        arms=[
            ArmReport(origins=[RetrievalOrigin.VECTOR], candidates=2, fired=True),
            ArmReport(origins=[RetrievalOrigin.GRAPH], candidates=5, fired=True),
        ],
        nodes=[GraphNode(id="n2", label="two", kind="entity")],
        edges=[GraphEdge(source="n1", target="n2", relation="relates_to")],
        num_candidates=5,
        spotlighted=spotlighted,
    )
    retrieve = MappedRetrieve({"start": r1, "q2": r2})
    complete = QueuedComplete(
        '{"sufficient": false, "reason": "need more", "followup_query": "q2"}',
        '{"sufficient": true, "reason": "now enough", "followup_query": null}',
    )
    return await agentic_retrieve("start", retrieve_fn=retrieve, complete=complete)


async def test_merge_keeps_round_twos_provenance_arms_and_graph_delta():
    # REGRESSION: the merge model_copy'd from round 1, discarding round 2's origins,
    # arms and graph delta — the live graph viz never showed the second hop.
    out = await _two_round_merge(spotlighted=True)
    result = out.result

    assert result.provenance.origins == [
        RetrievalOrigin.VECTOR,
        RetrievalOrigin.GRAPH,
        RetrievalOrigin.BM25,
    ]
    assert {n.id for n in result.graph_delta.nodes} == {"n1", "n2"}
    assert [e.relation for e in result.graph_delta.edges] == ["relates_to"]
    arms = {tuple(a.origins): a.candidates for a in result.observability.arms}
    assert arms[(RetrievalOrigin.VECTOR,)] == 5  # 3 + 2, summed across rounds
    assert arms[(RetrievalOrigin.GRAPH,)] == 5
    assert result.observability.fused_candidates == 8
    assert result.observability.rerank.kept == 2


async def test_merge_respects_the_spotlight_choice_of_the_pipeline():
    # REGRESSION: the merge always spotlighted, so a caller with spotlighting OFF got a
    # spotlighted context back the moment a second round ran — while
    # observability.spotlight_applied still said False.
    off = await _two_round_merge(spotlighted=False)
    assert DATAMARK_TOKEN not in off.result.answer_context
    assert off.result.observability.spotlight_applied is False

    on = await _two_round_merge(spotlighted=True)
    assert DATAMARK_TOKEN in on.result.answer_context
    assert on.result.observability.spotlight_applied is True
