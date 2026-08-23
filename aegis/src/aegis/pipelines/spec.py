"""What a pipeline *is*, declared once — stages, owners, and what each stage emits.

Aegis runs three pipelines. **Three, not twenty-nine**: a module is not a pipeline, and the
twenty-nine-module course in ``docs/teaching/`` is the explanation of the parts, not a
description of the flows. The flows are:

* ``retrieval`` — a question becomes an answer context (:mod:`aegis.retrieval.pipeline`);
* ``agent`` — a turn becomes a streamed answer (:mod:`aegis.agent.graph`);
* ``ingestion`` — a file becomes a searchable, graph-linked corpus
  (:data:`aegis.jobs.stages.INGEST_STAGES`).

**Why a declaration and not a diagram.** ``/agent/topology`` already proved the pattern:
the console's orchestration map used to hardcode its own DAG and drifted to nine nodes
and a human gate hanging off a step that decided nothing, and the fix was to serve the
shape *from the compiled graph* with a label tripwire behind it. The same drift was
live in two more places — ``CacheView`` carried a hardcoded ``SPECS`` array until §7.10b
deleted it, and the pipeline-health page derives its stage vocabulary from whatever rows
happened to land in ``run_events``, so a stage that has never run is invisible rather
than idle.

So this module declares each pipeline once, and four consumers read the one declaration:

1. **the runtime** — ``app.jobs.ingest_log`` takes the ``run_events`` event type and the
   per-stage sequence numbers from :data:`INGESTION_PIPELINE` rather than from string
   literals, so the vocabulary on the durable row *is* the declared vocabulary;
2. **the API** — ``GET /pipelines``;
3. **the console** — the pipeline-health page, which now lists every declared stage and
   marks the ones with no timing rather than silently omitting them;
4. **the docs** — ``docs/module/PIPELINES.md``, regenerated from here by
   ``python -m aegis.pipelines``.

**A declaration that only restates string literals buys nothing**, so it is bound to the
code in :mod:`aegis.pipelines.bindings`: the ingestion stage names must equal
:data:`aegis.jobs.stages.INGEST_STAGES`, the agent stage names and labels must equal
:data:`aegis.agent.graph.NODE_LABELS`, every streamed event name must be a real builder
in :mod:`aegis.agent.events`, and the retrieval stages must between them account for
**every** field of :class:`aegis.retrieval.models.RetrievalObservability`. A pipeline
that disagrees with its runtime raises :class:`~aegis.pipelines.bindings.PipelineDriftError`.

**This module is stdlib-only**, exactly as :mod:`aegis.jobs.stages` is and for a related
reason: a declaration everything reads must be importable from everywhere, including a
documentation build with no database, no model client and no graph library installed.
The verification that needs those imports lives next door in
:mod:`aegis.pipelines.bindings`, which imports them lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "AGENT_PIPELINE",
    "CHANNEL_MEANING",
    "INGESTION_PIPELINE",
    "PIPELINES",
    "RETRIEVAL_PIPELINE",
    "Channel",
    "Emission",
    "PipelineSpec",
    "INGEST_FINISHED_EVENT_TYPE",
    "INGEST_STAGE_EVENT_TYPE",
    "PipelineStage",
    "UnknownPipelineError",
    "UnknownPipelineStageError",
    "pipeline_spec",
]


class UnknownPipelineError(LookupError):
    """No pipeline is declared under that name."""


class UnknownPipelineStageError(LookupError):
    """A pipeline was asked for a stage it does not declare.

    An error rather than a shrug, for the reason
    :class:`aegis.jobs.stages.UnknownStageError` is one: the caller is writing a durable
    row keyed on that name, and guessing would file the work under a stage the pipeline
    does not have.
    """


class Channel(StrEnum):
    """Where a stage's output actually goes — and therefore what can be asked of it.

    The three values are not decoration. They are the difference between a figure the
    platform can show you an hour later and a figure that existed for one HTTP
    connection, and conflating them is how a console ends up promising per-stage
    timings for a pipeline that persists none.
    """

    #: A committed row in ``run_events``, inside the transaction that did the work.
    #: Replayable: a poll an hour later returns the same answer.
    RUN_EVENT = "run_event"
    #: An AG-UI frame on the SSE stream. It reaches the browser and is then gone —
    #: nothing writes it to a table.
    STREAM = "stream"
    #: A field on the object the call returns. In-band, in-process, not persisted.
    RESULT = "result"


#: What each channel means, in one line, for a reader who is not holding the code.
#:
#: Declared beside the enum rather than in the console or the doc generator because both
#: render it, and a legend that disagreed with the channel it explains would be the same
#: drift in miniature.
CHANNEL_MEANING: dict[Channel, str] = {
    Channel.RUN_EVENT: (
        "committed to run_events in the transaction that did the work — replayable, "
        "readable an hour later"
    ),
    Channel.STREAM: (
        "an AG-UI frame on the SSE stream — it reaches the browser and is then gone; "
        "nothing persists it"
    ),
    Channel.RESULT: "a field on the returned object — in-process, not persisted",
}


@dataclass(frozen=True, slots=True)
class Emission:
    """One thing a stage emits, and the channel it emits it on.

    Attributes:
        channel: Where it goes; see :class:`Channel`.
        name: The wire name. For :attr:`Channel.STREAM` this is the event ``type`` and
            must be a builder in :mod:`aegis.agent.events`; for :attr:`Channel.RESULT`
            it is a dotted field path on the returned model (``result.cache_hit``,
            ``observability.arms``); for :attr:`Channel.RUN_EVENT` it is the
            ``run_events.event_type``.
        detail: What the reader learns from it, in one line.
    """

    channel: Channel
    name: str
    detail: str

    def __post_init__(self) -> None:
        """Reject an emission with no name.

        Raises:
            ValueError: If ``name`` is blank. An unnamed emission cannot be bound to
                anything, which defeats the point of declaring it.
        """
        if not self.name.strip():
            raise ValueError("an emission must name what it emits")


@dataclass(frozen=True, slots=True)
class PipelineStage:
    """One stage of one pipeline: what runs, who owns it, and what it emits.

    Attributes:
        name: The stage's stable id — the string that appears on a ``run_events`` row,
            on a stream event's ``node``, or in the docs.
        label: The human label. For the agent pipeline this must equal the label the
            ``_timed`` wrapper streams from :data:`aegis.agent.graph.NODE_LABELS`.
        owner: The dotted module that implements the stage. Bound: the module must
            import.
        summary: What the stage does, in one sentence.
        emits: Everything the stage puts on a wire or a row. May be empty — a stage
            that emits nothing observable is a fact worth declaring, not an omission.
        optional: True when the stage runs only under a configuration or a route
            (a rewrite layer, an approval interrupt), so a reader does not expect it in
            every trace.
    """

    name: str
    label: str
    owner: str
    summary: str
    emits: tuple[Emission, ...] = ()
    optional: bool = False

    def __post_init__(self) -> None:
        """Validate the stage where the fix is cheap — at import.

        Raises:
            ValueError: If the name, label, owner or summary is blank. Each one is
                rendered to a reader or bound to code; a blank is a silent hole in
                both.
        """
        for field_name in ("name", "label", "owner", "summary"):
            if not getattr(self, field_name).strip():
                raise ValueError(f"stage {self.name!r}: {field_name} must not be blank")

    def emissions_on(self, channel: Channel) -> tuple[Emission, ...]:
        """Return this stage's emissions on one channel.

        Args:
            channel: The channel to filter to.

        Returns:
            The matching emissions, in declaration order.
        """
        return tuple(e for e in self.emits if e.channel is channel)


@dataclass(frozen=True, slots=True)
class PipelineSpec:
    """One pipeline, declared once.

    Attributes:
        name: The pipeline's id — ``retrieval``, ``agent`` or ``ingestion``.
        title: The human title.
        summary: What the pipeline turns into what.
        entrypoint: The dotted callable a caller invokes to run it.
        durable_record: The table its stage transitions are committed to, or ``None``
            when it persists nothing. ``None`` is enforced, not merely documented: a
            pipeline that records nothing may not declare a
            :attr:`Channel.RUN_EVENT` emission anywhere, so the honest answer cannot be
            walked back one stage at a time.
        stages: The stages, in the order a reader should read them.
        limits: What this pipeline does **not** record, stated where the figures are —
            the same discipline as ``not_recorded`` on the health surfaces.
    """

    name: str
    title: str
    summary: str
    entrypoint: str
    durable_record: str | None
    stages: tuple[PipelineStage, ...]
    limits: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate the pipeline at import.

        Raises:
            ValueError: If it declares no stage, declares one name twice, or claims a
                durable ``run_events`` emission while declaring no durable record. The
                duplicate check matters for the reason
                :func:`aegis.jobs.stages._reject_duplicate_names` exists: a lookup finds
                the first match, so a duplicate makes one of the two invisible.
        """
        if not self.stages:
            raise ValueError(f"pipeline {self.name!r} declares no stages")
        seen: set[str] = set()
        for stage in self.stages:
            if stage.name in seen:
                raise ValueError(
                    f"pipeline {self.name!r} declares stage {stage.name!r} twice; a "
                    "lookup would find the first and the second would be unreachable"
                )
            seen.add(stage.name)
        if self.durable_record is None:
            persisted = [
                f"{stage.name}:{emission.name}"
                for stage in self.stages
                for emission in stage.emissions_on(Channel.RUN_EVENT)
            ]
            if persisted:
                raise ValueError(
                    f"pipeline {self.name!r} declares durable_record=None but claims "
                    f"persisted emissions {persisted}. One of the two is a lie, and the "
                    "expensive one to ship is the claim that a figure survives the "
                    "request."
                )

    @property
    def stage_names(self) -> tuple[str, ...]:
        """The stage names, in declaration order."""
        return tuple(stage.name for stage in self.stages)

    def stage(self, name: str) -> PipelineStage:
        """Return one stage by name.

        Args:
            name: The stage id.

        Returns:
            The declared stage.

        Raises:
            UnknownPipelineStageError: If this pipeline declares no such stage.
        """
        for stage in self.stages:
            if stage.name == name:
                return stage
        raise UnknownPipelineStageError(
            f"pipeline {self.name!r} declares no stage {name!r}; its stages are "
            f"{self.stage_names}"
        )


