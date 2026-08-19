"""Fallback that survives a fan-out (phase 5 §5.8).

Three properties, none of which single-agent needed:

* a deployment that fails repeatedly is **skipped**, so N concurrent sub-agents
  do not each pay a dead deployment's full timeout — and one probe re-opens it;
* every degradation is **visible**: an event plus an ERROR log;
* a fallback chain **never escapes the tenant's tier** — a cheap tenant whose
  deployment is down gets a loud failure, not an expensive model on their ledger.

Every assertion is on *which deployment was actually called* (the fake litellm
records each request) rather than on a log line, except the two that exist
precisely to prove the degradation is observable.

No network, no litellm: a fake module is injected, exactly as in `test_llm.py`.
"""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import (
    FallbackReason,
    ModelUnavailableError,
    breaker_status,
    complete,
    configure,
    fallback_events,
    model_allowlist,
)

CHEAP_MODEL = "genailab-maas-gpt-4o-mini"
GENERATION_MODEL = "genailab-maas-gpt-4o"
REASONING_MODEL = "genailab-maas-Phi-4-reasoning"

MESSAGES = [{"role": "user", "content": "hi"}]


def _response(model: str):
    """A minimal successful completion answered by ``model``."""
    message = SimpleNamespace(content="ok", tool_calls=[])
    usage = SimpleNamespace(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage, model=model)


class SpyLiteLLM:
    """A fake ``litellm`` that records every request and can fail on demand.

    ``down`` is the set of deployment ids whose call raises — the stand-in for a
    dead upstream. It raises on the deployment the gateway *selected*, which is
    what makes "was the expensive model called?" answerable by inspection.
    """

    def __init__(self, down: set[str] | None = None):
        self.ssl_verify = None
        self.calls: list[dict] = []
        self.down: set[str] = set(down or ())

    @property
    def called_models(self) -> list[str]:
        """The deployment id (no provider prefix) of each recorded request."""
        return [str(call["model"]).removeprefix("openai/") for call in self.calls]

    async def acompletion(self, **kwargs):
        self.calls.append(kwargs)
        selected = str(kwargs["model"]).removeprefix("openai/")
        if selected in self.down:
            raise ConnectionError(f"{selected} is down")
        return _response(selected)

    def completion_cost(self, *, completion_response):
        return 0.001


@pytest.fixture
def spy(monkeypatch):
    """Install a fresh spy ``litellm`` (gateway state is reset by the autouse fixture)."""
    fake = SpyLiteLLM()
    monkeypatch.setitem(sys.modules, "litellm", fake)
    return fake


@pytest.fixture
def clock(monkeypatch):
    """A controllable monotonic clock for the breaker's cooldown."""

    class _Clock:
        now = 1000.0

        def advance(self, seconds: float) -> None:
            self.now += seconds

    fake = _Clock()
    monkeypatch.setattr(llm_mod, "_now", lambda: fake.now)
    return fake


async def _fail(role: ModelRole) -> None:
    """Run one call expected to raise, swallowing the transport error."""
    with pytest.raises((ConnectionError, ModelUnavailableError)):
        await complete(role, MESSAGES)


# ── 1. The breaker: a dead deployment is skipped, and a probe re-opens it ────


async def test_repeated_failures_skip_the_deployment_on_the_next_call(spy, clock):
    """After the threshold, the next call goes to the fallback — not to the dead one."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}

    for _ in range(3):
        await _fail(ModelRole.CHEAP)

    # The three failures all hit cheap: the breaker is not open before threshold.
    assert spy.called_models == [CHEAP_MODEL] * 3

    result = await complete(ModelRole.CHEAP, MESSAGES)

    # The fourth call never touches the dead deployment — that is the whole point:
    # with a fan-out, four agents would otherwise pay four full timeouts.
    assert spy.called_models[3] == GENERATION_MODEL
    assert result.model == GENERATION_MODEL
    assert breaker_status()[CHEAP_MODEL]["degraded"] is True


async def test_breaker_stays_shut_below_the_threshold(spy, clock):
    """Two failures out of three are not a dead provider — no skipping."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}

    for _ in range(2):
        await _fail(ModelRole.CHEAP)
    spy.down = set()
    await complete(ModelRole.CHEAP, MESSAGES)

    assert spy.called_models == [CHEAP_MODEL] * 3


