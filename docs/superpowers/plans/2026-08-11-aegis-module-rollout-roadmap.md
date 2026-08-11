# Aegis Module Rollout Roadmap (breadth-first, then SOTA depth)

**Mandate:** turn every remaining Aegis platform capability into an importable `aegis.<module>`
that (a) is standalone-installable with honest optional-dependency extras, (b) emits its work
through the shared **AG-UI streaming spine** (`aegis.core.stream.AegisEmitter`, à la carte), and
(c) is honest about infra. One module at a time, each via the full spec → plan → subagent build →
review → verify loop, to **SOTA maturity**. After all modules: a whole-platform verification agent,
then `docs/module/` learning docs (architecture diagram + flowchart per module).

**Foundations already done:** `aegis.core` (Module Contract), `aegis.guardrails` (pilot),
`aegis.core.stream` (AG-UI spine) + guardrails retrofit + backend demo + frontend decoder.

## Ordering rationale

Least-coupled / highest-visible-value first, so each module can be extracted LLM-agnostically
(inject a completer/embedder rather than depending on the gateway), and the marquee agent module
comes after the pieces it composes.

| # | Module | What it is | AG-UI it emits | Key deps (extras) | Notes |
|---|--------|-----------|----------------|-------------------|-------|
| 1 | **`aegis.ml`** | XGBoost+HGB ensemble · MAPIE conformal · SHAP | `shap_explanation`, `conformal_interval` + STEP(ml_predict) | xgboost, scikit-learn, mapie, shap, pandas, numpy | No LLM. Marquee visuals. Self-contained. |
| 2 | **`aegis.retrieval`** | hybrid vector+graph+BM25 → RRF → LLM rerank | `retrieval_citations` (candidates→reranked, id/score/origin) + STEP(retrieve) | pgvector, neo4j, redis, an injected embedder + reranker (ChatCompleter-style) | LLM-agnostic via injected embedder/reranker. Honest infra (pgvector/neo4j or explicit lite). |
| 3 | **`aegis.gateway`** | LiteLLM chokepoint: role routing, budgets, cost ledger | `custom(model_call)` (tokens/cost/cache) + STEP(llm) | litellm | Foundational; provides the ChatCompleter the others inject. |
| 4 | **`aegis.memory`** | episodic/semantic/procedural LTM, bitemporal | `memory_recall` + STEP(recall/persist) | pgvector | LLM-agnostic. |
| 5 | **`aegis.agent`** | LangGraph plan→gate→act→reflect + **live reasoning** | `reasoning` (live thinking) · `tool_*` · `routing` + STEPs | langgraph | **Marquee.** Composes ml/retrieval/guardrails/gateway/memory. Streams the agent's thinking. |
| 6 | **`aegis.governance`** | multi-tenant RBAC, budgets, RLS, audit | STEP + audit customs | (postgres) | Mostly infra; honest-infra focus. |
| 7 | **`aegis.evals`** | RAGAS-style proxies + LLM-judge quality gate | `custom(eval_result)` + EVALUATOR spans | (llm-judge via injected completer) | |
| 8 | **`aegis.observability`** | OTel/OpenInference export of the AG-UI stream → Phoenix | (bridges the stream to spans) | opentelemetry, arize-phoenix | Turns the same stream into traces. |

## Per-module process (repeat for each)

1. **Spec** (`docs/superpowers/specs/…-<module>-design.md`) — the module's interface (Protocols),
   what it streams (which AG-UI events / custom names — add new names to `aegis.core.stream_names`
   + the frontend mirror), its optional-dep extras, honest-infra story, and the strangler shim plan.
2. **Plan** (`docs/superpowers/plans/…-<module>.md`) — bite-sized TDD tasks, spike any new
   fast-moving dep first.
3. **Build** — subagent-driven loop (fresh implementer per task, review each, fix loop, final review).
4. **Verify** — module suite green + backend still green through the shim + a live end-to-end where
   it emits AG-UI the frontend decodes.
5. **Ledger** every step; defer minors to a followups doc.

## Invariants (every module)

- `aegis.core` stays heavy-dep-free (guard test). A module's heavy deps live under ITS extra.
- No `app.*` imports inside `aegis/`; leaves import only `aegis.core`.
- LLM-agnostic where possible (inject completer/embedder), so a module is useful standalone.
- Honest infra: real backend or explicit `AEGIS_MODE=lite`, never a silent fallback.
- Strangler migration: the legacy `backend/app` delegates to the new module via a shim; suite stays green.
- À la carte streaming: use only the emitter helpers the module needs.

## Finish line (after all 8)

1. **Whole-platform verification agent** — runs every module suite + backend + frontend, a live
   end-to-end of the full agent stream, and asserts the invariants across all modules.
2. **`docs/module/<module>.md` learning docs** — one per module: what it is + how it works
   (learning-oriented), an **architecture diagram** and a **flowchart** (mermaid), the AG-UI events it
   emits, its public API, its extras, and how to use it standalone. Plus `docs/module/README.md`
   indexing them + a whole-platform architecture diagram.

## Progress

- [x] Foundations: `aegis.core`, `aegis.guardrails`, AG-UI streaming spine.
- [ ] 1. `aegis.ml`  · [ ] 2. `aegis.retrieval` · [ ] 3. `aegis.gateway` · [ ] 4. `aegis.memory`
  · [ ] 5. `aegis.agent` · [ ] 6. `aegis.governance` · [ ] 7. `aegis.evals` · [ ] 8. `aegis.observability`
- [ ] Whole-platform verification agent · [ ] `docs/module/` learning docs
