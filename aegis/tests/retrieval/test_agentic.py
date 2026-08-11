"""Tests for the bounded agentic retrieval loop (offline fakes only)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from aegis.retrieval.agentic import (
    Sufficiency,
    agentic_retrieve,
    assess_sufficiency,
)
from aegis.retrieval.models import RetrievalResult, Source
from aegis.retrieval.query_rewrite import CallUsage, RewriteResult

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
