# The Aegis modular platform

## What "modular" means here

Aegis is a domain-agnostic, agentic-AI platform that used to be one monolithic FastAPI backend
(`backend/src/app/*`). On `feat/aegis-module-contract` it is being extracted, one component at a
time, into standalone, independently-**importable** `aegis.*` packages under `aegis/src/aegis/`.
The goal (see `docs/superpowers/specs/2026-08-11-aegis-module-contract-design.md`) is stated
plainly in the design spec: Aegis must become **importable, not forkable** — a team building an
unrelated agentic system should be able to run `pip install aegis[guardrails]` (or `[ml]`,
`[retrieval]`, …) and get a SOTA-complete, production-shaped component, not a 15-line stub that
only works bolted onto Aegis's own backend.

Every module obeys one shared **Module Contract**, made of three pillars, and speaks through one
shared **streaming spine**. This document explains both, shows how the modules relate, and points
to the per-module docs (`aegis-core.md`, `aegis-data.md`, `aegis-guardrails.md`, `aegis-ml.md`,
`aegis-retrieval.md`, `aegis-gateway.md`, `aegis-memory.md`, `aegis-governance.md`,
`aegis-evals-ops.md`, `aegis-observability.md`, `aegis-agent.md`) that each teach one package in
depth. `aegis.agent` — the LangGraph plan→gate→act→reflect graph that composes all of the
others through its `AgentDeps` injection seam — is the last and final module extracted (module 8
of 8; see `.superpowers/sdd/module-agent-map.md` and `.superpowers/sdd/module-agent-report.md`).
It sits above the leaf-module boundary described below by design: gluing the other modules
together through injected callables is its entire job, and the durable app-side wiring
(`AgentDeps.default()`) still lives in `backend/src/app/agent/` as the composition root, mirroring
the `gateway.configure(...)` pattern every other module proved. See `aegis-agent.md`.

## The Module Contract — three pillars

**Pillar A — Importable & isolated.** One repo, one `aegis` package, one `pyproject.toml`, with
optional-dependency **extras** per module (`aegis[guardrails]`, `aegis[ml]`, `aegis[retrieval]`, …
— see the table below for what each module actually needs). `aegis.core` holds only Protocols,
shared Pydantic types, the registry, config, health probes, and the `require()` lazy-import helper
— it has **zero heavy dependencies**. The boundary rule that makes this durable: `aegis.core`
imports nothing internal; a leaf module imports only `aegis.core` (and, for the data-backed
modules, `aegis.data`) plus its own third-party libs; there is **no leaf-to-leaf import** — shared
logic always goes into `aegis.core` instead. A missing optional dependency always fails loud via
`aegis.core.lazy.require(extra, module)`, which raises an `ImportError` naming the exact
`pip install` fix — never a silent `except ImportError: pass`.

**Pillar B — Shows its work.** Every module that has runtime work to narrate emits it as a live,
typed event stream instead of returning only a final answer. This is the "show-your-work" contract
described further below (the AG-UI streaming spine) — the same events that render live in a UI
also carry an OpenInference span-kind, so they double as an OTel/OpenInference trace export. One
event contract, two consumers: a live console and an observability backend.

**Pillar C — Honest infra.** Config is typed and fail-fast (`aegis.core.config.AegisMode`):
`full` (default) probes real Redis/Postgres/pgvector at boot and refuses to start if a required
backend is unreachable; `lite` deliberately boots on in-memory stores, **loudly** announced in
logs and (in a consuming service) a persistent UI banner; `auto` probes and may drop to `lite` but
never silently. Backend selection always goes through an explicit factory that logs its choice —
there is no `except → in-memory` path anywhere in the platform. This matters because the old
monolith's worst failure mode was exactly that: code configured for Redis + pgvector silently
fell back to RAM and *called it* durable storage.

### Known deviations from the boundary invariant

Two real leaf-to-leaf imports exist in the codebase today, found while writing these docs
(verified by reading the code, not asserted from the spec):

- `aegis.memory.recall`/`vector_ops`/`working` import `aegis.retrieval.fusion`, `.vectors`,
  `.spotlight`, `.types` (for RRF fusion, cosine similarity, and spotlighting) — a deliberate
  repoint documented in the memory extraction map/report, not an accident. Because Python runs a
  package's `__init__.py` on any submodule import, `import aegis.memory` transitively imports all
  of `aegis.retrieval`; the heavy backends inside `aegis.retrieval` (`lightrag`, `neo4j`, `redis`)
  stay lazy-imported, so this costs nothing extra beyond `aegis[data]` — but it is still a
  leaf-to-leaf import the Pillar-A boundary rule says shouldn't exist. See `aegis-memory.md`.
- `aegis.governance.enforcement` imports `BudgetExceededError` from `aegis.gateway.types` — another
  real leaf-to-leaf import, confirmed in the code. See `aegis-governance.md`.

