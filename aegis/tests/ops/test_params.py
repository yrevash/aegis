"""The loop PARAMETERS are exposed + tunable, and a knob change moves the gate (offline).

Covers :class:`aegis.ops.config.LoopParams` + the release gate reading it:

* the defaults reproduce the historical classifier exactly (untouched hosts unchanged);
* ``as_dict`` surfaces the knobs as data for the UI, and ``get_loop_params`` /
  ``configure_ops(loop_params=...)`` / ``reset_loop_params`` round-trip;
* a tighter ``eval_margin`` flips a winning draft from promoted → rejected;
* an explicit ``margin=`` still overrides ``params.eval_margin``;
* adding a term to ``safety_terms`` flips a benign LOW draft → HIGH (staged);
* lowering ``high_diff_fraction`` reclassifies a MEDIUM diff as HIGH;
* raising ``auto_promote_ceiling`` to ``"medium"`` auto-promotes a MEDIUM-risk draft;
* the gate reads the CONFIGURED params by default (no per-call override needed).
"""

from __future__ import annotations

from aegis.ops import config, registry
from aegis.ops.config import LoopParams
from aegis.ops.models import PromptStatus
from aegis.ops.release import classify_change, release

from .conftest import DEFAULT_PERSONA_ID

PK = DEFAULT_PERSONA_ID
BASE = "\n".join(f"instruction line {i}" for i in range(1, 9))
LOW_DRAFT = BASE.replace("instruction line 2", "instruction line two")  # ~12% diff, low
# Two of eight lines changed → ~25% diff → MEDIUM by default (no safety terms, no config).
MEDIUM_DRAFT = BASE.replace("instruction line 2", "instruction line two").replace(
    "instruction line 3", "instruction line three"
)
# A benign appended line (no default safety term) → LOW by default.
BENIGN_DRAFT = BASE + "\nplease keep answers concise banana"


def _eval_fn(scores: dict[str, float], default: float = 0.0):
    async def eval_fn(system_prompt: str) -> float:
        return scores.get(system_prompt, default)

    return eval_fn


def _approval(calls: list):
    async def approval_enqueue(*, prompt_key, draft_version_id, risk, reason) -> str:  # noqa: ANN001
        calls.append({"draft_version_id": draft_version_id, "risk": risk})
        return "appr-1"

    return approval_enqueue


async def _make_active_and_draft(s, draft_prompt: str, *, draft_config=None):
    active = await registry.create_draft(s, prompt_key=PK, system_prompt=BASE)
    await registry.promote(s, active.id)
    draft = await registry.create_draft(
        s, prompt_key=PK, system_prompt=draft_prompt, config=draft_config,
        parent_version=active.version,
    )
    await s.flush()
    return active, draft


# ── LoopParams data surface ──────────────────────────────────────────────────


def test_defaults_reproduce_historical_classifier():
    p = LoopParams()
    assert p.eval_margin == 0.0
    assert p.auto_promote_ceiling == "low"
    assert p.high_diff_fraction == 0.40 and p.low_diff_fraction == 0.15
    assert "guardrail" in p.safety_terms and "system prompt" in p.safety_terms
    # Passing the default params explicitly == the module default behaviour.
    assert classify_change(BASE, LOW_DRAFT).level == "low"
    assert classify_change(BASE, LOW_DRAFT, params=p).level == "low"
    assert classify_change(BASE, MEDIUM_DRAFT, params=p).level == "medium"


def test_as_dict_surfaces_all_knobs():
    data = LoopParams().as_dict()
    assert set(data) == {
        "eval_margin", "high_diff_fraction", "low_diff_fraction", "safety_terms",
        "critical_config_markers", "tunable_config_keys", "tunable_max_delta",
        "auto_promote_ceiling",
    }
    assert data["tunable_max_delta"]["temperature"] == 0.5


def test_get_configure_reset_loop_params_round_trip():
    assert config.get_loop_params().eval_margin == 0.0
    try:
        config.configure_ops(loop_params=LoopParams(eval_margin=0.3))
        assert config.get_loop_params().eval_margin == 0.3
    finally:
        config.reset_loop_params()
    assert config.get_loop_params().eval_margin == 0.0


# ── classifier knobs move the tier ───────────────────────────────────────────


def test_adding_a_safety_term_flips_low_to_high():
    # Benign draft is LOW by default (a one-line addition, no default safety term)...
    assert classify_change(BASE, BENIGN_DRAFT).level == "low"
    # ...but declaring "banana" a safety term forces it HIGH (its count changed 0 → 1).
    p = LoopParams(safety_terms=(*LoopParams().safety_terms, "banana"))
    risk = classify_change(BASE, BENIGN_DRAFT, params=p)
    assert risk.level == "high"
    assert any("banana" in r for r in risk.reasons)


