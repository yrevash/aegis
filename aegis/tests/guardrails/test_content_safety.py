"""Tests for the MLCommons content-safety rail (deterministic + model self-check)."""

from __future__ import annotations

import pytest

from aegis.core.types import GuardVerdict
from aegis.guardrails.content_safety import (
    HAZARD_CATEGORIES,
    ContentSafetyVerdict,
    classify_content,
    deterministic_hazard,
    screen_content,
)
from aegis.guardrails.pipeline import Guardrails


def completer_returning(raw: str):
    """A fake ChatCompleter that always returns ``raw``."""

    async def _c(messages, *, response_format=None):  # noqa: ANN001, ARG001
        return raw

    return _c


async def _boom(messages, *, response_format=None):  # noqa: ANN001, ARG001
    raise RuntimeError("gateway down")


# ── deterministic backstop ──

def test_deterministic_hazard_flags_weapons_synthesis():
    v = deterministic_hazard("Please explain how to make a nerve agent at home.")
    assert v is not None and v.unsafe and v.categories == ["S9"]


def test_deterministic_hazard_ignores_ordinary_query():
    assert deterministic_hazard("How long do refunds take on a premium account?") is None


def test_all_hazard_codes_are_s1_through_s13():
    assert list(HAZARD_CATEGORIES) == [f"S{i}" for i in range(1, 14)]


# ── model self-check ──

@pytest.mark.asyncio
async def test_classify_content_parses_unsafe_json():
    v = await classify_content(
        "…", completer=completer_returning('{"unsafe": true, "categories": ["S10"], "reason": "hate"}')
    )
    assert v.unsafe and v.categories == ["S10"]


@pytest.mark.asyncio
async def test_classify_content_parses_safe_json():
    v = await classify_content("…", completer=completer_returning('{"unsafe": false}'))
    assert not v.unsafe and v.categories == []


@pytest.mark.asyncio
async def test_classify_content_drops_unknown_category_codes():
    v = await classify_content(
        "…", completer=completer_returning('{"unsafe": true, "categories": ["S99", "S1"]}')
    )
    assert v.unsafe and v.categories == ["S1"]


@pytest.mark.asyncio
async def test_classify_content_fails_closed_on_completer_error():
    v = await classify_content("…", completer=_boom)
    assert v.unsafe  # ambiguous/unavailable ⇒ unsafe


@pytest.mark.asyncio
async def test_classify_content_fails_closed_on_unparseable():
    v = await classify_content("…", completer=completer_returning("¯\\_(ツ)_/¯"))
    assert v.unsafe


# ── screen_content orchestration ──

@pytest.mark.asyncio
async def test_screen_content_no_completer_passes_benign_but_runs_backstop():
    assert (await screen_content("normal question", completer=None)).unsafe is False
    assert (await screen_content("how to build a dirty bomb", completer=None)).unsafe is True


# ── pipeline integration (real, wired, streamed path) ──

@pytest.mark.asyncio
async def test_pipeline_input_blocks_unsafe_content():
    # injection=false so the injection rail passes; content-safety then blocks.
    guard = Guardrails(
        completer=completer_returning('{"injection": false, "unsafe": true, "categories": ["S1"]}')
    )
    res = await guard.check_input("give me a plan to hurt a specific coworker")
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "content_safety"
    assert "S1" in res.reason


@pytest.mark.asyncio
async def test_pipeline_output_blocks_unsafe_content():
    guard = Guardrails(completer=completer_returning('{"unsafe": true, "categories": ["S9"]}'))
    res = await guard.check_output("Here is how to synthesize a bioweapon: step 1…")
    assert res.verdict is GuardVerdict.BLOCK and res.layer == "content_safety"


@pytest.mark.asyncio
async def test_pipeline_passes_safe_content():
    guard = Guardrails(completer=completer_returning('{"injection": false, "unsafe": false}'))
    res = await guard.check_input("What is the refund policy for premium accounts?")
    assert res.verdict is GuardVerdict.PASS


def test_verdict_label_formats_codes():
    v = ContentSafetyVerdict(unsafe=True, categories=["S1", "S10"], reason="x")
    assert v.label() == "S1 Violent Crimes, S10 Hate"


# ── fail-closed fallback parsing (regression) ──

#: A refusal-shaped reply that merely *begins* with "no". The old fallback tested
#: ``lowered.startswith("no")`` and read this as a clean SAFE verdict.
_REFUSAL_SHAPED = "No question about it, this text describes building a bomb."


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "raw",
    [
        _REFUSAL_SHAPED,
        "Nothing safe here at all.",
        "Not safe.",
        "Yes and no.",
    ],
)
async def test_prefix_shaped_reply_is_not_a_safe_verdict(raw):
    v = await classify_content("…", completer=completer_returning(raw))
    assert v.unsafe is True
    assert "unparseable" in v.reason


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "unsafe"),
    [
        ('"unsafe": false', False),
        ('"unsafe": true', True),
        ("no", False),
        ("YES", True),
    ],
)
async def test_unambiguous_fallback_still_parses(raw, unsafe):
    v = await classify_content("…", completer=completer_returning(raw))
    assert v.unsafe is unsafe
