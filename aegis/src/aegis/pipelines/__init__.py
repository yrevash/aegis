"""The three pipelines Aegis runs, declared once and read by four consumers.

``retrieval``, ``agent``, ``ingestion`` — what each one's stages are, which module owns
each stage, and what each stage emits. See :mod:`aegis.pipelines.spec` for the
declaration and why it exists, and :mod:`aegis.pipelines.bindings` for the checks that
make a declaration which disagrees with the code raise instead of mislead.

``python -m aegis.pipelines`` renders ``docs/module/PIPELINES.md`` from the declaration.
"""

from __future__ import annotations

from aegis.pipelines.bindings import (
    PipelineDriftError,
    ingest_stage_order,
    verify_agent_pipeline,
    verify_ingestion_pipeline,
    verify_pipelines,
    verify_retrieval_pipeline,
)
from aegis.pipelines.spec import (
    AGENT_PIPELINE,
    CHANNEL_MEANING,
    INGEST_FINISHED_EVENT_TYPE,
    INGEST_STAGE_EVENT_TYPE,
    INGESTION_PIPELINE,
    PIPELINES,
    RETRIEVAL_PIPELINE,
    Channel,
    Emission,
    PipelineSpec,
    PipelineStage,
    UnknownPipelineError,
    UnknownPipelineStageError,
    pipeline_spec,
)

__all__ = [
    "AGENT_PIPELINE",
    "CHANNEL_MEANING",
    "INGEST_FINISHED_EVENT_TYPE",
    "INGEST_STAGE_EVENT_TYPE",
    "INGESTION_PIPELINE",
    "PIPELINES",
    "RETRIEVAL_PIPELINE",
    "Channel",
    "Emission",
    "PipelineDriftError",
    "PipelineSpec",
    "PipelineStage",
    "UnknownPipelineError",
    "UnknownPipelineStageError",
    "ingest_stage_order",
    "pipeline_spec",
    "verify_agent_pipeline",
    "verify_ingestion_pipeline",
    "verify_pipelines",
    "verify_retrieval_pipeline",
]