async def test_a_success_resets_the_consecutive_failure_count(spy, clock):
    """Consecutive means consecutive: a success in between must clear the count."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)

    spy.down = {CHEAP_MODEL}
    await _fail(ModelRole.CHEAP)
    await _fail(ModelRole.CHEAP)
    spy.down = set()
    await complete(ModelRole.CHEAP, MESSAGES)
    spy.down = {CHEAP_MODEL}
    await _fail(ModelRole.CHEAP)
    await _fail(ModelRole.CHEAP)
    spy.down = set()
    await complete(ModelRole.CHEAP, MESSAGES)

    assert spy.called_models == [CHEAP_MODEL] * 6


async def test_one_probe_after_the_cooldown_reopens_the_deployment(spy, clock):
    """The cooldown expires, one probe goes back to the primary, and it closes."""
    configure(breaker_threshold=2, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}
    for _ in range(2):
        await _fail(ModelRole.CHEAP)

    # Still inside the cooldown: skipped.
    await complete(ModelRole.CHEAP, MESSAGES)
    assert spy.called_models[-1] == GENERATION_MODEL

    clock.advance(31.0)
    spy.down = set()

    probe = await complete(ModelRole.CHEAP, MESSAGES)
    assert spy.called_models[-1] == CHEAP_MODEL, "the probe must go to the recovered deployment"
    assert probe.model == CHEAP_MODEL

    # The probe succeeded, so the breaker is closed — not merely half-open.
    await complete(ModelRole.CHEAP, MESSAGES)
    assert spy.called_models[-1] == CHEAP_MODEL
    assert CHEAP_MODEL not in breaker_status()


async def test_a_failed_probe_restarts_the_cooldown(spy, clock):
    """A probe that fails does not reopen the deployment for everybody else."""
    configure(breaker_threshold=2, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}
    for _ in range(2):
        await _fail(ModelRole.CHEAP)

    clock.advance(31.0)
    await _fail(ModelRole.CHEAP)  # the probe, still dead
    assert spy.called_models[-1] == CHEAP_MODEL

    spy.down = set()
    await complete(ModelRole.CHEAP, MESSAGES)
    assert spy.called_models[-1] == GENERATION_MODEL, "a failed probe must re-arm the cooldown"


# ── 2. The degradation is visible: an event and an ERROR log ────────────────


async def test_breaker_fallback_emits_event_and_logs_at_error(spy, clock, caplog):
    """A skipped primary is an event with the role, both deployments and the reason."""
    configure(breaker_threshold=1, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}
    await _fail(ModelRole.CHEAP)
    spy.down = set()

    with caplog.at_level("ERROR"):
        await complete(ModelRole.CHEAP, MESSAGES)

    event = fallback_events()[-1]
    assert event["role"] == ModelRole.CHEAP.value
    assert event["failed_deployment"] == CHEAP_MODEL
    assert event["taken_deployment"] == GENERATION_MODEL
    assert event["reason"] == FallbackReason.CIRCUIT_OPEN.value
    assert any(
        record.levelname == "ERROR" and "Gateway fallback" in record.message
        for record in caplog.records
    ), "a fallback nobody can see in the log is the defect this test exists for"


async def test_in_litellm_fallback_is_reported_and_counted(spy, clock, caplog):
    """LiteLLM answering from a fallback deployment is a degradation, not a detail."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)

    async def _answered_by_generation(**kwargs):
        spy.calls.append(kwargs)
        return _response(GENERATION_MODEL)

    spy.acompletion = _answered_by_generation

    with caplog.at_level("ERROR"):
        result = await complete(ModelRole.CHEAP, MESSAGES)

    assert result.model == GENERATION_MODEL
    event = fallback_events()[-1]
    assert event["failed_deployment"] == CHEAP_MODEL
    assert event["taken_deployment"] == GENERATION_MODEL
    assert event["reason"] == FallbackReason.UPSTREAM_ERROR.value
    # The primary that did not answer is faulted, so a repeatedly-bypassed
    # deployment eventually stops being asked at all.
    assert breaker_status()[CHEAP_MODEL]["consecutive_failures"] == 1
    assert any(record.levelname == "ERROR" for record in caplog.records)


async def test_an_unrecognised_response_model_is_not_fabricated_into_a_fallback(spy, clock):
    """A provider echoing an unknown id is not evidence the primary failed."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)

    async def _odd_echo(**kwargs):
        spy.calls.append(kwargs)
        return _response("some-provider-internal-name")

    spy.acompletion = _odd_echo

    await complete(ModelRole.CHEAP, MESSAGES)

    assert fallback_events() == []
    assert breaker_status() == {}


async def test_the_injected_sink_sees_every_degradation(spy, clock):
    """The host's stream gets the event too — logs are not the only channel."""
    seen: list[llm_mod.FallbackEvent] = []

    class _Sink:
        def fallback(self, event):
            seen.append(event)

    configure(breaker_threshold=1, breaker_cooldown_seconds=30.0, fallback_sink=_Sink())
    spy.down = {CHEAP_MODEL}
    await _fail(ModelRole.CHEAP)
    spy.down = set()
    await complete(ModelRole.CHEAP, MESSAGES)

    assert [event.reason for event in seen] == [FallbackReason.CIRCUIT_OPEN]
    assert seen[0].failed_deployment == CHEAP_MODEL


async def test_a_raising_sink_never_breaks_the_call(spy, clock):
    """Observation is not a control path: a broken sink must not fail the run."""

    class _BrokenSink:
        def fallback(self, event):
            raise RuntimeError("sink exploded")

    configure(breaker_threshold=1, breaker_cooldown_seconds=30.0, fallback_sink=_BrokenSink())
    spy.down = {CHEAP_MODEL}
    await _fail(ModelRole.CHEAP)
    spy.down = set()

    result = await complete(ModelRole.CHEAP, MESSAGES)
    assert result.model == GENERATION_MODEL


