# aegis.agent — Orchestration Graph (extraction) Design Spec

- **Date:** 2026-08-12 · **Branch:** `feat/aegis-module-contract` · Module 8 of 8 (FINALE)
- **Map:** `.superpowers/sdd/module-agent-map.md` · The hardest module — but the DI seam is ~80% done.

## 1. Goal

Extract the LangGraph plan→gate→act→reflect orchestration into a standalone importable **`aegis.agent`** — pure
graph-over-injected-deps (the `AgentDeps` seam), depending only on `langgraph` + injected callables + injected
tracing + an injected event-validator. The domain/infra wiring (`AgentDeps.default()`) and the durable
orchestrator/routes stay in `app` (the composition root). This mirrors the `gateway.configure(...)` pattern
every other module proved.

## 2. Split (core in aegis, wiring in app)

**MOVE to `aegis.agent`** (pure orchestration): `state.py` (AgentState TypedDict — zero couplings), `router.py`
(RouterDecision + route_query + fallback roster), `approvals.py` (ApprovalRegistry/ParkedRunRegistry — pure
asyncio), `deps.py` CONTRACT (AgentConfig, AgentDeps, MemoryDeps dataclasses + type aliases — WITHOUT the
`_default_*` app bindings), `graph.py` `build_agent(deps, *, checkpointer, tracer, emit)` + all node bodies,
`events.py` BUILDERS (plain dict factories), and the pure parts of `orchestrator.py` `run_agent` (the stream
loop + gate rendezvous), taking injected durable-store callables.

**KEEP in `app`** (composition root): `AgentDeps.default()` + every `_default_*` (wire `app.adapter` +
`app.data` + `app.config`), `_build_postgres_checkpointer` (needs app.config + langgraph-checkpoint-postgres),
the durable orchestrator glue (`_enqueue_gate`, resolve/finalize, trace-eval firing, checkpointer construction),
and `routes.py`.

## 3. The five injection seams (sever these)

1. **Observability** → import `span`, `set_span_attribute(s)`, `get_tracer`, `current_trace_id`, `semconv`,
   `SpanKind` from **`aegis.observability`** + `aegis.core.events` (now extracted). Degrade to no-op when no
   tracer (already the behavior on a non-recording span). No injection needed beyond importing the real package.
2. **StreamEvent union / event-validator** → KEEP the legacy union in `app.api.schemas` (locked SSE contract;
   defer AG-UI). Move `events.py` dict-builders into `aegis.agent`; **inject a `stamp`/validate callable**
   (`emit: Callable[[dict], dict]` or a validator) so `aegis.agent` never imports `app.api.schemas`. Move the
   enums it needs (`GuardStage`, `RunStatus`, `ApprovalDecision`) into `aegis.core.types` (guardrails/governance
   already moved GuardVerdict/RiskLevel/Role there), and `app.api.schemas` re-exports them (identity).
3. **Checkpointer** → break the `app.data.session` ↔ `graph._build_postgres_checkpointer` circular dep: inject
   the checkpointer (`build_agent(deps, *, checkpointer=InMemorySaver())` / a factory), move
   `_build_postgres_checkpointer` to `app`. Default `InMemorySaver`.
4. **Audit + approvals durable calls** → lift onto the seam: add injected callables `record_audit`,
   `enqueue_approval`, `resolve_approval`, `finalize_resumed` (AgentDeps fields or an `ApprovalStore`/`AuditSink`
   protocol passed to `run_agent`/`decide_approval`). Each already wrapped in `_safe_*` → null injection
   degrades cleanly (matches offline/lite). Add `audit` to the seam (currently hidden in `_default_run_tool`).
5. **Domain adapter + config** → `app.adapter` stays the injected surface (via `AgentDeps.default()` in app);
   `app.config` stays app. `aegis.agent` never imports either.

## 4. Extra

`agent = ["langgraph>=0.2", "langchain-core>=0.3"]` (+ `all`); the Postgres checkpointer
(`langgraph-checkpoint-postgres`) stays an app/backend concern. `aegis.core` stays minimal (langgraph banned
from core — agent is a leaf). `import aegis.agent` keeps litellm/DB out (langgraph is its own dep).

## 5. Strangler shim

`backend/src/app/agent/{graph,deps,orchestrator,router,approvals,events,state}.py` → shims re-exporting
`aegis.agent`, with `app`-side `AgentDeps.default()` + `_default_*` + `_build_postgres_checkpointer` + the
durable orchestrator glue binding the injected seams (event-validator = the app.api.schemas `StreamEvent`
TypeAdapter stamp; checkpointer = `get_agent_checkpointer()`; audit/approvals = `app.data.*`). `routes.py`
`POST /query` → `run_agent` unchanged. `app.api.schemas` re-exports moved enums. Backend green minus the 2 env
failures; the full agent vertical slice (13 tests via `build_fake_deps`) passes through the shim.

## 6. Testing & proof

Port `backend/tests/agent/` (13 files, already inject fakes for every callable via `build_fake_deps`) → adapt
to build `aegis.agent`'s `AgentDeps` directly with the injected seams. Add an import-isolation guard
(`import aegis.agent` pulls no litellm/fastapi/DB drivers — langgraph is fine). Backend parity: the whole slice
(orchestrator, self-repair loop, ml-signal, durable approvals, memory wiring, budget-exceeded, trace-eval,
span-tree, telemetry) passes through the shim; full backend suite green minus the 2 env failures.

## 7. Definition of done (and honest scope)

`aegis.agent` importable + `aegis[agent]`-installable — a pure graph-over-injected-deps that runs the full
plan→gate→act→reflect flow when given an `AgentDeps` + checkpointer + tracer + event-validator; the backend
delegates via shims with `AgentDeps.default()` as the composition root; backend green (minus 2 env failures)
with the full agent test slice passing. **Honest scope:** if the checkpointer/event-validator injection proves
fiddly, landing DONE_WITH_CONCERNS with the pure graph core extracted (and the most durable orchestrator pieces
left app-side behind the seam) is acceptable — the marquee win is the graph running on injected deps.