def _stream(name: str, detail: str) -> Emission:
    """Build a :attr:`Channel.STREAM` emission.

    Args:
        name: The AG-UI event ``type``.
        detail: What the reader learns from it.

    Returns:
        The emission.
    """
    return Emission(Channel.STREAM, name, detail)


def _result(name: str, detail: str) -> Emission:
    """Build a :attr:`Channel.RESULT` emission.

    Args:
        name: The dotted field path on the returned model.
        detail: What the reader learns from it.

    Returns:
        The emission.
    """
    return Emission(Channel.RESULT, name, detail)


#: The ``run_events.event_type`` one committed ingest stage is recorded under.
#:
#: Declared here rather than in the host because the host **reads it from here**:
#: ``app.jobs.ingest_log.INGEST_STAGE_EVENT`` is this string, and the projection, the
#: health aggregation and the console all key on that constant. That is what makes the
#: declaration load-bearing instead of descriptive.
INGEST_STAGE_EVENT_TYPE = "ingest_stage"

#: The ``run_events.event_type`` an ingest's close-out is recorded under.
INGEST_FINISHED_EVENT_TYPE = "ingest_finished"


def _ingest_row(detail: str) -> Emission:
    """Build the ``ingest_stage`` row emission every ingest stage writes.

    Args:
        detail: What this particular stage's row carries beyond the common envelope.

    Returns:
        The emission.
    """
    return Emission(Channel.RUN_EVENT, INGEST_STAGE_EVENT_TYPE, detail)


