"""The declaration must fail when it disagrees with the code — proved by mutation.

A ``PipelineSpec`` that merely restated string literals would buy nothing: it would be a
second copy of the stage names, free to drift exactly as the console's hardcoded
orchestration DAG drifted from :mod:`aegis.agent.graph`. What makes it a contract is
:mod:`aegis.pipelines.bindings`, and what proves the binding is real is breaking it.

So each check below is exercised by **changing the runtime and watching the declaration
refuse it** — a node the graph gained, a stage the pipeline gained, an observability
field nobody claims to produce. A test that only asserted the happy path would pass just
as well with the verification deleted.

Nothing here touches a database, a model or the network: the declaration is stdlib data
and every binding reads an already-imported table.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from aegis.jobs.stages import CPU_QUEUE, StageSpec
from aegis.pipelines import (
    AGENT_PIPELINE,
    INGEST_STAGE_EVENT_TYPE,
    INGESTION_PIPELINE,
    RETRIEVAL_PIPELINE,
    Channel,
    Emission,
    PipelineDriftError,
    PipelineSpec,
    PipelineStage,
    verify_agent_pipeline,
    verify_ingestion_pipeline,
    verify_pipelines,
    verify_retrieval_pipeline,
)
from aegis.pipelines.docs import render_markdown

#: The generated reference, which must stay byte-identical to the declaration.
_REFERENCE = Path(__file__).resolve().parents[3] / "docs" / "module" / "PIPELINES.md"


def test_the_three_declarations_bind_to_the_code_they_describe():
    """All three verify against the real stage tuple, graph and models."""
    assert [spec.name for spec in verify_pipelines()] == [
        "retrieval",
        "agent",
        "ingestion",
    ]
    # The ingest stage names ARE the tuple a resume walks, in order.
    assert verify_ingestion_pipeline() == INGESTION_PIPELINE.stage_names
    # Every ingest stage records itself under the one type the projection filters on.
    assert {
        emission.name
        for stage in INGESTION_PIPELINE.stages
        for emission in stage.emissions_on(Channel.RUN_EVENT)
    } == {INGEST_STAGE_EVENT_TYPE}


def test_a_stage_the_pipeline_gains_but_the_spec_does_not_is_drift(monkeypatch):
    """Add a seventh ingest stage to the runtime; the declaration must refuse it.

    This is the mutation behind the whole task: ``documents.completed_stage`` would then
    hold a name the spec, the API, the console and the docs have never heard of.
    """
    from aegis.jobs import stages as stage_module

    grown = (
        *stage_module.INGEST_STAGES,
        StageSpec("summarise", timeout_seconds=600, max_attempts=2, task_queue=CPU_QUEUE),
    )
    monkeypatch.setattr(
        stage_module, "stage_names", lambda *_a, **_k: tuple(s.name for s in grown)
    )
    with pytest.raises(PipelineDriftError, match="summarise"):
        verify_ingestion_pipeline()


def test_a_node_the_graph_gains_or_relabels_is_drift(monkeypatch):
    """The agent spec is pinned to the label table the run actually streams."""
    from aegis.agent import graph as graph_module

    real = dict(graph_module.NODE_LABELS)

    monkeypatch.setattr(
        graph_module, "NODE_LABELS", {**real, "critique": "Critique the answer"}
    )
    with pytest.raises(PipelineDriftError, match="critique"):
        verify_agent_pipeline()

    # And a node that keeps its id but changes the label the run streams: the console
    # would then print a word no event carries.
    monkeypatch.setattr(graph_module, "NODE_LABELS", {**real, "gate": "Approval gate"})
    with pytest.raises(PipelineDriftError, match="Approval gate"):
        verify_agent_pipeline()


def test_a_stream_event_no_builder_can_produce_is_drift(monkeypatch):
    """A declared event name must be something ``aegis.agent.events`` can build."""
    from aegis.agent import events as event_module

    monkeypatch.delattr(event_module, "retrieval")
    with pytest.raises(PipelineDriftError, match="retrieval"):
        verify_agent_pipeline()


def test_an_observability_field_no_stage_claims_is_drift(monkeypatch):
    """A new measurement must be attributed to the stage that produces it."""
    from aegis.retrieval import models as retrieval_models

    class Grown(retrieval_models.RetrievalObservability):
        dedup_dropped: int = 0

    monkeypatch.setattr(retrieval_models, "RetrievalObservability", Grown)
    with pytest.raises(PipelineDriftError, match="dedup_dropped"):
        verify_retrieval_pipeline()


def test_a_pipeline_that_persists_nothing_cannot_claim_a_persisted_emission():
    """The agent pipeline's honesty is structural, not a comment.

    ``run_events`` holds no agent rows — the ingest stage log is its only writer — so the
    agent spec declares ``durable_record=None``. A spec in that state may not declare a
    ``run_event`` emission anywhere, which is what stops the claim being walked back one
    stage at a time later.
    """
    assert AGENT_PIPELINE.durable_record is None
    assert RETRIEVAL_PIPELINE.durable_record is None
    assert not [
        emission
        for spec in (AGENT_PIPELINE, RETRIEVAL_PIPELINE)
        for stage in spec.stages
        for emission in stage.emissions_on(Channel.RUN_EVENT)
    ]
    with pytest.raises(ValueError, match="durable_record=None"):
        PipelineSpec(
            name="wishful",
            title="Wishful",
            summary="claims a durable row it never writes",
            entrypoint="nowhere.run",
            durable_record=None,
            stages=(
                PipelineStage(
                    name="only",
                    label="Only",
                    owner="nowhere",
                    summary="pretends",
                    emits=(Emission(Channel.RUN_EVENT, "made_up", "never written"),),
                ),
            ),
        )


def test_every_aegis_module_a_stage_names_as_its_owner_exists():
    """A stage's owner must be a module an integrator can actually go and read."""
    missing = sorted(
        {
            stage.owner
            for spec in (RETRIEVAL_PIPELINE, AGENT_PIPELINE, INGESTION_PIPELINE)
            for stage in spec.stages
            if stage.owner.startswith("aegis.")
            and importlib.util.find_spec(stage.owner) is None
        }
    )
    assert not missing, f"stages name owner modules that do not exist: {missing}"


def test_the_generated_reference_is_the_declaration():
    """``docs/module/PIPELINES.md`` is generated, and a stale copy fails here.

    The same shape as the console's offline topology snapshot: the last place a second,
    drifting copy of the pipeline could hide is a document nobody regenerates.
    """
    if not _REFERENCE.is_file():  # pragma: no cover - aegis checked out standalone
        pytest.skip(f"reference not present at {_REFERENCE}")
    assert _REFERENCE.read_text() == render_markdown(), (
        "docs/module/PIPELINES.md is stale; regenerate it with "
        "`python -m aegis.pipelines > docs/module/PIPELINES.md`"
    )
