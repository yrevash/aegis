"""Audit A / §5.8: one tenant's failures must not spend another tenant's money.

The circuit breaker is process-global and keyed by *deployment*, which is correct —
a deployment is genuinely shared, and a shared fact about it (it is unreachable) is
a shared fact for everybody. The defect was never *where* the state lives; it was
*what counted as evidence for it*.

`_attempt` used to fault the deployment on **any** exception. A provider rejecting
one tenant's request — an over-long prompt, a content-policy refusal, that tenant's
own rate limit — says nothing about upstream health, but three of them opened the
breaker for the whole process. The next tenant's ``CHEAP`` call was then answered by
``GENERATION`` and billed to *their* ledger, which is precisely the "spend decision
taken on their behalf" §5.8 exists to prevent — arriving through a door the tier
bound does not cover. At its worst, a ``CHEAP``-only tenant whose own calls never
failed was refused outright with ``all_degraded``.

So the rule these tests pin down: **only evidence about the deployment arms the
breaker.** Connect errors, timeouts and 5xx do. 4xx, content policy, context length
and per-tenant rate limits do not, and neither does an opaque error type this
gateway has never heard of — an unrecognised failure is not evidence of anything.
"""

from __future__ import annotations

import sys

import pytest

import aegis.gateway.llm as llm_mod
from aegis.core.models import ModelRole
from aegis.gateway.llm import (
    ModelUnavailableError,
    breaker_status,
    complete,
    configure,
    model_allowlist,
)

from .test_fallback_survival import CHEAP_MODEL, MESSAGES, SpyLiteLLM


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


TENANT_A_BAD = [{"role": "user", "content": "TENANT-A-BAD-PROMPT"}]
TENANT_B_GOOD = [{"role": "user", "content": "tenant B question"}]


class _BadRequest(Exception):
    """A CLIENT-side error: this request was wrong, the deployment is healthy.

    Deliberately a bare ``Exception`` with no status code — the provider-neutral
    worst case. If the classifier only recognised litellm's typed exceptions, this
    would still poison the breaker.
    """


def _reject_tenant_a(spy):  # noqa: ANN001, ANN202
    """Make the fake provider reject tenant A's prompt and serve everyone else."""
    real = spy.acompletion

    async def _patched(**kwargs):
        if kwargs["messages"][0]["content"] == "TENANT-A-BAD-PROMPT":
            spy.calls.append(kwargs)
            raise _BadRequest("context_length_exceeded")
        return await real(**kwargs)

    spy.acompletion = _patched


async def test_one_tenants_bad_requests_do_not_degrade_another_tenants_service(spy, clock):
    """Three client-side rejections from tenant A leave the deployment healthy."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    _reject_tenant_a(spy)

    for _ in range(3):
        with pytest.raises(_BadRequest):
            await complete(ModelRole.CHEAP, TENANT_A_BAD)

    assert CHEAP_MODEL not in breaker_status(), (
        "a request-side failure was counted as evidence the deployment is dead: "
        f"{breaker_status()}"
    )

    before = len(spy.calls)
    result = await complete(ModelRole.CHEAP, TENANT_B_GOOD)
    served = [str(c["model"]).removeprefix("openai/") for c in spy.calls[before:]]

    assert served == [CHEAP_MODEL], (
        f"tenant A's client-side errors pushed tenant B off {CHEAP_MODEL} onto "
        f"{served}; answered by {result.model} and billed to tenant B"
    )
    assert result.model == CHEAP_MODEL


async def test_a_cheap_only_tenant_is_not_refused_over_another_tenants_bad_requests(spy, clock):
    """The same coupling at its worst: B is CHEAP-only and has nowhere to fall back to."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    _reject_tenant_a(spy)

    for _ in range(3):
        with pytest.raises(_BadRequest):
            await complete(ModelRole.CHEAP, TENANT_A_BAD)

    with model_allowlist([ModelRole.CHEAP]):
        result = await complete(ModelRole.CHEAP, TENANT_B_GOOD)

    assert result.model == CHEAP_MODEL, (
        "tenant B, whose own calls never failed, was refused entirely because "
        "tenant A's client errors opened a process-global breaker"
    )


async def test_a_real_transport_failure_still_arms_the_breaker(spy, clock):
    """Non-vacuity: the breaker is not simply switched off.

    A shared breaker over a shared deployment is deliberate and stays. A tenant
    bounded to ``CHEAP`` *is* refused when ``CHEAP`` is genuinely unreachable —
    that is a true fact about the deployment, not one tenant's mistake — and the
    cooldown probe is what gives the service back.
    """
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    spy.down = {CHEAP_MODEL}

    for _ in range(3):
        with pytest.raises((ConnectionError, ModelUnavailableError)):
            await complete(ModelRole.CHEAP, MESSAGES)

    assert breaker_status()[CHEAP_MODEL]["degraded"] is True
    with model_allowlist([ModelRole.CHEAP]), pytest.raises(ModelUnavailableError) as exc:
        await complete(ModelRole.CHEAP, MESSAGES)
    assert exc.value.reason.value == "all_degraded"

    spy.down = set()
    clock.advance(31.0)
    with model_allowlist([ModelRole.CHEAP]):
        probe = await complete(ModelRole.CHEAP, MESSAGES)
    assert probe.model == CHEAP_MODEL


@pytest.mark.parametrize(
    ("status", "arms"),
    [
        (400, False),  # bad request
        (403, False),  # content policy
        (429, False),  # this tenant's rate limit
        (408, True),   # the deployment did not answer in time
        (500, True),
        (503, True),
    ],
)
async def test_the_status_code_decides_whether_the_breaker_arms(spy, clock, status, arms):
    """A 4xx is evidence about the request; a 5xx (or a timeout) is about the deployment."""
    configure(breaker_threshold=1, breaker_cooldown_seconds=30.0)

    class _Typed(Exception):
        status_code = status

    async def _raise(**kwargs):
        spy.calls.append(kwargs)
        raise _Typed("provider said no")

    spy.acompletion = _raise

    with pytest.raises(_Typed):
        await complete(ModelRole.CHEAP, MESSAGES)

    assert (CHEAP_MODEL in breaker_status()) is arms, (
        f"HTTP {status} armed={CHEAP_MODEL in breaker_status()}, expected {arms}"
    )


async def test_a_client_error_is_reported_even_though_it_does_not_arm_the_breaker(spy, caplog):
    """Not arming must not mean not saying: a silent control is the standing defect."""
    configure(breaker_threshold=3, breaker_cooldown_seconds=30.0)
    _reject_tenant_a(spy)

    with caplog.at_level("WARNING", logger="aegis.gateway.llm"), pytest.raises(_BadRequest):
        await complete(ModelRole.CHEAP, TENANT_A_BAD)

    assert any(
        "circuit breaker is NOT armed" in record.getMessage() for record in caplog.records
    ), [r.getMessage() for r in caplog.records]


def test_the_classifier_is_exported_and_conservative():
    """An unrecognised error type is not evidence of anything, so it arms nothing."""
    assert llm_mod._is_deployment_evidence(ConnectionError("refused")) is True
    assert llm_mod._is_deployment_evidence(TimeoutError("hung")) is True
    assert llm_mod._is_deployment_evidence(ValueError("who knows")) is False