RETRIEVAL_PIPELINE = PipelineSpec(
    name="retrieval",
    title="Retrieval",
    summary=(
        "A question becomes an answer context: cache, hybrid wide recall, fusion, "
        "rerank, spotlighted assembly."
    ),
    entrypoint="aegis.retrieval.pipeline.Retriever.retrieve",
    durable_record=None,
    stages=(
        PipelineStage(
            name="rewrite",
            label="Rewrite the query",
            owner="aegis.retrieval.query_rewrite",
            summary=(
                "Expand or decompose the query before recall. A layer above "
                "retrieve(), so it is absent from a bare call."
            ),
            emits=(
                _result(
                    "observability.rewrite",
                    "which rewrites were tried and which one recall actually ran on",
                ),
            ),
            optional=True,
        ),
        PipelineStage(
            name="cache",
            label="Two-tier semantic cache",
            owner="aegis.retrieval.cache",
            summary=(
                "Exact lookup, then a cosine near-match above the semantic threshold. "
                "Both tiers are partitioned by the tenant scope, not filtered after "
                "the fact. A hit returns here and every stage below is skipped."
            ),
            emits=(
                _result("result.cache_hit", "whether this answer came from the cache"),
                _result(
                    "result.provenance",
                    "the cache lineage — which tier answered, and at what similarity",
                ),
            ),
        ),
        PipelineStage(
            name="recall",
            label="Hybrid wide recall",
            owner="aegis.retrieval.pipeline",
            summary=(
                "Run the dense, graph and keyword arms against the backend, each "
                "carrying the tenant predicate into the rows it scans."
            ),
            emits=(
                _result(
                    "observability.arms",
                    "one row per arm that genuinely recalled, with its candidate count",
                ),
                _result(
                    "observability.keyword",
                    "whether BM25 was a corpus-wide recall arm or a re-ranking pass — "
                    "the distinction the arm list refuses to blur",
                ),
            ),
        ),
        PipelineStage(
            name="fuse",
            label="Reciprocal rank fusion",
            owner="aegis.retrieval.fusion",
            summary="Fuse the arms' ranked lists into one pool — the honest N.",
            emits=(
                _result("observability.fusion", "the fusion method actually applied"),
                _result("observability.fused_candidates", "the fused pool size"),
                _result(
                    "result.num_candidates",
                    "the same N, on the result, so a caller can show N recalled -> K kept",
                ),
            ),
        ),
        PipelineStage(
            name="rerank",
            label="Cross-encoder rerank",
            owner="aegis.retrieval.local_reranker",
            summary=(
                "Grade the pool with the local ONNX cross-encoder, with the API "
                "reranker behind it on a loud failure."
            ),
            emits=(
                _result(
                    "observability.rerank",
                    "whether it ran, which engine graded, how many survived, and why "
                    "it degraded if it did",
                ),
            ),
        ),
        PipelineStage(
            name="spotlight",
            label="Spotlight the context",
            owner="aegis.retrieval.spotlight",
            summary=(
                "Delimit retrieved text so the generator cannot mistake corpus content "
                "for instructions."
            ),
            emits=(
                _result(
                    "observability.spotlight_applied",
                    "whether the answer context was spotlighted",
                ),
            ),
        ),
        PipelineStage(
            name="assemble",
            label="Assemble the answer context",
            owner="aegis.retrieval.pipeline",
            summary=(
                "Build the context and the citation-grade sources, and carry the graph "
                "nodes and edges the recall touched."
            ),
            emits=(
                _result("result.answer_context", "the context handed to the generator"),
                _result("result.sources", "the K survivors, citation-grade"),
                _result("result.graph_delta", "the graph nodes and edges recall touched"),
                _result(
                    "result.query_vec_dim",
                    "the dimensionality of the embedding this turn computed, or null "
                    "on a cache hit that computed none",
                ),
            ),
        ),
        PipelineStage(
            name="agentic",
            label="Self-RAG loop",
            owner="aegis.retrieval.agentic",
            summary=(
                "Re-query on an insufficient context, bounded. A layer above "
                "retrieve(), so it is absent from a single-shot call."
            ),
            emits=(
                _result(
                    "observability.agentic",
                    "how many iterations ran and what stopped the loop",
                ),
            ),
            optional=True,
        ),
    ),
    limits=(
        "No per-stage timing is recorded anywhere. Retrieval returns counts and "
        "verdicts, not durations; a stage latency would need a timer this pipeline "
        "does not have.",
        "Nothing here is written to run_events. Every figure above lives on the "
        "returned RetrievalResult and is gone when the caller drops it.",
    ),
)


