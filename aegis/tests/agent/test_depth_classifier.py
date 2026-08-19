"""The depth classifier: how WIDE a turn runs, and who got to decide.

Two properties are pinned here, and they are the two the budget depends on:

* **SINGLE on every failure path.** A classifier that quietly fans out is the failure
  $100 of gateway credit cannot absorb, so every way this code can go wrong — no
  policy, no model, a model that answers in prose, a model that raises, a predicate
  that explodes — resolves to one lane.
* **The user's width is the user's decision.** In an explicit mode the classifier is
  *skipped*, not overruled afterwards, and the only thing that may touch an explicit
  width is the platform narrowing it. The user can never widen past the cap.
"""

from __future__ import annotations

import pytest

from aegis.agent.router import (
    Depth,
    DepthDecision,
    DepthMode,
    DepthPolicy,
    RouterDecision,
    decide_depth,
)
from aegis.gateway.types import LLMResult, Usage

pytestmark = pytest.mark.anyio

_POLICY = DepthPolicy(available_agents=4)


def _model(content: str):
    """A cheap-model double returning ``content`` and counting its calls."""
    calls: list[str] = []

    async def complete(role, messages, **_kw):  # noqa: ANN001, ARG001
        calls.append(str(role))
        return LLMResult(content=content, tool_calls=[], usage=Usage(), model="fake")

    return complete, calls


# ── Deterministic first ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "query",
    [
        "What is my remaining budget?",
        "resolve R1",
        "who raised this request",
        "status of R42?",
    ],
)
async def test_short_single_intent_queries_never_fan_out(query):
    complete, calls = _model("4")
    decision = await decide_depth(query, policy=_POLICY, complete=complete)
    assert decision.depth is Depth.SINGLE
    assert decision.fanout == 0
    assert decision.reason
    assert calls == [], "a plainly single-intent query must not pay for a model call"


@pytest.mark.parametrize(
    "query",
    [
        "Compare our escalation policy with the new regulation and tell me what changed.",
        "What is our SLA? And how many requests breached it last month?",
        "Summarise the policy; then tell me which clients are affected.",
    ],
)
async def test_explicitly_multipart_queries_fan_out_deterministically(query):
    complete, calls = _model("1")
    decision = await decide_depth(query, policy=_POLICY, complete=complete)
    assert decision.depth is Depth.TEAM
    assert 2 <= decision.fanout <= 4
    assert calls == [], "an obviously multi-part query must not pay for a model call"


async def test_a_narrow_specialist_turn_answers_in_one_pass():
    """A turn already routed to e.g. the memory specialist has nothing to fan out."""
    decision = await decide_depth(
        "compare what you remember about me and what I told you last week and summarise",
        policy=_POLICY,
        role_is_default=False,
    )
    assert decision.depth is Depth.SINGLE
    assert "one pass" in decision.reason


# ── The ambiguous band: exactly one cheap call, and it may not be trusted ─────

_AMBIGUOUS = (
    "I would like a considered overview of how our current operational posture has "
    "been developing across the last several reporting periods please"
)


async def test_the_ambiguous_band_spends_exactly_one_cheap_call():
    complete, calls = _model("3")
    decision = await decide_depth(_AMBIGUOUS, policy=_POLICY, complete=complete)
    assert decision.depth is Depth.TEAM
    assert decision.fanout == 3
    assert decision.used_llm
    assert calls == ["cheap"], "the width classifier must cost exactly one cheap call"


async def test_a_prose_reply_from_the_classifier_is_a_non_answer_not_a_fanout():
    complete, _ = _model("It depends on what you mean by independent.")
    decision = await decide_depth(_AMBIGUOUS, policy=_POLICY, complete=complete)
    assert decision.depth is Depth.SINGLE
    assert decision.used_llm


async def test_a_classifier_that_raises_defaults_to_single():
    async def boom(*_a, **_k):
        raise RuntimeError("cheap deployment down")

    decision = await decide_depth(_AMBIGUOUS, policy=_POLICY, complete=boom)
    assert decision.depth is Depth.SINGLE
    assert decision.reason


