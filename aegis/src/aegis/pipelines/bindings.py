"""Bind each declaration to the code it describes, and fail loudly when they disagree.

:mod:`aegis.pipelines.spec` is only worth having if the code **reads** it and the two
cannot drift. This module is the second half of that: four checks, each one tied to the
artefact the runtime actually uses, each one raising :class:`PipelineDriftError` with the
exact difference rather than logging a warning nobody reads.

* :func:`verify_ingestion_pipeline` — the declared stage names must equal
  :data:`aegis.jobs.stages.INGEST_STAGES`, in order. That tuple is what a **resume**
  walks and what ``documents.completed_stage`` is written from, so a spec that disagreed
  with it would mislabel a real durable row.
* :func:`verify_agent_pipeline` — the declared stage names and labels must equal
  :data:`aegis.agent.graph.NODE_LABELS`, the one table the ``_timed`` wrapper streams
  from, and every declared stream event must be a real builder in
  :mod:`aegis.agent.events`.
* :func:`verify_retrieval_pipeline` — the retrieval stages must between them account for
  **every** field of :class:`aegis.retrieval.models.RetrievalObservability`, and every
  ``result.*`` they name must be a field of
  :class:`aegis.retrieval.models.RetrievalResult`. Adding a measurement without saying
  which stage produces it is exactly the drift that makes an observability model
  unreadable.

**Why the imports are inside the functions.** :mod:`aegis.pipelines.spec` is stdlib-only
so a docs build, a workflow sandbox or an integrator with none of the extras installed
can still read the declaration. Verifying it needs LangGraph, SQLAlchemy and Pydantic —
so each check pays for its own imports at call time, and :func:`ingest_stage_order`, the
one the ingest worker calls at import, pulls only the stdlib-only stage contract.
"""

from __future__ import annotations

from aegis.pipelines.spec import (
    AGENT_PIPELINE,
    INGEST_STAGE_EVENT_TYPE,
    INGESTION_PIPELINE,
    PIPELINES,
    RETRIEVAL_PIPELINE,
    Channel,
    PipelineSpec,
)

__all__ = [
    "PipelineDriftError",
    "ingest_stage_order",
    "verify_agent_pipeline",
    "verify_ingestion_pipeline",
    "verify_pipelines",
    "verify_retrieval_pipeline",
]


class PipelineDriftError(RuntimeError):
    """A pipeline declaration and the code it describes have diverged.

    Raised rather than warned, and raised at the point the runtime reads the
    declaration, so the failure lands on whoever changed one side without the other
    instead of on a reader six weeks later looking at a console that quietly lies.
    """


def verify_ingestion_pipeline() -> tuple[str, ...]:
    """Check the ingestion declaration against the stage contract the resume walks.

    Returns:
        The declared stage names, in order — the same tuple
        :func:`aegis.jobs.stages.stage_names` returns, having been proved equal to it.

    Raises:
        PipelineDriftError: If the names or their order differ. Order is checked, not
            just membership: :func:`aegis.jobs.stages.remaining_stages` resumes *after*
            a named stage, so a reordered declaration would describe a resume that does
            not happen.
    """
    from aegis.jobs.stages import stage_names

    declared = INGESTION_PIPELINE.stage_names
    actual = stage_names()
    if declared != actual:
        raise PipelineDriftError(
            "the ingestion PipelineSpec and aegis.jobs.stages.INGEST_STAGES disagree: "
            f"the spec declares {declared}, the pipeline runs {actual}. Every ingest "
            "stage event is keyed on these names, so one of the two is writing rows "
            "nothing can read back."
        )
    return declared


def ingest_stage_order() -> dict[str, int]:
    """Return each ingest stage's 1-based sequence number, taken from the declaration.

    This is what makes the declaration load-bearing rather than descriptive:
    ``app.jobs.ingest_log`` builds its ``seq`` table from here, so the number written on
    a durable ``run_events`` row is a function of the declared pipeline. It is 1-based
    for the reason it always was — the close-out's number must be distinct from every
    stage's, and every event of a run must carry a positive ``seq``.

    Returns:
        Stage name to sequence number.

    Raises:
        PipelineDriftError: Via :func:`verify_ingestion_pipeline`, if the declaration
            and the stage contract disagree. The ingest log module calls this at import,
            so drift stops the worker rather than mislabelling rows.
    """
    return {name: index + 1 for index, name in enumerate(verify_ingestion_pipeline())}