AGENT_PIPELINE = PipelineSpec(
    name="agent",
    title="Agent",
    summary=(
        "A turn becomes a streamed answer: guardrails, a supervisor route, retrieval, "
        "planning, a risk gate with a human interrupt, tools, and a generated answer."
    ),
    entrypoint="aegis.agent.orchestrator.run_agent",
    durable_record=None,
    stages=(
        PipelineStage(
            name="guard_input",
            label="Input guardrail",
            owner="aegis.agent.rails",
            summary=(
                "Screen the turn before anything else runs. This is the one stage that "
                "is both the entry point and a terminal: a blocked input ends the run."
            ),
            emits=(_stream("guardrail", "the rail verdict, its layer, and any redactions"),),
        ),
        PipelineStage(
            name="route",
            label="Route intent",
            owner="aegis.agent.router",
            summary=(
                "Classify the turn to a roster specialist, or to the adaptive fan-out."
            ),
            emits=(_stream("routing", "the specialist chosen and the confidence behind it"),),
        ),
        PipelineStage(
            name="answer_memory",
            label="Answer from memory",
            owner="aegis.agent.graph",
            summary=(
                "The memory specialist: answer a self-referential turn from long-term "
                "memory, skipping retrieval, planning and tools entirely."
            ),
            emits=(_stream("memory", "what was recalled and used"),),
        ),
        PipelineStage(
            name="recall_memory",
            label="Recall memory",
            owner="aegis.memory",
            summary=(
                "Load the subject's memory for the qa lane. Wired plain rather than "
                "through the timing wrapper, so a turn with memory inactive emits "
                "nothing at all here."
            ),
            emits=(_stream("memory", "what was recalled, when anything was"),),
        ),
        PipelineStage(
            name="plan_team",
            label="Plan the team",
            owner="aegis.agent.team",
            summary="Allocate the fan-out width for a turn the router sent to the team lane.",
            emits=(
                _stream("reasoning", "the allocation, in sentences"),
                _stream("agent_status", "each allocated agent, before it starts"),
            ),
            optional=True,
        ),
        PipelineStage(
            name="run_team",
            label="Run agents concurrently",
            owner="aegis.agent.subagent",
            summary=(
                "Run the allocated agents concurrently inside one node — an "
                "asyncio.gather, not a subgraph — each narrating its own lane."
            ),
            emits=(
                _stream("agent_status", "per-lane lifecycle"),
                _stream("node_started", "a lane's node, prefixed to its agent"),
                _stream("node_finished", "the same, with the lane's duration and spend"),
                _stream("reasoning", "a lane's plan"),
                _stream("tool_call", "a lane's proposed call, with its risk tier"),
                _stream("tool_result", "a lane's result"),
            ),
            optional=True,
        ),
        PipelineStage(
            name="synthesize",
            label="Synthesise findings",
            owner="aegis.agent.team",
            summary="Merge the lanes into one answer draft.",
            emits=(_stream("synthesis", "which lanes contributed"),),
            optional=True,
        ),
        PipelineStage(
            name="retrieve",
            label="Agentic retrieval",
            owner="aegis.retrieval.agentic",
            summary=(
                "Run the retrieval pipeline for this turn. This is the seam between "
                "the two pipelines: everything the retrieval spec declares happens "
                "inside this one node."
            ),
            emits=(
                _stream("retrieval", "the candidate count, scored sources and graph delta"),
                _stream("provenance", "the origins and fusion method behind the context"),
                _stream("reasoning", "what the retrieval decided, in sentences"),
            ),
        ),
        PipelineStage(
            name="plan",
            label="Reason & plan",
            owner="aegis.agent.graph",
            summary="Produce the plan and the tool calls it proposes.",
            emits=(_stream("reasoning", "the plan, chunked into sentences"),),
        ),
        PipelineStage(
            name="gate",
            label="Risk gate",
            owner="aegis.agent.graph",
            summary=(
                "Decide on **tool risk** whether the proposed calls need a human. This "
                "is the only branch into approval, and the console's map is checked "
                "against that fact by GET /agent/topology."
            ),
        ),
        PipelineStage(
            name="approval",
            label="Human approval",
            owner="aegis.agent.approvals",
            summary=(
                "Pause the run until a human decides, enumerating every call that will "
                "execute. The node itself emits nothing: it re-executes on resume, so "
                "the orchestrator emits approval_required from the interrupt value "
                "exactly once instead."
            ),
            emits=(
                _stream("approval_required", "emitted by the orchestrator, not the node"),
                _stream("approval_queued", "when the run is parked to a durable inbox"),
            ),
            optional=True,
        ),
        PipelineStage(
            name="act",
            label="Execute actions",
            owner="aegis.agent.graph",
            summary="Execute exactly the calls the gate — or the human — admitted.",
            emits=(
                _stream("tool_call", "the call, its arguments and its risk tier"),
                _stream("tool_result", "whether it succeeded, and a summary"),
            ),
        ),
        PipelineStage(
            name="reflect",
            label="Reflect & self-repair",
            owner="aegis.agent.graph",
            summary="Judge the draft and loop back once when it does not hold up.",
            emits=(_stream("reflection", "the verdict and what it sent back"),),
        ),
        PipelineStage(
            name="generate",
            label="Generate answer",
            owner="aegis.agent.graph",
            summary=(
                "Produce the answer text. The gateway call is deliberately "
                "non-streaming; the pacing happens in the stream stage below."
            ),
        ),
        PipelineStage(
            name="guard_output",
            label="Output guardrail",
            owner="aegis.agent.rails",
            summary="Screen the answer before a single token reaches the browser.",
            emits=(_stream("guardrail", "the rail verdict and any redactions"),),
        ),
        PipelineStage(
            name="stream",
            label="Stream answer",
            owner="aegis.agent.graph",
            summary="Pace the already-produced answer out as tokens.",
            emits=(_stream("token", "one chunk of the answer"),),
        ),
        PipelineStage(
            name="persist_memory",
            label="Persist memory",
            owner="aegis.memory",
            summary=(
                "Write the turn to long-term memory. Wired plain, like recall_memory, "
                "so an inactive memory emits nothing. The tail of every completed run."
            ),
        ),
    ),
    limits=(
        "run_events holds no agent rows. The ingest stage log is the only writer of "
        "that table; a query's node timings reach the browser on the SSE stream and "
        "are discarded when it closes. Every duration above is streamed, never "
        "persisted, so there is no p95 to aggregate an hour later.",
        "The stage list is the graph's node set, not a path. A single turn runs one "
        "lane through it — the memory specialist, the qa lane or the team fan-out — "
        "and the edges that decide which are served by GET /agent/topology, from the "
        "compiled graph.",
    ),
)


