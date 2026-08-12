# Aegis SOTA-Everything Program — master plan

> The plan of record. Supersedes `2026-08-12-make-it-real-sota.md` (its tasks fold in).
> Order: **SOTA every backend module → clean unused code → full Next.js frontend rewrite
> (all dashboards) → pipelines.** Frontend is LAST. Subagent-per-task, verify each, commit.

## Principles (from the owner)
- **Real, enforced, used, streamable** — nothing showoff. Every SOTA piece actually runs; no
  silent RAM fallback (fail loud like the real DBs).
- **Prefer the industry SOTA library** over homegrown code (NeMo, Presidio, Qdrant, RedisVL,
  RAGAS/DeepEval, garak, OTel/Phoenix, LightRAG…). Take it and wire it in.
- **Data consistency is critical** — every surface reads the same authoritative source; no
  drift between what the agent did, what the DB holds, and what the UI shows.
- **Clean unused code** as we go — dead modules/knobs/files removed (very important).
- **Testing: focused, not exhaustive** — ~20–50 tests per module for the main behavior, not
  500–1000. Per-module test hardening is a later pass.
- **Frontend built LAST**, as a full **Next.js + Tailwind** rewrite, using the two cloned
  templates (`.frontend-templates/cruip`, `.frontend-templates/tailadmin`) as the design base.
  So per-module UIs/dashboards are built in that phase — backend now emits the data + stream
  events; UIs render them later.

## Aligns to the jury rubric (`docs/JURY_RUBRIC.md`)
Prototype 25% (real working system) · Hypothesis 20% (the platform) · Roadmap 10% (this infra)
= the top levers. Business Impact 15% = token-opt/evals/savings metrics. Articulation 15% = the
Next.js console.

---

## PHASE 0 — Finish the vector foundation (in progress)
- **TV1 DONE** — retrieval vectors → Qdrant (embedded local / server, fail-loud), pgvector out of
  the retrieval extra. Committed `e033d13`.
- **TV2** — memory vector recall → Qdrant (embeddings out of pgvector; tenant/subject scoped).
- **TV3** — remove `aegis.data` `VectorType` + purge `pgvector` from every extra/config; adopt it
  in `AEGIS_MODE` boot (Qdrant required in full mode). Postgres stays for relational/KV/governance.

## PHASE 1 — SOTA every backend module (one at a time; each: SOTA lib → real+enforced+streamed → data-consistent → SDK-constructible + stream method → clean unused → 20–50 tests → verify → commit)
1. **Memory** — RedisVL `SemanticCache` (TTL + eviction/scaling), durable pgvector→Qdrant bitemporal
   tier, mem0/Zep/GenAgents kept, tenant-scoped + **shared across agents**, full CRUD + delete/expiry,
   stream every op (recall/check/add/delete).
2. **Cache** — unify answer-cache + semantic-cache; make every hit/miss/evict/TTL observable (stream +
   metrics); wire the dead guardrails injection cache or delete it.
3. **Retrieval / RAG arsenal** — ensure ALL methods real + selectable: vector, graph, BM25, RRF,
   LLM-rerank, spotlight, Self-RAG loop, query-rewrite/HyDE. Expose which ran + tunable knobs.
4. **Graph** — real entity/relation extraction DONE; polish + expose tweak knobs.
5. **Guardrails** — NeMo engine + content-safety + topical + grounding DONE; **activate grounding
   end-to-end** through the agent graph (real contexts); wire red-team hooks (see 14).
6. **ML / MLOps** — model card, SHAP, conformal, ensemble; the train/predict/explain flow + params.
7. **LLMOps** — trace→eval→diagnose→gate→release loop + its parameters, real + shown.
8. **Token optimization** — the routing/caching/small-model savings optimizer; real measured savings
   + the numbers a dashboard needs.
9. **Evals** — RAGAS + DeepEval real; **data-consistent** eval store; the numbers for its own dashboard.
10. **Agentic harness** — the LangGraph orchestration; expose config (gate risk, max iterations,
    self-repair) so it's tweakable; full run trace as data.
11. **Governance** — RBAC/budgets/audit/RLS real + the dashboard data (tenants/budgets/usage).
12. **Security** — auth (JWT/Argon2), injection, RLS, secrets; a security posture summary surface.
13. **Latency** — real per-node + p95 telemetry (OTel) surfaced as data, not sample.
14. **Red teaming** — a real red-team harness (garak / prompt-injection + jailbreak suites) run
    against the guardrails; results as data.

## PHASE 2 — Dead-code / unused sweep
Repo-wide: remove unused modules, dead config knobs (`guardrails_engine` resolved, `AEGIS_MODE`
adopted), unwired scaffolding, and any remaining duplicate/stale files. Verify nothing breaks.

## PHASE 3 — Frontend: full Next.js + Tailwind rewrite (LAST)
Rebuild the frontend in **Next.js + Tailwind**, using `cruip` + `tailadmin` as the design system,
LIGHT theme, keeping the 4-portal separation. A clean, to-the-point, **data-consistent** dashboard
per module: Memory (+cache), RAG/retrieval, Graph, Guardrails, MLOps, LLMOps, Token-opt, Evals,
Agentic harness (view + tweak), Cache, Governance, Security, Latency, Red-team. Stream everything
live. This is where the per-module UIs live.

## PHASE 4 — Pipelines
Compose the mastered modules into end-to-end pipelines.

## Cadence
Subagent per task; I verify (real run + focused suite + ruff/lint) before commit; push per task;
pause only at anything you flagged (the dashboard changes come in Phase 3 anyway).
