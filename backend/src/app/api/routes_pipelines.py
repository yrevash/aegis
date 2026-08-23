"""``GET /pipelines`` — the three pipeline declarations, served from the declaration.

**The second consumer of one declaration** (task 8.12). :mod:`aegis.pipelines` declares
what each of Aegis's three pipelines — retrieval, agent, ingestion — is made of: its
stages, the module that owns each stage, and what each stage puts on a wire or a row.
Four things read that declaration: the ingest runtime (``app.jobs.ingest_log`` takes the
``run_events`` event type and the per-stage sequence numbers from it), this route, the
console's pipeline-health page, and ``docs/module/PIPELINES.md``.

**This route verifies before it serves.** :func:`aegis.pipelines.verify_pipelines`
re-checks the declaration against the code it describes — the ingest stage tuple, the
agent graph's node labels, the retrieval observability model — and raises
:class:`~aegis.pipelines.PipelineDriftError` on a disagreement. Serving a declaration the
process has already contradicted is the exact failure ``/agent/topology`` was built to
end, and the console draws from this answer.

**A new module rather than another hundred lines of** :mod:`app.api.routes`, mounted from
the composition root by :func:`mount`, exactly as ``routes_redteam`` and ``routes_reports``
are — and by extending ``target.routes`` rather than ``include_router``, because
FastAPI 0.141's lazy inclusion hides a router's children from anything that *enumerates*
the route table, which is how a route escapes ``tests/api/test_route_coverage.py``.

**Guarded by** :func:`app.api.routes.require_auth` **and nothing narrower.** There is no
tenant data here — the response is the same bytes for every caller, and it is the shape
of the product, not anyone's corpus. It is behind authentication because the console is,
and because an unauthenticated map of the platform's internals is a reconnaissance gift
for no gain.
"""

from __future__ import annotations

from aegis.pipelines import CHANNEL_MEANING, verify_pipelines
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.routes import AuthContext, require_auth

__all__ = [
    "EmissionModel",
    "PipelineModel",
    "PipelineStageModel",
    "PipelinesResponse",
    "mount",
    "pipelines_router",
]

pipelines_router = APIRouter()


class EmissionModel(BaseModel):
    """One thing a stage emits, and the channel that decides what may be asked of it."""

    channel: str = Field(
        description="run_event (committed, replayable) | stream (SSE, not persisted) | "
        "result (a field on the returned object)."
    )
    name: str = Field(
        description="The wire name: an event type, or a dotted field path on the result."
    )
    detail: str = Field(description="What a reader learns from it, in one line.")


class PipelineStageModel(BaseModel):
    """One stage: what runs, which module owns it, and what it emits."""

    name: str = Field(description="The stable stage id, as a row or an event spells it.")
    label: str = Field(description="The human label; for the agent, the streamed label.")
    owner: str = Field(description="The dotted module that implements the stage.")
    summary: str = Field(description="What the stage does.")
    optional: bool = Field(
        description="True when the stage runs only under a configuration or a route, so "
        "a reader does not expect it in every trace."
    )
    emits: list[EmissionModel] = Field(default_factory=list)


class PipelineModel(BaseModel):
    """One declared pipeline."""

    name: str
    title: str
    summary: str
    entrypoint: str = Field(description="The dotted callable that runs it.")
    durable_record: str | None = Field(
        description="The table its stage transitions commit to, or null when it "
        "persists nothing. Null is a promise, not an omission: such a pipeline may not "
        "declare a persisted emission anywhere."
    )
    stages: list[PipelineStageModel] = Field(default_factory=list)
    limits: list[str] = Field(
        default_factory=list,
        description="What this pipeline does not record, stated beside the figures it "
        "does — the same discipline as not_recorded on the health surfaces.",
    )


class PipelinesResponse(BaseModel):
    """Body for ``GET /pipelines``.

    ``channels`` is the legend, served rather than hardcoded in the browser for the same
    reason everything else here is: a console that spelled out what ``stream`` means
    would be a second copy of a sentence that lives in the declaration.
    """

    pipelines: list[PipelineModel] = Field(default_factory=list)
    channels: dict[str, str] = Field(default_factory=dict)


@pipelines_router.get(
    "/pipelines", response_model=PipelinesResponse, tags=["platform"]
)
async def list_pipelines(
    auth: AuthContext = Depends(require_auth),
) -> PipelinesResponse:
    """Return every pipeline Aegis runs, its stages, and what each stage emits.

    Three pipelines, not twenty-nine: a module is not a pipeline. The twenty-nine-module course
    in ``docs/teaching/`` explains the parts; this is the flows they compose into.

    The declaration is **verified against the code before it is served** — the ingest
    stage tuple a resume walks, the agent graph's own node labels, and the retrieval
    observability model's fields — so this endpoint cannot describe a pipeline the
    process is not running.

    Args:
        auth: The authenticated principal. Every role sees the same answer: there is no
            tenant data in the shape of the product.

    Returns:
        The three declarations plus the channel legend.

    Raises:
        aegis.pipelines.PipelineDriftError: If a declaration and the code it describes
            have diverged. Deliberately not caught: a 500 naming the exact difference is
            a better answer than a diagram that is quietly wrong, which is the failure
            the hardcoded orchestration map shipped for months.
    """
    del auth  # the answer is identical for every principal; the guard is the point
    return PipelinesResponse(
        pipelines=[
            PipelineModel(
                name=spec.name,
                title=spec.title,
                summary=spec.summary,
                entrypoint=spec.entrypoint,
                durable_record=spec.durable_record,
                stages=[
                    PipelineStageModel(
                        name=stage.name,
                        label=stage.label,
                        owner=stage.owner,
                        summary=stage.summary,
                        optional=stage.optional,
                        emits=[
                            EmissionModel(
                                channel=emission.channel.value,
                                name=emission.name,
                                detail=emission.detail,
                            )
                            for emission in stage.emits
                        ],
                    )
                    for stage in spec.stages
                ],
                limits=list(spec.limits),
            )
            for spec in verify_pipelines()
        ],
        channels={channel.value: note for channel, note in CHANNEL_MEANING.items()},
    )


def mount(target: APIRouter) -> None:
    """Attach this module's routes to ``target`` as real ``APIRoute`` objects.

    Idempotent, exactly as :func:`app.api.routes_reports.mount` is and for the same
    reason: the composition root mounts several of these while :mod:`app.api.routes` is
    edited elsewhere, and a second shadowed copy of a handler is invisible at runtime
    and confusing in the route-coverage analysis.

    Args:
        target: The application's main router, extended in place.
    """
    present = {
        (route.path, frozenset(getattr(route, "methods", ()) or ()))
        for route in target.routes
    }
    target.routes.extend(
        route
        for route in pipelines_router.routes
        if (route.path, frozenset(getattr(route, "methods", ()) or ())) not in present
    )