INGESTION_PIPELINE = PipelineSpec(
    name="ingestion",
    title="Ingestion",
    summary=(
        "A file becomes a searchable, graph-linked corpus, in six durable stages a "
        "resume walks. Each stage commits its run_events row in the transaction that "
        "did its work; a terminal ingest_finished event closes the run out."
    ),
    entrypoint="app.ingestion.upload.upload_document",
    durable_record="run_events",
    stages=(
        PipelineStage(
            name="parse",
            label="Parse the document",
            owner="aegis.ingestion.convert",
            summary=(
                "Read the stored bytes into a structured tree, write it beside them as "
                "the parse artifact, and score the parse. A low-confidence parse is "
                "flagged and indexed, never blocked."
            ),
            emits=(
                _ingest_row(
                    "page_count, title and parse_confidence onto documents; the "
                    "confidence reasons, the heading histogram and the OCR decision as "
                    "facts — the numbers that until now only reached a log file"
                ),
            ),
        ),
        PipelineStage(
            name="chunk",
            label="Chunk the sections",
            owner="aegis.retrieval.chunker",
            summary=(
                "Pack the parsed sections into tenant-owned chunks rows, each table "
                "its own chunk. Delete-then-insert inside the caller's transaction."
            ),
            emits=(
                _ingest_row(
                    "chunk_count onto documents; chunks, tables, summarised and the "
                    "model calls the table summaries cost, as facts"
                ),
            ),
        ),
        PipelineStage(
            name="enrich",
            label="Enrich the chunk text",
            owner="app.ingestion.stages",
            summary=(
                "Fold the document / type / date / heading-path prefix into the text "
                "that is actually embedded and full-text indexed."
            ),
            emits=(_ingest_row("the enriched row count, as a fact"),),
        ),
        PipelineStage(
            name="embed",
            label="Embed the chunks",
            owner="app.ingestion.stages",
            summary=(
                "Write the embedding of record onto chunks.embedding, so a rebuilt "
                "index replays rows instead of paying the provider twice."
            ),
            emits=(_ingest_row("the embedded count and the batch count, as facts"),),
        ),
        PipelineStage(
            name="index",
            label="Publish to the index",
            owner="aegis.retrieval.vector_store",
            summary=(
                "Publish the chunks into the knowledge backend under "
                "content-addressed, tenant-prefixed ids."
            ),
            emits=(_ingest_row("the indexed count and the collection published to"),),
        ),
        PipelineStage(
            name="graph",
            label="Extract the graph",
            owner="aegis.retrieval.graph_extract",
            summary=(
                "Extract the entities and relations each chunk states onto chunks.meta "
                "— a row we own — so the graph is answerable with the graph store down."
            ),
            emits=(_ingest_row("the entity and relation totals, and the extractor name"),),
        ),
    ),
    limits=(
        "There is no retry count. The orchestrator retries an activity internally and "
        "the row carries only its latest state, so the attempts behind a succeeded "
        "stage are not in this database.",
        "A stage that has never run has no row, so the health page reads its stage "
        "list from this declaration rather than from whatever happens to be in "
        "run_events — which is exactly the drift this spec exists to stop.",
    ),
)


#: Every pipeline Aegis runs. Three.
PIPELINES: tuple[PipelineSpec, ...] = (
    RETRIEVAL_PIPELINE,
    AGENT_PIPELINE,
    INGESTION_PIPELINE,
)


def pipeline_spec(name: str) -> PipelineSpec:
    """Return one pipeline's declaration by name.

    Args:
        name: ``retrieval``, ``agent`` or ``ingestion``.

    Returns:
        The declaration.

    Raises:
        UnknownPipelineError: If nothing is declared under that name.
    """
    for spec in PIPELINES:
        if spec.name == name:
            return spec
    raise UnknownPipelineError(
        f"no pipeline {name!r}; Aegis declares {[spec.name for spec in PIPELINES]}"
    )
