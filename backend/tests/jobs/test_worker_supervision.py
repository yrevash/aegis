"""The worker is supervised and its state is visible — audit C, C5 and C6.

Three defects met in one place on the cold demo path:

* the Temporal connect failure surfaced a raw Rust tonic transport string that never
  said "start the Temporal dev server" (C5);
* the worker task died once, at boot, and was **never restarted** — bringing Temporal
  back did not bring the worker back (C6);
* ``GET /health`` returned ``{"status": "ok"}`` throughout, and there was no ``/ready``
  to ask a different question of (C6).
"""

from __future__ import annotations

import asyncio

import pytest

from app.jobs import health as worker_health_mod
from app.jobs.client import TemporalUnavailableError
from app.main import run_worker_supervised

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _clean_worker_health():
    """Every test starts from the boot state and leaves it that way."""
    worker_health_mod.reset_worker_health()
    yield
    worker_health_mod.reset_worker_health()


# ── C5: the connect failure says what to do about it ─────────────────────────


async def test_connect_failure_names_the_remedy(monkeypatch):
    """A dead orchestrator must produce a sentence an operator can act on.

    The SDK's own message is ``tonic::transport::Error(Transport, ConnectError(...))``:
    every word true, none of it actionable, and it reached a tenant verbatim as the
    ``detail`` of the 503 from ``POST /documents``.
    """
    from app.jobs import client as client_mod

    class _Dead:
        @staticmethod
        async def connect(address, namespace=None):  # noqa: ANN001, ARG004
            raise RuntimeError(
                "Failed client connect: `tonic::transport::Error(Transport, "
                'ConnectError(ConnectError("tcp connect error", Os { code: 61 })))`'
            )

    client_mod.reset_temporal_client()
    monkeypatch.setattr(client_mod, "Client", _Dead)
    with pytest.raises(TemporalUnavailableError) as excinfo:
        await client_mod.get_temporal_client()
    client_mod.reset_temporal_client()

    message = str(excinfo.value)
    assert "temporal server start-dev" in message, "the message must name the fix"
    assert "TEMPORAL_ADDRESS" in message, "or the knob that points elsewhere"
    # The raw transport string is kept — translating must not destroy evidence.
    assert "tonic::transport::Error" in message


# ── C6: the worker is restarted, and its state is legible ────────────────────


async def test_worker_is_retried_until_temporal_comes_back(monkeypatch):
    """A worker that could not connect at boot must recover on its own.

    Temporal is refused for the first two attempts and then answers. Nothing restarts
    the process; the supervisor is the only thing that could have noticed.
    """
    attempts = {"connect": 0}
    ran = asyncio.Event()

    async def _connect():
        attempts["connect"] += 1
        if attempts["connect"] <= 2:
            raise TemporalUnavailableError("localhost:7233", "default", OSError("refused"))
        return object()

    async def _run_workers(stop):
        ran.set()
        await stop.wait()

    monkeypatch.setattr("app.jobs.client.get_temporal_client", _connect)
    monkeypatch.setattr("app.jobs.worker.run_workers", _run_workers)
    monkeypatch.setattr("app.main._WORKER_RETRY_MIN_SECONDS", 0.01)
    monkeypatch.setattr("app.main._WORKER_RETRY_MAX_SECONDS", 0.02)

    stop = asyncio.Event()
    task = asyncio.create_task(run_worker_supervised(stop))
    await asyncio.wait_for(ran.wait(), timeout=5)

    snapshot = worker_health_mod.worker_health()
    assert snapshot.state == worker_health_mod.WORKER_RUNNING
    assert snapshot.restarts >= 2, "the recovery must be visible, not silently papered over"
    assert attempts["connect"] == 3

    stop.set()
    await asyncio.wait_for(task, timeout=5)
    assert worker_health_mod.worker_health().state == worker_health_mod.WORKER_STOPPED


async def test_a_down_worker_is_reported_with_its_reason(monkeypatch):
    """While the orchestrator is absent the state says ``down`` and says why."""

    async def _connect():
        raise TemporalUnavailableError("localhost:7233", "default", OSError("refused"))

    monkeypatch.setattr("app.jobs.client.get_temporal_client", _connect)
    monkeypatch.setattr("app.main._WORKER_RETRY_MIN_SECONDS", 0.01)
    monkeypatch.setattr("app.main._WORKER_RETRY_MAX_SECONDS", 0.02)

    stop = asyncio.Event()
    task = asyncio.create_task(run_worker_supervised(stop))
    for _ in range(200):
        await asyncio.sleep(0.01)
        if worker_health_mod.worker_health().state == worker_health_mod.WORKER_DOWN:
            break
    snapshot = worker_health_mod.worker_health()
    stop.set()
    await asyncio.wait_for(task, timeout=5)

    assert snapshot.state == worker_health_mod.WORKER_DOWN
    assert snapshot.detail is not None
    assert "temporal server start-dev" in snapshot.detail


# ── C6: /health stops claiming the substrate is fine, and /ready exists ──────


async def test_health_reports_the_worker_and_ready_refuses_when_it_is_down(client):
    """``/health`` carries the worker state; ``/ready`` is 503 while it is down.

    Liveness and readiness answer different questions on purpose. ``/health`` staying
    ``ok`` is correct — restarting the API does not start Temporal — but it must no
    longer be the *only* thing this platform says while its whole durable substrate is
    dead.
    """
    worker_health_mod.set_worker_state(
        worker_health_mod.WORKER_DOWN, detail="the durable-job orchestrator is not reachable"
    )

    health = await client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"
    assert health.json()["worker"] == "down"

    ready = await client.get("/ready")
    assert ready.status_code == 503
    body = ready.json()
    assert body["ready"] is False
    assert body["worker"]["state"] == "down"
    assert "not reachable" in body["worker"]["detail"]


async def test_ready_is_200_for_a_deployment_that_runs_no_worker(client):
    """``disabled`` is not a failure: the offline demo ships exactly that shape."""
    worker_health_mod.set_worker_state(worker_health_mod.WORKER_DISABLED)
    ready = await client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["ready"] is True


async def test_ready_needs_no_token(client):
    """A readiness probe a load balancer cannot call is not a readiness probe."""
    worker_health_mod.set_worker_state(worker_health_mod.WORKER_RUNNING)
    assert (await client.get("/ready")).status_code == 200