async def test_no_classifier_model_defaults_to_single():
    decision = await decide_depth(_AMBIGUOUS, policy=_POLICY, complete=None)
    assert decision.depth is Depth.SINGLE
    assert not decision.used_llm


async def test_no_policy_at_all_defaults_to_single():
    decision = await decide_depth(_AMBIGUOUS, policy=None)
    assert decision.depth is Depth.SINGLE
    assert decision.decided_by == "tenant_default"


async def test_the_classifier_reply_is_clamped_to_the_cap():
    complete, _ = _model("11")
    decision = await decide_depth(
        _AMBIGUOUS, policy=DepthPolicy(max_parallel_agents=3, available_agents=4),
        complete=complete,
    )
    assert decision.fanout == 3


# ── Manual wins ──────────────────────────────────────────────────────────────


async def test_an_explicit_single_skips_the_classifier_entirely():
    complete, calls = _model("4")
    decision = await decide_depth(
        "compare A and B and C and tell me everything",
        policy=DepthPolicy(mode=DepthMode.SINGLE, available_agents=4),
        complete=complete,
    )
    assert decision.depth is Depth.SINGLE
    assert decision.decided_by == "user"
    assert calls == [], "Fast must not pay for the model call it is trying to avoid"


async def test_an_explicit_team_on_a_trivial_query_is_honoured():
    complete, calls = _model("1")
    decision = await decide_depth(
        "hi",
        policy=DepthPolicy(mode=DepthMode.TEAM, available_agents=4),
        complete=complete,
    )
    assert decision.depth is Depth.TEAM
    assert decision.fanout == 4
    assert decision.decided_by == "user"
    assert calls == []


async def test_the_platform_may_narrow_an_explicit_width_but_the_user_may_not_widen_it():
    decision = await decide_depth(
        "anything",
        policy=DepthPolicy(
            mode=DepthMode.TEAM,
            requested_fanout=9,
            max_parallel_agents=3,
            available_agents=4,
        ),
    )
    assert decision.fanout == 3
    assert decision.decided_by == "platform_cap"
    assert "narrowed" in decision.reason


async def test_a_roster_smaller_than_the_cap_is_the_real_ceiling():
    decision = await decide_depth(
        "anything",
        policy=DepthPolicy(
            mode=DepthMode.TEAM, requested_fanout=4, max_parallel_agents=4,
            available_agents=2,
        ),
    )
    assert decision.fanout == 2


async def test_team_disabled_beats_everything_including_an_explicit_request():
    decision = await decide_depth(
        "compare A and B and tell me C",
        policy=DepthPolicy(mode=DepthMode.TEAM, team_enabled=False, available_agents=4),
    )
    assert decision.depth is Depth.SINGLE
    assert decision.decided_by == "tenant_default"


async def test_no_roster_is_reported_as_no_team_not_as_disabled():
    """Two different facts; a trace that conflates them sends you to the wrong page."""
    decision = await decide_depth(
        "compare A and B and tell me C",
        policy=DepthPolicy(available_agents=0),
    )
    assert decision.depth is Depth.SINGLE
    assert decision.decided_by == "tenant_default"
    assert "no sub-agent team" in decision.reason


# ── The hand-off object carries the width ────────────────────────────────────


def test_router_decision_defaults_to_single_with_no_width_decision():
    decision = RouterDecision(role="qa", reason="default")
    assert decision.depth is Depth.SINGLE
    assert decision.fanout == 0
    assert decision.decided_by == "auto"


def test_with_depth_folds_both_reasons_into_the_one_visible_line():
    merged = RouterDecision(role="qa", reason="no specialist keywords matched").with_depth(
        DepthDecision(
            depth=Depth.TEAM, fanout=3, reason="3 sub-questions detected",
            decided_by="auto", used_llm=True,
        )
    )
    assert merged.depth is Depth.TEAM
    assert merged.fanout == 3
    assert merged.used_llm
    assert "no specialist keywords matched" in merged.reason
    assert "3 sub-questions detected" in merged.reason
