# Phase 3 — Next.js + Tailwind frontend rebuild (demo-safe)

> Decision: FULL rewrite to **Next.js (App Router) + TypeScript + Tailwind**, **TailAdmin** as the
> primary design base (cruip secondary). New app in **`web/`**; the existing Vite `frontend/` stays
> LIVE until the Next.js app hits parity, then we switch. Light theme only, 4-portal separation kept.
> Subagent per task, verify each (build + typecheck + lint + it runs), commit. ~20-50 focused tests where logic warrants.

## Reuse (not from zero)
- Design tokens (signal palette, fonts) from `frontend/src/index.css`; chart MATH (`waterfallLayout`,
  `ganttLayout`, `conformalScale`) is pure TS → port as-is. Data types (`types/stream.ts`, `types/api.ts`),
  the AG-UI decoder (`agui/decode.ts`), run-reducer logic.
- **Every backend accessor built in Phase 1** feeds a dashboard: `run_summary`, `harness_config`,
  `latency_summary`, `security_posture`, `optimization_summary`/`usage_tally`, `governance_dashboard`,
  `model_card`, eval `metric_configs`, red-team report, memory CRUD/cache + all the new stream names.

## Data consistency (hard rule)
One source per number: the dashboard reads the backend accessor / stream event; never recompute or fabricate.
Mirror ALL stream names in `web/.../streamNames.ts` (memory_write, memory_cache, guardrail_cache,
retrieval_cache, ml_model, ops_diagnose/gate/release, retrieval observability, …).

## Tasks (ordered, demo-safe)
1. **Scaffold** — `web/` Next.js App Router + TS + Tailwind; integrate TailAdmin design (layout/components,
   reimplemented in React if the template is HTML); port tokens + fonts (light-only); portal shell
   (admin/ai_team/devops/client) + nav; the API client + SSE/stream layer + full streamNames mirror; a
   placeholder Console route. Must `next build` + run.
2. **Console** — the money-shot live run (reasoning, orchestration flow, SHAP waterfall, conformal band,
   node gantt, rerank+provenance, guardrail cards, trust chain, tokens/cost) on the real stream. Highest jury value.
3. **MLOps** dashboard — model card + explain (SHAP/conformal) from `/ml/explain` + `model_card`.
4. **LLMOps** dashboard — trace→eval→diagnose→gate→release loop + params (ops stream + LoopParams).
5. **Evals** dashboard — RAGAS/DeepEval metrics + thresholds (metric_configs), data-consistent.
6. **Token-opt** dashboard — savings/routing (optimization_summary), measured.
7. **Memory + Cache** dashboard — recall/add/delete/expire + cache hit/miss/evict (the memory stream + CRUD).
8. **RAG + Graph** dashboard — the arsenal observability (which arms fired, RRF, rerank, spotlight, Self-RAG) + the real entity graph.
9. **Agentic harness** view+tweak — run_summary trace + harness_config knobs.
10. **Governance** dashboard — tenants/budgets/usage/audit (governance_dashboard).
11. **Security** dashboard — the posture surface (threat→control→status) + RiskMap.
12. **Latency** dashboard — per-node p50/p95 (latency_summary).
13. **Red-team** dashboard — the attack report (block rates, leaked probes).
14. **Admin dashboard** — rebuilt 100% real (kills the last 5 sample tiles); PAUSE for the owner's changes.
15. **Parity switch** — cut over from Vite `frontend/` to `web/`; retire the old app.

## Verify per task
`cd web && npx next build` (or `next lint` + `tsc`) green; the route renders with real/mock data; pure-logic tests
where warranted. The Vite app stays green until the switch.
