# backend.md — Backend Context

> The backend is the brain of **Aegis**: agent orchestration, retrieval, the ML spine, guardrails, and observability. It must be clean, typed, modular, and fully local-or-API (no Docker, no GPU, 16 GB laptop). Read `hackathon.md` and `security.md` alongside this.

---

## 0. Aegis modules (the product identity)

Every backend capability is a first-class **Aegis module** — a branded name presented
**with its honest underlying tech** (branding, never hiding). The list below mirrors the
live, typed manifest in `backend/src/app/capabilities.py`, served at
`GET /platform/capabilities` (and the identity card `GET /about`). This is the single
source of truth also used by the README and the frontend Platform view.

| Aegis module | Tech underneath | Real code path | Status |
|---|---|---|---|
| **Aegis Gateway** | LiteLLM | `app.core.llm` | live |
| **Aegis Router** | LangGraph | `app.agent.router` | live |
| **Aegis Memory** | Postgres + embedded Chroma | `app.memory` | live |
| **Aegis Cache** | Redis | `app.retrieval.cache` | live |
| **Aegis Retrieval** | Neo4j/LightRAG + embedded NanoVectorDB | `app.retrieval.pipeline` | live |
| **Aegis Signal** | XGBoost + MAPIE + SHAP | `app.ml.model` | live |
| **Aegis Guardrails** | programmatic + NeMo Colang | `app.guardrails.rails` | live |
| **Aegis Evals** | RAGAS-style proxies + LLM judge | `app.eval.harness` | live |
| **Aegis Loop** | native | `app.ops.release` | live |
| **Aegis Governance** | Postgres RLS + JWT | `app.core.governance` | live |
| **Aegis Trace** | OpenTelemetry → Phoenix | `app.observability.otel` | live |
| **Aegis Tools / MCP** | native + MCP SDK | `app.mcp.server` | optional |

The branded names label the modules; the tech column keeps every claim honest. The
underlying code, packages and behaviour are unchanged — this is presentation, backed by
a real manifest.

---

## 1. Stack (finalized)

- **FastAPI** (async) — API layer; auto-generates OpenAPI docs (free documentation points). SSE streaming endpoints.
- **LiteLLM** — single model gateway over the entire Azure fleet; routing + fallback + cost tracking.
- **LangGraph** — stateful agent orchestration (directed graph with conditional edges, human-in-the-loop, durable state).
- **LightRAG** (`lightrag-hku`) — graph+vector RAG pipeline (builds and retrieves; see §4).
- **ML spine:** XGBoost (model) + MAPIE (conformal prediction) + SHAP (explanation). CPU-only, light.
- **Guardrails:** Guardrails AI **or** NeMo Guardrails + self-built checks + Garak (red-team). Injection detection via **API** classifier, no local guard model. See `security.md`.
- **Observability:** OpenTelemetry SDK emitting `gen_ai.*` spans → **Arize Phoenix** (local, in-process, no Docker).
- **Reranker:** a small cross-encoder via `sentence-transformers`, OR an API-based rerank call to stay zero-footprint. **AGENT: pick based on RAM headroom at build time; ask if unsure.**
- **Validation/quality:** Pydantic models everywhere; Ruff (lint/format); pytest on the critical path.

**Dropped on purpose:** Rust (bottleneck is API latency, not local compute), local guardrail models (RAM), Docker-dependent infra, Supabase.

---

## 2. The Azure model fleet (API-only, via LiteLLM)

```
azure/genailab-maas-gpt-35-turbo
azure/genailab-maas-gpt-4o
azure/genailab-maas-gpt-4o-mini
azure/genailab-maas-text-embedding-3-large
azure/genailab-maas-whisper
azure_ai/genailab-maas-DeepSeek-R1
azure_ai/genailab-maas-DeepSeek-V3-0324
azure_ai/genailab-maas-Llama-3.2-90B-Vision-Instruct
azure_ai/genailab-maas-Llama-3.3-70B-Instruct
azure_ai/genailab-maas-Llama-4-Maverick-17B-128E-Instruct-FP8
azure_ai/genailab-maas-Phi-3.5-vision-instruct
azure_ai/genailab-maas-Phi-4-reasoning
```

The `azure/` and `azure_ai/` prefixes are LiteLLM provider strings — confirming LiteLLM is the correct gateway. **Only these models may be used.**

### Heterogeneous model routing (a stated architectural decision — cost story)

- **Entity extraction / routing / classification / cheap steps →** `gpt-4o-mini`, `Llama-3.2`.
- **Hard reasoning steps →** `Phi-4-reasoning`, `DeepSeek-R1`.
- **Main generation →** `gpt-4o`, `DeepSeek-V3`, `Llama-4-Maverick`.
- **Embeddings →** `text-embedding-3-large`.
- **Voice (if used) →** `whisper`. **Vision (if used) →** `Llama-3.2-90B-Vision`, `Phi-3.5-vision`.

