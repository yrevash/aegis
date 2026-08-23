# The Aegis pipelines

**Generated from `aegis.pipelines.spec` — do not edit by hand.**
Regenerate with `python -m aegis.pipelines > docs/module/PIPELINES.md`;
`aegis/tests/pipelines/test_pipeline_spec.py` fails if this file and the
declaration disagree.

Aegis runs **three** pipelines. A module is not a pipeline: the twenty-nine-module
course in [`../teaching/`](../teaching/README.md) explains the parts, and this
document is the flows they compose into. Each stage below names the module that
owns it and what it puts on a wire or a row, and each of those claims is bound
to the code by `aegis.pipelines.bindings` — a declaration that disagrees with
the runtime raises `PipelineDriftError` rather than quietly misleading a reader.

The same declaration is served by `GET /pipelines` and read by the console's
pipeline-health page, so the screen, the API and this document cannot drift
apart.

## Where a stage's output goes

| Channel | Meaning |
|---------|---------|
| `run_event` | committed to run_events in the transaction that did the work — replayable, readable an hour later |
| `stream` | an AG-UI frame on the SSE stream — it reaches the browser and is then gone; nothing persists it |
| `result` | a field on the returned object — in-process, not persisted |

## Retrieval — `retrieval`

A question becomes an answer context: cache, hybrid wide recall, fusion, rerank, spotlighted assembly.

**Entry point:** `aegis.retrieval.pipeline.Retriever.retrieve`  
**Durable record:** none — nothing here survives the request

| # | Stage | Owns it | What it does | Emits |
|---|-------|---------|--------------|-------|
| 1 | `rewrite` *(conditional)* | `aegis.retrieval.query_rewrite` | Expand or decompose the query before recall. A layer above retrieve(), so it is absent from a bare call. | `result` `observability.rewrite` — which rewrites were tried and which one recall actually ran on |
| 2 | `cache` | `aegis.retrieval.cache` | Exact lookup, then a cosine near-match above the semantic threshold. Both tiers are partitioned by the tenant scope, not filtered after the fact. A hit returns here and every stage below is skipped. | `result` `result.cache_hit` — whether this answer came from the cache<br>`result` `result.provenance` — the cache lineage — which tier answered, and at what similarity |
| 3 | `recall` | `aegis.retrieval.pipeline` | Run the dense, graph and keyword arms against the backend, each carrying the tenant predicate into the rows it scans. | `result` `observability.arms` — one row per arm that genuinely recalled, with its candidate count<br>`result` `observability.keyword` — whether BM25 was a corpus-wide recall arm or a re-ranking pass — the distinction the arm list refuses to blur |
| 4 | `fuse` | `aegis.retrieval.fusion` | Fuse the arms' ranked lists into one pool — the honest N. | `result` `observability.fusion` — the fusion method actually applied<br>`result` `observability.fused_candidates` — the fused pool size<br>`result` `result.num_candidates` — the same N, on the result, so a caller can show N recalled -> K kept |
| 5 | `rerank` | `aegis.retrieval.local_reranker` | Grade the pool with the local ONNX cross-encoder, with the API reranker behind it on a loud failure. | `result` `observability.rerank` — whether it ran, which engine graded, how many survived, and why it degraded if it did |
| 6 | `spotlight` | `aegis.retrieval.spotlight` | Delimit retrieved text so the generator cannot mistake corpus content for instructions. | `result` `observability.spotlight_applied` — whether the answer context was spotlighted |
| 7 | `assemble` | `aegis.retrieval.pipeline` | Build the context and the citation-grade sources, and carry the graph nodes and edges the recall touched. | `result` `result.answer_context` — the context handed to the generator<br>`result` `result.sources` — the K survivors, citation-grade<br>`result` `result.graph_delta` — the graph nodes and edges recall touched<br>`result` `result.query_vec_dim` — the dimensionality of the embedding this turn computed, or null on a cache hit that computed none |
| 8 | `agentic` *(conditional)* | `aegis.retrieval.agentic` | Re-query on an insufficient context, bounded. A layer above retrieve(), so it is absent from a single-shot call. | `result` `observability.agentic` — how many iterations ran and what stopped the loop |

