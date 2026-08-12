"""The ``guardrails_engine`` switch routes the live path to the right engine.

``app.guardrails.check_input``/``check_output`` pick their engine from
``settings.guardrails_engine``: ``"nemo"`` (when the package is importable) runs
the NeMo Colang engine; anything else — the ``"programmatic"`` default — runs the
fast programmatic pipeline, which is also the fallback. A NeMo engine error fails
closed to a BLOCK. These tests stub the engine functions so they run offline with
no real ``LLMRails`` build and no network.
"""

from __future__ import annotations

import pytest
from aegis.core.types import GuardResult, GuardVerdict

from app import guardrails as G
from app.config import get_settings

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _restore_engine(monkeypatch):
    """Always restore the settings knob after each test."""
    original = get_settings().guardrails_engine
    yield
    monkeypatch.setattr(get_settings(), "guardrails_engine", original)


async def test_nemo_engine_routes_input_through_nemo(monkeypatch):
    """``guardrails_engine='nemo'`` sends check_input through the NeMo path."""
    monkeypatch.setattr(get_settings(), "guardrails_engine", "nemo")
    monkeypatch.setattr(G.nemo, "nemo_available", lambda: True)

    wired: list[object] = []
    monkeypatch.setattr(G.nemo, "set_completer", lambda c: wired.append(c))

    sentinel = GuardResult(
        verdict=GuardVerdict.BLOCK, reason="from nemo", text="x", layer="nemo-input"
    )

    async def fake_nemo_input(text: str) -> GuardResult:
        return sentinel

    async def boom(text: str) -> GuardResult:  # programmatic path must NOT run
        raise AssertionError("programmatic pipeline should not be called for engine='nemo'")

    monkeypatch.setattr(G.nemo, "nemo_check_input", fake_nemo_input)
    monkeypatch.setattr(G._guard, "check_input", boom)

    result = await G.check_input("anything")
    assert result is sentinel
    # The platform's cheap-model completer was wired into the NeMo actions.
    assert wired and wired[0] is G._gateway_completer


async def test_programmatic_default_routes_through_pipeline(monkeypatch):
    """The default ``'programmatic'`` engine uses the pipeline, not NeMo."""
    monkeypatch.setattr(get_settings(), "guardrails_engine", "programmatic")

    sentinel = GuardResult(
        verdict=GuardVerdict.PASS, reason="from pipeline", text="ok", layer=None
    )

    async def fake_guard_input(text: str) -> GuardResult:
        return sentinel

    async def boom(text: str) -> GuardResult:  # NeMo path must NOT run
        raise AssertionError("NeMo engine should not be called for engine='programmatic'")

    monkeypatch.setattr(G._guard, "check_input", fake_guard_input)
    monkeypatch.setattr(G.nemo, "nemo_check_input", boom)

    result = await G.check_input("anything")
    assert result is sentinel


async def test_nemo_selected_but_unavailable_falls_back(monkeypatch):
    """``'nemo'`` with the package absent falls back to the programmatic pipeline."""
    monkeypatch.setattr(get_settings(), "guardrails_engine", "nemo")
    monkeypatch.setattr(G.nemo, "nemo_available", lambda: False)

    sentinel = GuardResult(
        verdict=GuardVerdict.PASS, reason="from pipeline", text="ok", layer=None
    )

    async def fake_guard_input(text: str) -> GuardResult:
        return sentinel

    monkeypatch.setattr(G._guard, "check_input", fake_guard_input)
    result = await G.check_input("anything")
    assert result is sentinel


async def test_nemo_engine_error_fails_closed_to_block(monkeypatch):
    """A NeMo engine error blocks (fail-closed) — never a silent pass."""
    monkeypatch.setattr(get_settings(), "guardrails_engine", "nemo")
    monkeypatch.setattr(G.nemo, "nemo_available", lambda: True)
    monkeypatch.setattr(G.nemo, "set_completer", lambda c: None)

    async def kaboom(text: str) -> GuardResult:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(G.nemo, "nemo_check_input", kaboom)
    monkeypatch.setattr(G.nemo, "nemo_check_output", kaboom)

    result = await G.check_input("anything")
    assert result.verdict is GuardVerdict.BLOCK
    assert result.layer == "nemo-input"

    result_out = await G.check_output("anything")
    assert result_out.verdict is GuardVerdict.BLOCK
    assert result_out.layer == "nemo-output"


async def test_nemo_engine_routes_output_through_nemo(monkeypatch):
    """``guardrails_engine='nemo'`` sends check_output through the NeMo path."""
    monkeypatch.setattr(get_settings(), "guardrails_engine", "nemo")
    monkeypatch.setattr(G.nemo, "nemo_available", lambda: True)
    monkeypatch.setattr(G.nemo, "set_completer", lambda c: None)

    sentinel = GuardResult(
        verdict=GuardVerdict.REDACT, reason="from nemo", text="y", layer="nemo-output"
    )

    async def fake_nemo_output(text: str) -> GuardResult:
        return sentinel

    async def boom(text: str) -> GuardResult:
        raise AssertionError("programmatic pipeline should not be called for engine='nemo'")

    monkeypatch.setattr(G.nemo, "nemo_check_output", fake_nemo_output)
    monkeypatch.setattr(G._guard, "check_output", boom)

    result = await G.check_output("anything")
    assert result is sentinel