# ── 3. The chain is bounded by the tenant's tier ────────────────────────────


async def test_a_cheap_only_tenant_fails_loudly_instead_of_being_promoted(spy, clock):
    """The expensive deployment is NEVER called for a tenant not entitled to it."""
    configure(breaker_threshold=2, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}

    with model_allowlist([ModelRole.CHEAP]):
        for _ in range(2):
            await _fail(ModelRole.CHEAP)

        with pytest.raises(ModelUnavailableError) as excinfo:
            await complete(ModelRole.CHEAP, MESSAGES)

    assert excinfo.value.reason is FallbackReason.ALL_DEGRADED
    assert GENERATION_MODEL not in spy.called_models
    assert REASONING_MODEL not in spy.called_models
    assert spy.called_models == [CHEAP_MODEL] * 2
    # The loud failure is also a visible one.
    assert fallback_events()[-1]["taken_deployment"] is None
    assert fallback_events()[-1]["reason"] == FallbackReason.ALL_DEGRADED.value


async def test_the_tier_bound_removes_the_out_of_tier_fallback_from_the_chain(spy, clock):
    """Even while healthy, a cheap-only tenant is never offered an expensive fallback."""
    with model_allowlist([ModelRole.CHEAP]):
        await complete(ModelRole.CHEAP, MESSAGES)

    assert spy.calls[0]["model"] == f"openai/{CHEAP_MODEL}"
    assert "fallbacks" not in spy.calls[0], "an out-of-tier fallback must not be offered"


async def test_a_downgrade_within_tier_is_allowed_and_visible(spy, clock):
    """Cheaper-than-asked is inside the tenant's entitlement; it is still reported."""
    with model_allowlist([ModelRole.CHEAP]):
        result = await complete(ModelRole.GENERATION, MESSAGES)

    assert result.model == CHEAP_MODEL
    assert spy.called_models == [CHEAP_MODEL]
    event = fallback_events()[-1]
    assert event["failed_deployment"] == GENERATION_MODEL
    assert event["taken_deployment"] == CHEAP_MODEL
    assert event["reason"] == FallbackReason.OUTSIDE_TIER.value


async def test_a_role_with_no_in_tier_deployment_is_refused_before_any_spend(spy, clock):
    """Nothing in tier means no call at all — not a cheaper guess, not an expensive one."""
    with model_allowlist([ModelRole.VISION]), pytest.raises(ModelUnavailableError) as excinfo:
        await complete(ModelRole.CHEAP, MESSAGES)

    assert excinfo.value.reason is FallbackReason.OUTSIDE_TIER
    assert spy.calls == []


async def test_the_tier_bound_is_per_task_not_per_process(spy, clock):
    """A fan-out runs many tenants' calls at once; the bound must travel per task."""

    async def _cheap_tenant():
        with model_allowlist([ModelRole.CHEAP]):
            await asyncio.sleep(0)
            return await complete(ModelRole.GENERATION, MESSAGES)

    async def _full_tenant():
        await asyncio.sleep(0)
        return await complete(ModelRole.GENERATION, MESSAGES)

    bounded, unbounded = await asyncio.gather(_cheap_tenant(), _full_tenant())

    assert bounded.model == CHEAP_MODEL
    assert unbounded.model == GENERATION_MODEL


# ── 4. A sibling's failure is a sibling's failure ───────────────────────────


async def test_one_agents_dead_deployment_does_not_cancel_its_siblings(spy, clock):
    """A gathered fan-out: the failing branch fails, the others still answer."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    spy.down = {REASONING_MODEL}

    async def _dead():
        # Tier-bounded to the dead role, so nothing rescues it: this branch fails.
        with model_allowlist([ModelRole.REASONING]):
            return await complete(ModelRole.REASONING, MESSAGES)

    async def _alive():
        return await complete(ModelRole.VISION, MESSAGES)

    results = await asyncio.gather(_dead(), _alive(), return_exceptions=True)

    assert isinstance(results[0], ConnectionError)
    assert results[1].model == "genailab-maas-Llama-3.2-90B-Vision-Instruct"


# ── 5. The knobs are readable, because a knob nobody can see cannot be tuned ─


def test_optimization_config_exposes_the_breaker_and_the_tier_bound():
    configure(breaker_threshold=7, breaker_cooldown_seconds=12.5)
    config = llm_mod.optimization_config()

    assert config["breaker"]["threshold"] == 7
    assert config["breaker"]["cooldown_seconds"] == 12.5
    assert config["breaker"]["state"] == {}
    assert config["allowed_roles"] is None

    with model_allowlist([ModelRole.CHEAP]):
        assert llm_mod.optimization_config()["allowed_roles"] == ["cheap"]