**What this pipeline does not record**

- No per-stage timing is recorded anywhere. Retrieval returns counts and verdicts, not durations; a stage latency would need a timer this pipeline does not have.
- Nothing here is written to run_events. Every figure above lives on the returned RetrievalResult and is gone when the caller drops it.

## Agent — `agent`

A turn becomes a streamed answer: guardrails, a supervisor route, retrieval, planning, a risk gate with a human interrupt, tools, and a generated answer.

**Entry point:** `aegis.agent.orchestrator.run_agent`  
**Durable record:** none — nothing here survives the request

| # | Stage | Owns it | What it does | Emits |
|---|-------|---------|--------------|-------|
| 1 | `guard_input` | `aegis.agent.rails` | Screen the turn before anything else runs. This is the one stage that is both the entry point and a terminal: a blocked input ends the run. | `stream` `guardrail` — the rail verdict, its layer, and any redactions |
| 2 | `route` | `aegis.agent.router` | Classify the turn to a roster specialist, or to the adaptive fan-out. | `stream` `routing` — the specialist chosen and the confidence behind it |
| 3 | `answer_memory` | `aegis.agent.graph` | The memory specialist: answer a self-referential turn from long-term memory, skipping retrieval, planning and tools entirely. | `stream` `memory` — what was recalled and used |
| 4 | `recall_memory` | `aegis.memory` | Load the subject's memory for the qa lane. Wired plain rather than through the timing wrapper, so a turn with memory inactive emits nothing at all here. | `stream` `memory` — what was recalled, when anything was |
| 5 | `plan_team` *(conditional)* | `aegis.agent.team` | Allocate the fan-out width for a turn the router sent to the team lane. | `stream` `reasoning` — the allocation, in sentences<br>`stream` `agent_status` — each allocated agent, before it starts |
| 6 | `run_team` *(conditional)* | `aegis.agent.subagent` | Run the allocated agents concurrently inside one node — an asyncio.gather, not a subgraph — each narrating its own lane. | `stream` `agent_status` — per-lane lifecycle<br>`stream` `node_started` — a lane's node, prefixed to its agent<br>`stream` `node_finished` — the same, with the lane's duration and spend<br>`stream` `reasoning` — a lane's plan<br>`stream` `tool_call` — a lane's proposed call, with its risk tier<br>`stream` `tool_result` — a lane's result |
| 7 | `synthesize` *(conditional)* | `aegis.agent.team` | Merge the lanes into one answer draft. | `stream` `synthesis` — which lanes contributed |
| 8 | `retrieve` | `aegis.retrieval.agentic` | Run the retrieval pipeline for this turn. This is the seam between the two pipelines: everything the retrieval spec declares happens inside this one node. | `stream` `retrieval` — the candidate count, scored sources and graph delta<br>`stream` `provenance` — the origins and fusion method behind the context<br>`stream` `reasoning` — what the retrieval decided, in sentences |
| 9 | `plan` | `aegis.agent.graph` | Produce the plan and the tool calls it proposes. | `stream` `reasoning` — the plan, chunked into sentences |
| 10 | `gate` | `aegis.agent.graph` | Decide on **tool risk** whether the proposed calls need a human. This is the only branch into approval, and the console's map is checked against that fact by GET /agent/topology. | nothing observable |
| 11 | `approval` *(conditional)* | `aegis.agent.approvals` | Pause the run until a human decides, enumerating every call that will execute. The node itself emits nothing: it re-executes on resume, so the orchestrator emits approval_required from the interrupt value exactly once instead. | `stream` `approval_required` — emitted by the orchestrator, not the node<br>`stream` `approval_queued` — when the run is parked to a durable inbox |
| 12 | `act` | `aegis.agent.graph` | Execute exactly the calls the gate — or the human — admitted. | `stream` `tool_call` — the call, its arguments and its risk tier<br>`stream` `tool_result` — whether it succeeded, and a summary |
| 13 | `reflect` | `aegis.agent.graph` | Judge the draft and loop back once when it does not hold up. | `stream` `reflection` — the verdict and what it sent back |
| 14 | `generate` | `aegis.agent.graph` | Produce the answer text. The gateway call is deliberately non-streaming; the pacing happens in the stream stage below. | nothing observable |
| 15 | `guard_output` | `aegis.agent.rails` | Screen the answer before a single token reaches the browser. | `stream` `guardrail` — the rail verdict and any redactions |
| 16 | `stream` | `aegis.agent.graph` | Pace the already-produced answer out as tokens. | `stream` `token` — one chunk of the answer |
| 17 | `persist_memory` | `aegis.memory` | Write the turn to long-term memory. Wired plain, like recall_memory, so an inactive memory emits nothing. The tail of every completed run. | nothing observable |