def verify_agent_pipeline() -> tuple[str, ...]:
    """Check the agent declaration against the compiled graph's node table.

    Returns:
        The declared stage names, in declaration order.

    Raises:
        PipelineDriftError: If the declared stage set differs from
            :data:`aegis.agent.graph.NODE_LABELS`, if any label differs from the one the
            ``_timed`` wrapper streams, or if a declared stream event names something
            :mod:`aegis.agent.events` cannot build. The first is the drift that put nine
            nodes on the console's map; the last is what stops the docs promising an
            event no code emits.
    """
    from aegis.agent import events as agent_events
    from aegis.agent.graph import NODE_LABELS

    declared = {stage.name: stage.label for stage in AGENT_PIPELINE.stages}
    missing = sorted(set(NODE_LABELS) - set(declared))
    extra = sorted(set(declared) - set(NODE_LABELS))
    if missing or extra:
        raise PipelineDriftError(
            "the agent PipelineSpec and aegis.agent.graph.NODE_LABELS disagree: "
            f"undeclared graph nodes {missing}, declared non-nodes {extra}. A node "
            "the spec does not know is a node the console and the docs cannot draw."
        )
    mislabelled = {
        name: (label, NODE_LABELS[name])
        for name, label in declared.items()
        if label != NODE_LABELS[name]
    }
    if mislabelled:
        raise PipelineDriftError(
            "the agent PipelineSpec labels nodes differently from the labels the run "
            f"actually streams: {mislabelled} (declared, streamed)."
        )
    unbuildable = sorted(
        {
            emission.name
            for stage in AGENT_PIPELINE.stages
            for emission in stage.emissions_on(Channel.STREAM)
            if not callable(getattr(agent_events, emission.name, None))
        }
    )
    if unbuildable:
        raise PipelineDriftError(
            f"the agent PipelineSpec declares stream events {unbuildable} that "
            "aegis.agent.events has no builder for, so nothing can emit them."
        )
    return AGENT_PIPELINE.stage_names


def verify_retrieval_pipeline() -> tuple[str, ...]:
    """Check the retrieval declaration against the models it says it fills in.

    Returns:
        The declared stage names, in order.

    Raises:
        PipelineDriftError: If the stages do not between them claim every field of
            :class:`~aegis.retrieval.models.RetrievalObservability`, claim one that does
            not exist, or name a ``result.*`` field
            :class:`~aegis.retrieval.models.RetrievalResult` does not have.
            Completeness is checked in both directions on purpose: a measurement nobody
            claims is a measurement no reader can attribute to a stage.
    """
    from aegis.retrieval.models import RetrievalObservability, RetrievalResult

    claimed: dict[str, set[str]] = {"observability": set(), "result": set()}
    unknown_prefixes: set[str] = set()
    for stage in RETRIEVAL_PIPELINE.stages:
        for emission in stage.emissions_on(Channel.RESULT):
            prefix, _, field = emission.name.partition(".")
            if prefix not in claimed or not field:
                unknown_prefixes.add(emission.name)
                continue
            claimed[prefix].add(field)
    if unknown_prefixes:
        raise PipelineDriftError(
            f"the retrieval PipelineSpec names result fields {sorted(unknown_prefixes)} "
            "that are neither 'observability.<field>' nor 'result.<field>'."
        )
    observed = set(RetrievalObservability.model_fields)
    unclaimed = sorted(observed - claimed["observability"])
    invented = sorted(claimed["observability"] - observed)
    if unclaimed or invented:
        raise PipelineDriftError(
            "the retrieval PipelineSpec and RetrievalObservability disagree: fields no "
            f"stage claims to produce {unclaimed}, declared fields the model does not "
            f"have {invented}."
        )
    bad_result = sorted(claimed["result"] - set(RetrievalResult.model_fields))
    if bad_result:
        raise PipelineDriftError(
            f"the retrieval PipelineSpec declares result fields {bad_result} that "
            "RetrievalResult does not have."
        )
    return RETRIEVAL_PIPELINE.stage_names


def verify_pipelines() -> tuple[PipelineSpec, ...]:
    """Verify all three declarations and return them.

    This is what ``GET /pipelines`` calls, so the API cannot serve a declaration the
    process it is running in has already contradicted.

    Returns:
        :data:`aegis.pipelines.spec.PIPELINES`, verified.

    Raises:
        PipelineDriftError: On the first disagreement found.
    """
    verify_retrieval_pipeline()
    verify_agent_pipeline()
    verify_ingestion_pipeline()
    _verify_ingest_event_type()
    return PIPELINES


def _verify_ingest_event_type() -> None:
    """Check every ingest stage records itself under the one declared event type.

    Raises:
        PipelineDriftError: If any stage declares a different ``run_events.event_type``.
            The projection, the health aggregation and the console all filter on one
            constant; a second type would make a stage's row invisible to all three.
    """
    types = {
        emission.name
        for stage in INGESTION_PIPELINE.stages
        for emission in stage.emissions_on(Channel.RUN_EVENT)
    }
    if types != {INGEST_STAGE_EVENT_TYPE}:
        raise PipelineDriftError(
            f"the ingestion PipelineSpec records stages under {sorted(types)}; the "
            f"projection and the health aggregation both filter on "
            f"{INGEST_STAGE_EVENT_TYPE!r} alone, so anything else is a row nothing "
            "reads back."
        )
