"""Focused tests for the honest security-posture surface.

The contract under test:

* :func:`security_posture` returns one entry per major threat (OWASP LLM Top-10
  2025 + key agentic themes), each mapped to a **real** control.
* Every ``enforced`` entry names an importable function/rail that exists — no
  fabricated ``enforced``.
* Statuses are derived from the **live wiring**, so a config change (wire the
  injection completer, inject a budget hook / flip ``budget_fail_open``, replace
  the dev JWT secret) flips the reported status — never a static list.
* No threat is silently claimed-covered when its control is off.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import aegis.gateway.llm as gw_llm
from aegis.governance import security as gsec
from aegis.guardrails import nemo
from aegis.security import (
    PostureEntry,
    PostureSignals,
    PostureStatus,
    read_signals,
    resolve_symbol,
    security_posture,
)

# Every OWASP LLM-Top-10 (2025) item the task enumerates, plus the agentic themes.
_EXPECTED_THREATS = {
    "LLM01",
    "LLM02",
    "LLM04",
    "LLM05",
    "LLM06",
    "LLM07",
    "LLM08",
    "LLM09",
    "LLM10",
    "AGENTIC-IDENTITY",
    "AGENTIC-TRACEABILITY",
    "AGENTIC-TOOL-MISUSE",
    # MITRE ATLAS AML.T0024's second half. It is not an OWASP row and it is not a
    # payload rail, which is exactly why it needs its own line on this surface: a
    # reviewer scanning the table for "can one principal enumerate our corpus" would
    # otherwise find nothing, and find nothing whether or not the control existed.
    "AGENTIC-EXTRACTION",
}


@pytest.fixture(autouse=True)
def _reset_security_wiring() -> Iterator[None]:
    """Snapshot + restore every process-global the posture introspects.

    Keeps the default (offline) posture deterministic between tests: no injection
    completer, the dev JWT secret, and a no-op (ungoverned) gateway.
    """
    saved_completer = nemo.get_completer()
    saved_sec = gsec._config
    saved_cfg = gw_llm._config
    saved_gov = gw_llm._governance

    nemo.set_completer(None)
    gsec.configure_security(gsec.DEFAULT_JWT_SECRET)
    gw_llm._config = None
    gw_llm._governance = gw_llm._NoOpGovernance()
    try:
        yield
    finally:
        nemo.set_completer(saved_completer)
        gsec._config = saved_sec
        gw_llm._config = saved_cfg
        gw_llm._governance = saved_gov


class _StubCompleter:
    """A minimal ChatCompleter stand-in — presence is all the posture reads."""

    async def complete(self, *_args: object, **_kwargs: object) -> str:
        return ""


class _RealHook:
    """A real (non-no-op) governance hook — its presence flips budget enforcement."""

    def get_context(self) -> None:
        return None

    async def enforce(self, _ctx: object) -> None:
        return None

    async def record(self, _ctx: object, **_kwargs: object) -> None:
        return None


def _by_id() -> dict[str, PostureEntry]:
    return {e.threat_id: e for e in security_posture()}


def _status(threat_id: str) -> PostureStatus:
    return _by_id()[threat_id].status


# ─────────────────────────────────────────────────────────────────────────────
# Shape / coverage
# ─────────────────────────────────────────────────────────────────────────────


def test_returns_an_entry_per_major_threat() -> None:
    ids = {e.threat_id for e in security_posture()}
    assert ids == _EXPECTED_THREATS


def test_every_entry_is_a_typed_posture_entry() -> None:
    for e in security_posture():
        assert isinstance(e, PostureEntry)
        assert isinstance(e.status, PostureStatus)


def test_no_duplicate_threat_ids() -> None:
    ids = [e.threat_id for e in security_posture()]
    assert len(ids) == len(set(ids))


def test_every_entry_names_a_control_module_and_mechanism() -> None:
    for e in security_posture():
        assert e.control.strip()
        assert e.module.startswith("aegis.")
        assert e.mechanism.strip()
        assert e.detail.strip()
        assert e.refs, f"{e.threat_id} has no importable refs"


def test_the_nine_owasp_llm_items_are_present() -> None:
    ids = {e.threat_id for e in security_posture()}
    for item in ("LLM01", "LLM02", "LLM04", "LLM05", "LLM06", "LLM07", "LLM08", "LLM09", "LLM10"):
        assert item in ids


# ─────────────────────────────────────────────────────────────────────────────
# No fabricated 'enforced' — every claimed mechanism must really exist
# ─────────────────────────────────────────────────────────────────────────────


def test_all_refs_are_importable() -> None:
    for e in security_posture():
        for ref in e.refs:
            resolve_symbol(ref)  # raises if it does not resolve


def test_every_enforced_entry_resolves_a_real_callable() -> None:
    for e in security_posture():
        if e.status is PostureStatus.ENFORCED:
            resolved = [resolve_symbol(r) for r in e.refs]
            assert any(callable(obj) for obj in resolved), (
                f"{e.threat_id} is 'enforced' but names no real callable"
            )


def test_resolve_symbol_rejects_malformed_ref() -> None:
    with pytest.raises(ValueError):
        resolve_symbol("aegis.security.posture")  # no ':attr'


def test_resolve_symbol_raises_on_missing_symbol() -> None:
    with pytest.raises((AttributeError, ModuleNotFoundError)):
        resolve_symbol("aegis.security.posture:does_not_exist")


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic controls are unconditionally enforced (pure code, no config gate)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("threat_id", ["LLM02", "LLM04", "LLM05", "LLM06", "LLM07", "LLM08"])
def test_pure_code_controls_are_enforced(threat_id: str) -> None:
    assert _status(threat_id) is PostureStatus.ENFORCED


def test_traceability_is_enforced() -> None:
    assert _status("AGENTIC-TRACEABILITY") is PostureStatus.ENFORCED


def test_misinformation_is_honestly_partial() -> None:
    # Grounding is opt-in and advisory by default — never a silent green.
    assert _status("LLM09") is PostureStatus.PARTIAL


def test_tool_misuse_is_honestly_partial_host_side() -> None:
    entry = _by_id()["AGENTIC-TOOL-MISUSE"]
    assert entry.status is PostureStatus.PARTIAL
    assert "host-side" in entry.detail.lower()


# ─────────────────────────────────────────────────────────────────────────────
# Status flips — the surface reflects live wiring, not a static list
# ─────────────────────────────────────────────────────────────────────────────


def test_injection_partial_when_no_model_layer() -> None:
    nemo.set_completer(None)
    assert _status("LLM01") is PostureStatus.PARTIAL


def test_injection_enforced_when_model_layer_wired() -> None:
    nemo.set_completer(_StubCompleter())
    assert _status("LLM01") is PostureStatus.ENFORCED


def test_injection_flip_is_reversible() -> None:
    assert _status("LLM01") is PostureStatus.PARTIAL
    nemo.set_completer(_StubCompleter())
    assert _status("LLM01") is PostureStatus.ENFORCED
    nemo.set_completer(None)
    assert _status("LLM01") is PostureStatus.PARTIAL


def test_identity_partial_under_dev_jwt_secret() -> None:
    gsec.configure_security(gsec.DEFAULT_JWT_SECRET)
    assert _status("AGENTIC-IDENTITY") is PostureStatus.PARTIAL


def test_identity_enforced_under_real_jwt_secret() -> None:
    gsec.configure_security("a-strong-production-secret-not-the-dev-default-0000")
    assert _status("AGENTIC-IDENTITY") is PostureStatus.ENFORCED


def test_consumption_partial_without_budget_hook() -> None:
    # No governance hook wired: the loop cap bounds runaway loops, but spend is
    # ungoverned — honestly partial, never enforced.
    assert gw_llm._governance.__class__ is gw_llm._NoOpGovernance
    assert _status("LLM10") is PostureStatus.PARTIAL


def test_consumption_enforced_with_budget_hook() -> None:
    gw_llm._governance = _RealHook()
    assert _status("LLM10") is PostureStatus.ENFORCED


def test_consumption_partial_when_budget_fail_open() -> None:
    gw_llm._governance = _RealHook()

    class _Cfg:
        base_url = ""
        api_key = ""
        ssl_verify = True
        max_output_tokens = 1024
        timeout_seconds = 60.0
        budget_fail_open = True

    gw_llm._config = _Cfg()
    # A wired hook that fails open is weaker than fail-closed enforcement.
    assert _status("LLM10") is PostureStatus.PARTIAL


def test_no_threat_claimed_enforced_when_its_control_is_off() -> None:
    # Default offline posture: injection model layer off, budget ungoverned, dev
    # secret in force — each of those threats must be PARTIAL, not ENFORCED.
    statuses = _by_id()
    assert statuses["LLM01"].status is PostureStatus.PARTIAL
    assert statuses["LLM10"].status is PostureStatus.PARTIAL
    assert statuses["AGENTIC-IDENTITY"].status is PostureStatus.PARTIAL


# ─────────────────────────────────────────────────────────────────────────────
# Signals plumbing
# ─────────────────────────────────────────────────────────────────────────────


def test_read_signals_reflects_default_offline_wiring() -> None:
    s = read_signals()
    assert isinstance(s, PostureSignals)
    assert s.model_layer_wired is False
    assert s.jwt_dev_secret is True
    assert s.budget_hook_wired is False
    assert s.hazard_categories > 0
    assert s.rls_tables > 0
    assert s.gate_min_risk == "high"


def test_read_signals_tracks_completer_wiring() -> None:
    assert read_signals().model_layer_wired is False
    nemo.set_completer(_StubCompleter())
    assert read_signals().model_layer_wired is True


def test_security_posture_accepts_injected_signals() -> None:
    # Fully-wired signals should light every derived status green.
    s = read_signals().model_copy(
        update={
            "model_layer_wired": True,
            "jwt_dev_secret": False,
            "budget_hook_wired": True,
            "budget_fail_open": False,
        }
    )
    statuses = {e.threat_id: e.status for e in security_posture(signals=s)}
    assert statuses["LLM01"] is PostureStatus.ENFORCED
    assert statuses["AGENTIC-IDENTITY"] is PostureStatus.ENFORCED
    assert statuses["LLM10"] is PostureStatus.ENFORCED
    # Injected signals are pure inputs — no live reads leaked in.
    assert statuses["LLM09"] is PostureStatus.PARTIAL  # grounding stays advisory


def test_security_posture_is_side_effect_free_and_deterministic() -> None:
    first = [(e.threat_id, e.status) for e in security_posture()]
    second = [(e.threat_id, e.status) for e in security_posture()]
    assert first == second