**What this pipeline does not record**

- run_events holds no agent rows. The ingest stage log is the only writer of that table; a query's node timings reach the browser on the SSE stream and are discarded when it closes. Every duration above is streamed, never persisted, so there is no p95 to aggregate an hour later.
- The stage list is the graph's node set, not a path. A single turn runs one lane through it — the memory specialist, the qa lane or the team fan-out — and the edges that decide which are served by GET /agent/topology, from the compiled graph.

## Ingestion — `ingestion`

A file becomes a searchable, graph-linked corpus, in six durable stages a resume walks. Each stage commits its run_events row in the transaction that did its work; a terminal ingest_finished event closes the run out.

**Entry point:** `app.ingestion.upload.upload_document`  
**Durable record:** `run_events`

| # | Stage | Owns it | What it does | Emits |
|---|-------|---------|--------------|-------|
| 1 | `parse` | `aegis.ingestion.convert` | Read the stored bytes into a structured tree, write it beside them as the parse artifact, and score the parse. A low-confidence parse is flagged and indexed, never blocked. | `run_event` `ingest_stage` — page_count, title and parse_confidence onto documents; the confidence reasons, the heading histogram and the OCR decision as facts — the numbers that until now only reached a log file |
| 2 | `chunk` | `aegis.retrieval.chunker` | Pack the parsed sections into tenant-owned chunks rows, each table its own chunk. Delete-then-insert inside the caller's transaction. | `run_event` `ingest_stage` — chunk_count onto documents; chunks, tables, summarised and the model calls the table summaries cost, as facts |
| 3 | `enrich` | `app.ingestion.stages` | Fold the document / type / date / heading-path prefix into the text that is actually embedded and full-text indexed. | `run_event` `ingest_stage` — the enriched row count, as a fact |
| 4 | `embed` | `app.ingestion.stages` | Write the embedding of record onto chunks.embedding, so a rebuilt index replays rows instead of paying the provider twice. | `run_event` `ingest_stage` — the embedded count and the batch count, as facts |
| 5 | `index` | `aegis.retrieval.vector_store` | Publish the chunks into the knowledge backend under content-addressed, tenant-prefixed ids. | `run_event` `ingest_stage` — the indexed count and the collection published to |
| 6 | `graph` | `aegis.retrieval.graph_extract` | Extract the entities and relations each chunk states onto chunks.meta — a row we own — so the graph is answerable with the graph store down. | `run_event` `ingest_stage` — the entity and relation totals, and the extractor name |

**What this pipeline does not record**

- There is no retry count. The orchestrator retries an activity internally and the row carries only its latest state, so the attempts behind a succeeded stage are not in this database.
- A stage that has never run has no row, so the health page reads its stage list from this declaration rather than from whatever happens to be in run_events — which is exactly the drift this spec exists to stop.