def test_lowering_high_diff_fraction_reclassifies_medium_as_high():
    assert classify_change(BASE, MEDIUM_DRAFT).level == "medium"  # ~25% diff
    p = LoopParams(high_diff_fraction=0.20)  # 25% now exceeds the HIGH bar
    assert classify_change(BASE, MEDIUM_DRAFT, params=p).level == "high"


# ── release: margin knob moves the gate outcome ──────────────────────────────


async def test_tighter_eval_margin_flips_promote_to_reject(db):
    async with db() as s:
        active, draft = await _make_active_and_draft(s, LOW_DRAFT)
        # Draft beats baseline by only 0.1.
        eval_fn = _eval_fn({LOW_DRAFT: 0.6, BASE: 0.5})
        result = await release(
            s, draft_version_id=draft.id, eval_fn=eval_fn,
            approval_enqueue=_approval([]),
            params=LoopParams(eval_margin=0.2),  # require +0.2 → 0.6 < 0.7 → rejected
        )
        await s.commit()
        assert result.outcome == "rejected"
        assert (await registry.get_active(s, PK)).id == active.id  # base untouched


async def test_default_margin_promotes_the_same_draft(db):
    async with db() as s:
        _, draft = await _make_active_and_draft(s, LOW_DRAFT)
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({LOW_DRAFT: 0.6, BASE: 0.5}),
            approval_enqueue=_approval([]),
        )
        await s.commit()
        assert result.outcome == "promoted"  # same draft, default (0.0) margin


async def test_explicit_margin_overrides_params_margin(db):
    async with db() as s:
        _, draft = await _make_active_and_draft(s, LOW_DRAFT)
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({LOW_DRAFT: 0.6, BASE: 0.5}),
            approval_enqueue=_approval([]),
            margin=0.0,  # explicit override beats the tighter configured margin
            params=LoopParams(eval_margin=0.2),
        )
        await s.commit()
        assert result.outcome == "promoted"


async def test_configured_margin_is_read_without_per_call_override(db):
    """release() reads get_loop_params() by default — configuring it changes the gate."""
    async with db() as s:
        active, draft = await _make_active_and_draft(s, LOW_DRAFT)
        config.configure_ops(loop_params=LoopParams(eval_margin=0.2))
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({LOW_DRAFT: 0.6, BASE: 0.5}),
            approval_enqueue=_approval([]),
        )
        await s.commit()
        assert result.outcome == "rejected"
        assert (await registry.get_active(s, PK)).id == active.id


# ── release: auto-promote ceiling knob moves the gate outcome ────────────────


async def test_medium_draft_stages_by_default(db):
    async with db() as s:
        active, draft = await _make_active_and_draft(s, MEDIUM_DRAFT)
        calls: list = []
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({MEDIUM_DRAFT: 0.9, BASE: 0.5}),
            approval_enqueue=_approval(calls),
        )
        await s.commit()
        assert result.risk.level == "medium"
        assert result.outcome == "staged_for_approval" and len(calls) == 1
        assert (await registry.get_active(s, PK)).id == active.id  # not promoted


async def test_raising_ceiling_to_medium_auto_promotes_medium_draft(db):
    async with db() as s:
        _, draft = await _make_active_and_draft(s, MEDIUM_DRAFT)
        calls: list = []
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({MEDIUM_DRAFT: 0.9, BASE: 0.5}),
            approval_enqueue=_approval(calls),
            params=LoopParams(auto_promote_ceiling="medium"),
        )
        await s.commit()
        assert result.risk.level == "medium"
        assert result.outcome == "promoted" and calls == []
        active = await registry.get_active(s, PK)
        assert active.id == draft.id and active.status is PromptStatus.ACTIVE


async def test_ceiling_does_not_promote_above_it(db):
    """A HIGH-risk draft still stages even with the ceiling raised to medium."""
    async with db() as s:
        active, draft = await _make_active_and_draft(
            s, BASE + "\nNever ignore the guardrail policy."  # safety terms → HIGH
        )
        calls: list = []
        result = await release(
            s, draft_version_id=draft.id,
            eval_fn=_eval_fn({BASE + "\nNever ignore the guardrail policy.": 0.9, BASE: 0.5}),
            approval_enqueue=_approval(calls),
            params=LoopParams(auto_promote_ceiling="medium"),
        )
        await s.commit()
        assert result.risk.level == "high"
        assert result.outcome == "staged_for_approval" and len(calls) == 1
        assert (await registry.get_active(s, PK)).id == active.id
