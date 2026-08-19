"""``GET /pipelines`` and the runtime that reads the same declaration (§8.12).

Two properties, and the second is the one that makes the declaration a contract rather
than documentation:

1. the endpoint serves the three declarations, behind authentication, and the ingestion
   one it serves **is** the stage tuple the resume walks;
2. ``app.jobs.ingest_log`` — the only writer of ``run_events`` — takes its event type and
   its per-stage sequence numbers from the declaration at import, so a spec that
   disagreed with :data:`aegis.jobs.stages.INGEST_STAGES` stops the ingest worker instead
   of committing rows labelled with a stage name nothing can read back.

The second is proved by mutation: the module is re-imported against a grown stage tuple
and the import must raise. Nothing here runs an ingest, opens a gateway or compiles a
model.
"""

from __future__ import annotations

import importlib
import importlib.util

import pytest
from aegis.jobs.stages import CPU_QUEUE, StageSpec, stage_names
from aegis.pipelines import (
    INGEST_STAGE_EVENT_TYPE,
    INGESTION_PIPELINE,
    PIPELINES,
    PipelineDriftError,
)

from app.core.security import create_access_token

pytestmark = pytest.mark.asyncio


def _headers(role: str = "client") -> dict[str, str]:
    """Auth header for a principal minted from a *fine* role."""
    token = create_access_token(
        user_id=None, username="pipelines", role=role, tenant_id=1
    )
    return {"Authorization": f"Bearer {token}"}


async def test_pipelines_serves_the_three_declarations(client):
    resp = await client.get("/pipelines", headers=_headers())
    assert resp.status_code == 200
    body = resp.json()

    assert [p["name"] for p in body["pipelines"]] == ["retrieval", "agent", "ingestion"]
    served = {p["name"]: p for p in body["pipelines"]}

    # The ingest stages served ARE the tuple the resume walks, in order.
    ingestion = served["ingestion"]
    assert [s["name"] for s in ingestion["stages"]] == list(stage_names())
    assert ingestion["durable_record"] == "run_events"
    assert {
        emission["name"]
        for stage in ingestion["stages"]
        for emission in stage["emits"]
        if emission["channel"] == "run_event"
    } == {INGEST_STAGE_EVENT_TYPE}

    # The agent pipeline declares honestly that nothing it emits is persisted — the
    # health page says the same thing, and now for the same reason.
    agent = served["agent"]
    assert agent["durable_record"] is None
    assert not [
        e for s in agent["stages"] for e in s["emits"] if e["channel"] == "run_event"
    ]
    assert any("run_events holds no agent rows" in limit for limit in agent["limits"])

    # The legend is served, so the console does not restate it.
    assert set(body["channels"]) == {"run_event", "stream", "result"}


async def test_pipelines_requires_authentication(client):
    assert (await client.get("/pipelines")).status_code == 401


async def test_every_module_a_stage_names_as_its_owner_imports():
    """Both halves of the platform: the ``aegis.*`` owners and the host's own.

    An owner that does not resolve is a reader sent to a file that is not there, which
    is the failure the whole task exists to prevent — one level down.
    """
    missing = sorted(
        {
            stage.owner
            for spec in PIPELINES
            for stage in spec.stages
            if importlib.util.find_spec(stage.owner) is None
        }
    )
    assert not missing, f"stages name owner modules that do not exist: {missing}"


async def test_the_ingest_log_takes_its_vocabulary_from_the_declaration():
    """The durable row's event type and ``seq`` are functions of the spec."""
    from app.jobs import ingest_log

    assert ingest_log.INGEST_STAGE_EVENT == INGEST_STAGE_EVENT_TYPE
    assert [
        ingest_log.stage_seq(name) for name in INGESTION_PIPELINE.stage_names
    ] == list(range(1, len(INGESTION_PIPELINE.stages) + 1))
    assert ingest_log.finished_seq() == len(INGESTION_PIPELINE.stages) + 1


async def test_a_pipeline_the_spec_does_not_declare_stops_the_ingest_writer(monkeypatch):
    """Grow the runtime pipeline without the spec: importing the writer must fail.

    The mutation that proves consumer 1 is real. Without it the worker would happily
    write a ``run_events`` row for a stage the API, the console and the docs have never
    heard of, and the first anyone would know is a gap in the ingest log.
    """
    from aegis.jobs import stages as stage_module

    from app.jobs import ingest_log

    grown = (
        *stage_module.INGEST_STAGES,
        StageSpec("summarise", timeout_seconds=600, max_attempts=2, task_queue=CPU_QUEUE),
    )
    monkeypatch.setattr(
        stage_module, "stage_names", lambda *_a, **_k: tuple(s.name for s in grown)
    )
    with pytest.raises(PipelineDriftError, match="summarise"):
        importlib.reload(ingest_log)
    # Leave the module as the rest of the session expects to find it.
    monkeypatch.undo()
    importlib.reload(ingest_log)
    assert ingest_log.INGEST_STAGE_EVENT == INGEST_STAGE_EVENT_TYPE