Both are noted here rather than smoothed over, because the whole point of "honest infra" is not
claiming an invariant holds when the code says otherwise.

## The à la carte streaming spine

Every module that narrates its work emits through **one** shared primitive,
`aegis.core.stream.AegisEmitter`, which wraps the official `ag-ui-protocol` SDK
(`ag_ui.core` + `ag_ui.encoder.EventEncoder`) rather than hand-rolling a wire format. AG-UI is a
recognized, open agent-UI standard, so Aegis's stream is consumable by ecosystem tooling
(CopilotKit, assistant-ui-style renderers), not just its own frontend.

The key design principle is **à la carte**: there is no base class a module must fully implement
and no mandatory event set. A module calls only the `AegisEmitter` helpers relevant to what it
does; the rest simply never fire:

- `run_started()` / `run_finished()` / `run_error()` — the lifecycle envelope, once per run.
- `step(name, span_kind)` — an async context manager bracketing `STEP_STARTED`/`STEP_FINISHED`
  (every OpenInference `SpanKind`: `LLM`, `EMBEDDING`, `RETRIEVER`, `RERANKER`, `TOOL`,
  `GUARDRAIL`, `AGENT`, `CHAIN`, `EVALUATOR`).
- `reasoning(delta)` — live agent-thinking, as a `CustomEvent(name="reasoning")` (native AG-UI
  `REASONING_*` events are still draft-spec, so this is the interim, isolated-behind-the-emitter
  channel).
- `text_start/text_delta/text_end` and `tool_start/tool_args/tool_end/tool_result` — bracketed
  assistant text and tool-call streaming.
- `custom(name, value)` — domain payloads (guardrail verdicts, SHAP explanations, citations, …) as
  a `CustomEvent`, where `name` **must** be one of the canonical names in
  `aegis.core.stream_names.ALL` — calling `custom()` with an unregistered name raises `ValueError`
  at the call site, so a typo can never reach the frontend as a silently-unrecognized event.

Every module calls a different subset: guardrails uses `step()` + `custom("guardrail_verdict")`;
retrieval uses `step()` + `custom("retrieval_citations")`; ml uses
`custom("shap_explanation")` + `custom("conformal_interval")`; gateway uses `step()` +
`custom("model_call")`; memory uses `custom("memory_recall")`; evals uses
`custom("eval_result")`. Same emitter, same wire contract, different vocabulary per module — see
the table below for exactly which events each module emits today.