Route by job, not by habit. Surface the routing breakdown on the dashboard (small-model share). This cuts cost and reads as sophistication.

---

## 3. Agent core (LangGraph)

- **Loop:** perceive → reason → act → observe, repeat until done or escalate.
- **Shape:** prefer **plan-and-execute** for demo clarity (visible, auditable plan), with a ReAct-style tool loop where open-endedness is needed.
- **Tool registry:** tools are typed functions that perform real actions (create record, call API, run workflow). MCP-shaped. **Tool definitions are part of the domain adapter** — keep them in a clearly-named, swappable module.
- **Bounded autonomy (critical):** high-risk actions OR high-uncertainty predictions route to a **human-in-the-loop gate** (a LangGraph conditional edge → approval node) instead of executing. Actions must be **idempotent**, **logged to the audit table**, and reversible where possible.
- **Guardrail hooks** wrap input and output of every model interaction (see `security.md`).

**AGENT: before building this loop, run the tool-calling spike (§9). The loop's design depends on it.**

---

## 4. Retrieval (LightRAG + hybrid stores)

- **LightRAG is the pipeline; Neo4j and the embedded vector store are the stores.** LightRAG ingests documents (`insert()`), calls an LLM to extract entities+relationships, builds the graph + embeddings, and retrieves over both at query time. Extraction + embeddings run **via API** (`gpt-4o-mini` + `text-embedding-3-large`), so nothing heavy runs locally.
- **Why LightRAG (not Microsoft GraphRAG):** it skips the expensive community-summarization step, so indexing is fast and cheap — right for indexing synthetic data on the day. (This is an ADR-worthy decision.)
- **Stores:** Neo4j (graph, local) + an **embedded vector store** (Chroma for retrieval and memory recall, NanoVectorDB for LightRAG's own internal vectors) + local Postgres (relational, KV, doc-status). No vector server and no `pgvector` extension — see ADR 0009. Graph traversal answers relationship questions; vector search answers similarity questions; LightRAG uses both.
- **Two-stage retrieval:** retrieve a wide candidate set → **rerank** → pass top context to generation.
- **Semantic cache in front:** embed query → nearest-neighbour lookup in **Redis (local)** → hit returns instantly; miss runs retrieval then writes back. Exact-match tier first, semantic tier on top.
- **Agentic RAG:** the agent decides *what* to retrieve dynamically — this is the differentiator, not the components.
- **Security:** apply Azure **Spotlighting** to mark retrieved content as data, not instructions (indirect-injection defense). Validate before writing to the graph (poisoning defense).

---

## 5. ML spine (trustworthy ML)

- **Model:** XGBoost (or LightGBM) — a real supervised model (e.g., a classifier/predictor). Features + target are part of the **domain adapter**.
- **Conformal prediction (MAPIE):** wrap the model to produce calibrated prediction sets/intervals with a *guaranteed coverage rate*. Requires a calibration split. This drives the human-gate threshold with a statistical guarantee — not a hand-picked number.
- **SHAP:** explain predictions (global + local). Prefer SHAP over LIME (game-theoretic consistency vs LIME's instability).
- **Exposed as a tool:** the agent calls the ML spine; it returns `{prediction, conformal_interval, shap_attribution}`. The frontend renders the explanation panel from this.

---

## 6. Data model & stores (fully local)

| Store | Tech (local) | Holds |
|---|---|---|
| Knowledge graph | Neo4j (Desktop/Community) | entities + relationships from LightRAG |
| Vector index | **Embedded Chroma / NanoVectorDB** (on-disk, no server) | chunk + memory embeddings, ANN search |
| Relational | **PostgreSQL** (no extension needed) | embeddings of record (JSON); users+roles (RBAC); domain records; **audit log**; eval results |
| Semantic cache | Redis (WSL2 or Memurai) | query-embedding → answer (TTL) |
| Traces | Arize Phoenix (in-process) | `gen_ai.*` spans |

- **No Supabase, and no `pgvector`.** Install PostgreSQL locally; no server-side extension is required. ANN search lives in the embedded vector store, which is a directory on disk, not a service (ADR 0009).
- **Audit log is a first-class table:** every autonomous action, the approving human (if any), the model used, and the trace id. This is what makes the system defensible (security + maintainability).

---

## 7. Streaming

- FastAPI SSE endpoints stream **structured agent-step events** (node started, tool called, retrieval done, awaiting approval, answer chunks) — not just final tokens.
- Define the event schema explicitly and share it with the frontend (see `frontend.md` §6). **Do not let the frontend guess the shape.**

---

## 8. Observability & eval

- **OpenTelemetry spans across the full taxonomy**, each tagged with `openinference.span.kind` so **Phoenix** (local) renders the whole agent run as one nested tree — not just the model calls. What actually emits today:
  - **AGENT** — the root `agent.run` span (`agent/orchestrator.py`), parent of everything below; still the `trace_id` that links the trace to the audit log.
  - **CHAIN** — one span per graph node (the `_timed` wrapper in `agent/graph.py` opens a span around the same node body that emits the `node_started`/`node_finished` stream events), for `ml_predict`, `plan`, `gate`, `act`, `reflect`, `generate`, `stream`.
  - **RETRIEVER** — the `retrieve` node span, carrying the query and the honest recall funnel (N candidates → K results).
  - **RERANKER** — the LLM rerank stage inside `retrieval/pipeline.py`.
  - **GUARDRAIL** — the `guard_input` / `guard_output` node spans, carrying the rail stage, verdict, and layer.
  - **TOOL** — one span per tool execution in the `act` node (tool name, risk, ok).
  - **LLM / EMBEDDING** — the existing `gen_ai.*` chat/embedding spans (`observability/genai.py`), now also tagged with their OpenInference kind.

  The span helper (`observability/spans.py`) degrades to a **no-op** when no tracer/Phoenix is configured (offline "lite" mode and tests), so instrumentation never crashes or requires the network. Being OTel-native = portable, no lock-in (an ADR-worthy point).
- **Token/cost tracking** from the spans → the live dashboard (cache-hit rate, small-model share, cost per 1000 queries).
- **Offline evals:** the quality gate (`app.eval`) computes **RAGAS-style deterministic proxies** — lexical/overlap proxies inspired by RAGAS metric ideas, **not** the `ragas` library (which is not a dependency: it needs a network/LLM and this gate runs fully offline). Three proxies are computed and asserted against thresholds:
  - **context-precision proxy @k** — fraction of the top-k retrieved sources whose document is a gold document (proxy for RAGAS *context precision*);
  - **context-recall proxy** — fraction of the case's gold documents that appear anywhere in the retrieved sources (proxy for RAGAS *context recall*);
  - **groundedness/faithfulness proxy** — fraction of the case's expected claim keywords present, by normalized substring match, in the assembled retrieval context (proxy for RAGAS *faithfulness*).

  RAGAS *answer relevancy* is **not** computed deterministically. The model-graded signal (genuine groundedness **and** relevance) comes from the **optional LLM-as-judge** (`DeepSeek-R1`/`Phi-4-reasoning` via the gateway): `evaluate()` calls it when a chat-completion `complete` callable is injected and surfaces its verdict on the report; with no `complete` the judge is skipped so the default gate stays offline (a real gateway run is opted into via `TAIF_EVAL_LLM_JUDGE`). The suite is wired as a **quality gate** that fails the build when the proxy metrics regress below their floors.

---

## 9. THE CRITICAL SPIKE (do this before anything else)

**Validate tool/function-calling through the Azure gateway.** Fire one LiteLLM call to `gpt-4o` with a `tools` parameter and confirm a `tool_calls` block returns.
- **If yes →** build the LangGraph agent normally.
- **If no →** pivot to a ReAct / structured-output (JSON) fallback where the model returns an action as parsed text, and the backend executes it.

Do not build the agent loop against the happy path until this is confirmed. Report the result.

---

## 10. API surface (design; auto-documented via OpenAPI)

- `POST /auth/login` → role + token.
- `POST /query` → SSE stream of step events + answer.
- `GET /graph` → nodes/edges for the viz (updates during retrieval if feasible).
- `POST /ml/explain` → `{prediction, conformal_interval, shap_attribution}`.
- `GET /metrics` → live token/cost/cache/eval numbers.
- `POST /approval` → approve/reject a paused action.
- All inputs/outputs are Pydantic models. All actions hit the audit log.

---

## 11. Quality bar

- Pydantic types everywhere; Ruff enforced; pytest on the agent/retrieval/ML critical path (not 100% coverage).
- **Modular boundaries:** `api/`, `agent/`, `retrieval/`, `ml/`, `guardrails/`, `data/`, `observability/`, plus an isolated `adapter/` for the five domain-specific pieces. No god-files. No domain logic in the core.
- README with first-screen architecture; ADRs for LightRAG-vs-GraphRAG, conformal prediction, multi-model routing, OTel-native observability.

---

## 12. Agent directives (behavior for this codebase)

- **Research current library APIs before implementing** (LangGraph, LightRAG, MAPIE, LiteLLM, Guardrails, OTel/Phoenix all move fast). Verify, don't rely on memory.
- **Ask, don't guess**, on: the tool-calling spike result, the domain schema/tools/personas, the SSE event schema, and any tradeoff with real rework cost.
- **De-risk order:** LiteLLM connection → tool-calling spike → embeddings + retrieval → Neo4j + LightRAG → LangGraph agent → ML spine → guardrails/observability throughout.
- **Do not over-engineer.** The stack is set; do not add tools/complexity not in these files without asking.