On the frontend, `frontend/src/agui/streamNames.ts` mirrors `aegis.core.stream_names` value-for-
value and `frontend/src/agui/decode.ts` provides a minimal SSE-frame decoder
(`decodeAguiStream`). As of this writing that is the current state of the frontend AG-UI surface —
the full process-rail timeline + per-event-type dispatcher described in the Module Contract spec
(§4: a vertical step timeline, each row expanding to a specialized renderer — guardrail verdict
card, citation cards, SHAP waterfall, conformal band, approval card — with an unknown-type JSON
fallback) is designed but not yet built. The existing frontend console instead renders the older,
bespoke 18+-member `StreamEvent` union (`frontend/src/types/stream.ts`), which predates the AG-UI
migration and is still the "locked" contract several backend paths (including today's agent graph)
emit through.

## Whole-platform architecture

```mermaid
graph TD
    subgraph foundations["Foundations — zero/near-zero deps"]
        core["aegis.core<br/>Protocols, types, events,<br/>registry, config, health,<br/>AegisEmitter + stream_names"]
        data["aegis.data<br/>AegisBase, VectorType,<br/>JsonB, EMBED_DIM"]
    end

    subgraph leaves["Leaf modules — depend on core (+ data where durable)"]
        guardrails["aegis.guardrails<br/>input/output rails"]
        ml["aegis.ml<br/>predict + SHAP + conformal"]
        retrieval["aegis.retrieval<br/>hybrid RAG"]
        gateway["aegis.gateway<br/>LLM chokepoint"]
        memory["aegis.memory<br/>working + episodic memory"]
        governance["aegis.governance<br/>tenants, RBAC, RLS, budgets"]
        evals["aegis.evals<br/>RAGAS-style + LLM-judge"]
        ops["aegis.ops<br/>eval-gated release"]
        observability["aegis.observability<br/>OTel/OpenInference export"]
    end

    subgraph orchestration["Orchestration — composes every module above"]
        agent["aegis.agent<br/>LangGraph plan→gate→act→reflect graph —<br/>AgentDeps injection seam"]
    end

    core --> guardrails
    core --> ml
    core --> retrieval
    core --> gateway
    core --> memory
    core --> governance
    core --> evals
    core --> ops
    core --> observability
    data --> memory
    data --> governance

    evals -.->|gates| ops

    agent -->|deps.check_input/check_output| guardrails
    agent -->|deps.predict_explain| ml
    agent -->|deps.retrieve, agentic_retrieve| retrieval
    agent -->|deps.complete| gateway
    agent -->|deps.memory: MemoryDeps Protocol| memory
    agent -->|deps.record_audit; BudgetExceededError| governance
    agent -->|span/SpanKind/semconv (real import)| observability

    guardrails -->|AegisEmitter.custom<br/>guardrail_verdict| stream["AG-UI event stream<br/>(SSE, ag-ui-protocol)"]
    ml -->|shap_explanation<br/>conformal_interval| stream
    retrieval -->|retrieval_citations| stream
    gateway -->|model_call| stream
    memory -->|memory_recall| stream
    evals -->|eval_result| stream
    agent -->|reasoning, routing, tool_call,<br/>approval_required, reflection, ... via<br/>the legacy StreamEvent seam (stamp=)| legacyStream["Legacy StreamEvent union<br/>(app.api.schemas, locked SSE contract)"]

    legacyStream -.->|AG-UI migration<br/>deferred follow-on| stream
    stream --> frontendDecode["frontend/src/agui<br/>streamNames.ts + decode.ts"]
    stream --> otel["Same stream, tagged with<br/>OpenInference SpanKind →<br/>OTel export (aegis.observability)"]

    style foundations fill:#eef,stroke:#448
    style orchestration fill:#efe,stroke:#484
```

## Modules at a glance

| Module | What it does | `aegis[extra]` | AG-UI `CustomEvent` name(s) |
|---|---|---|---|
| `aegis.core` | Contract: Protocols, shared types, registry, config, health, `AegisEmitter` | none (bare `aegis`) | defines the vocabulary; emits none itself |
| `aegis.data` | Portable SQLAlchemy base + pgvector/JSON column types | `data` | none |
| `aegis.guardrails` | Input/output rails: schema, PII, injection | none for the base pipeline; `nemo` (Colang engine), `redis` (durable cache) are optional | `guardrail_verdict` |
| `aegis.ml` | Prediction + SHAP explanation + conformal intervals | `ml` | `shap_explanation`, `conformal_interval` |
| `aegis.retrieval` | Hybrid (vector + graph) RAG with fusion, rerank, citations | `retrieval` | `retrieval_citations` |
| `aegis.gateway` | The LiteLLM chokepoint: routing, cost, budget, fallback | `gateway` | `model_call` |
| `aegis.memory` | Working + episodic memory, recall, consolidation | `data` (no dedicated `memory` extra exists) | `memory_recall` |
| `aegis.governance` | Tenants, RBAC, RLS, budgets, audit | `governance` | none (no `stream.py` — a policy/data layer, not a narrator) |
| `aegis.evals` | RAGAS-style metrics + LLM-judge harness | none — installs with bare `pip install aegis` | `eval_result` |
| `aegis.ops` | Diagnose, eval-gated release/promotion | `data` (no dedicated `evals`/`ops` extra exists; needs SQLAlchemy) | none (no `stream.py`) |
| `aegis.observability` | OTel/OpenInference span export (consumes the same event contract) | `observability` (+ optional `phoenix` for Arize Phoenix export) | none — it is the trace *exporter*, not an emitter |
| `aegis.agent` | Plan→gate→act→reflect orchestration graph — composes every module above through `AgentDeps` | `agent` (langgraph + langchain-core + otel) | none via `AegisEmitter` — emits through the legacy `StreamEvent` union instead (see below) |

`reasoning` and `routing` are reserved in `aegis.core.stream_names` specifically for
`aegis.agent`'s live-thinking and router-decision output, and both now fire — but through
`aegis.agent`'s own dict-builder + injected-`stamp` seam (`aegis.agent.events`, validated against
the legacy `StreamEvent` union), not through `AegisEmitter`. Migrating the agent graph onto the
AG-UI `CustomEvent` contract every other module now uses is explicit deferred follow-on work (see
`aegis-agent.md`), so it does not yet appear in the `AegisEmitter`/`stream_names` column above.

## Reading order

Start with `aegis-core.md` and `aegis-data.md` — every other doc assumes you know
`AegisEmitter`/`stream_names`, the core types (`GuardResult`, `SpanKind`, …), and `AegisBase`.
Then `aegis-guardrails.md` is the best worked example of the full contract end-to-end (it was the
pilot module). After that the remaining docs (`aegis-ml.md`, `aegis-retrieval.md`,
`aegis-gateway.md`, `aegis-memory.md`, `aegis-governance.md`, `aegis-evals-ops.md`,
`aegis-observability.md`) can be read in any order — see `docs/module/README.md` for a one-line
hook on each. Read `aegis-agent.md` last: it assumes you already know the `AgentDeps` seam it
injects (the gateway's `complete`, retrieval's `retrieve`/`agentic_retrieve`, guardrails'
`check_input`/`check_output`, ML's `predict_explain`, memory's `MemoryDeps` Protocol) is exactly
what those earlier docs already taught.
