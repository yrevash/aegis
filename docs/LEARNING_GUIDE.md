# Learning Guide — the Aegis platform, from zero

**New here? Start with this page, then follow the numbered set in [`docs/learn/`](learn/).**
No prior context is assumed. This is the one-page entry point; the detail lives in the
structured onboarding docs it links to. Every claim in those docs names the real file so
you can go read the code.

---

## What Aegis is (the one-paragraph version)

**Aegis is a domain-agnostic agentic-AI platform.** You ask it something; an AI *agent*
plans, retrieves knowledge, optionally calls tools that can change data, and answers —
while staying **cheap** (small models where possible), **trustworthy** (a real statistical
model with calibrated uncertainty), **secure** (guardrails + human approval for risky
actions), **multi-tenant** (roles + budgets + row isolation), **observable** (every step
traced), and **self-improving** (it grades its own runs and can propose a better prompt a
human approves). The **engine** (`backend/src/app/*`) knows nothing about any business
problem; all domain meaning lives in one folder, `backend/src/app/adapter/*`, which is the
only thing you rewrite for a new problem.

It deliberately embodies two reference architectures (see `docs/HARNESS_LLMOPS_PLAN.md`):

1. **The Harness** — the runtime that assembles context (system prompt + memory + retrieved
   knowledge + the ML signal + the question), runs a bounded plan → act → reflect loop,
   gates risky actions to a human, and streams the answer. It is the LangGraph state
   machine in `backend/src/app/agent/graph.py`.
2. **The LLM-Ops closed loop** — every answer is traced, evaluated, diagnosed, and any
   improvement flows back into the harness as a new (human-gated) system prompt
   (`backend/src/app/ops/*`).

The shipped example domain is an enterprise **customer-support / service-request** assistant
— that is just the current adapter; the engine would run any domain the same way.

## The onboarding set — read in order

| # | Doc | What you'll learn |
|---|---|---|
| 00 | [`learn/00-overview.md`](learn/00-overview.md) | What Aegis is · the two reference architectures · the big-picture diagram · the Aegis module map · core vs. adapter |
| 10 | [`learn/10-ai-concepts.md`](learn/10-ai-concepts.md) | Every AI idea from scratch: LLM, tokens, embeddings, RAG, agents, tools, guardrails, conformal ML + SHAP, memory, the LLM-Ops loop |
| 20 | [`learn/20-backend.md`](learn/20-backend.md) | Every backend module, its files, and how they connect (the module dependency map) |
| 30 | [`learn/30-frontend.md`](learn/30-frontend.md) | The console: surfaces, state management, the API client + mock mode, the design tokens |
| 40 | [`learn/40-request-flow.md`](learn/40-request-flow.md) | One request end-to-end, with a mermaid sequence diagram mapped to real nodes/files/events |
| 50 | [`learn/50-extend-for-your-domain.md`](learn/50-extend-for-your-domain.md) | The adapter contract: exactly what a new team supplies to reuse Aegis (and what not to touch) |
| 60 | [`learn/60-run-and-operate.md`](learn/60-run-and-operate.md) | Install + run (lite vs. full), env vars, the day-of runbook, how to verify |

## The Aegis module map (branded name + honest tech)

Every capability is a first-class **Aegis module**, always shown with the real tech
underneath. Full detail in [`learn/20-backend.md`](learn/20-backend.md).

| Aegis module | Tech underneath | Where |
|---|---|---|
| **Aegis Gateway** — single model chokepoint (routing, budgets, timeout, retry, ledger) | LiteLLM | `core/llm.py` |
| **Aegis Router** — multi-agent supervisor | LangGraph | `agent/router.py` |
| **Aegis Memory** — episodic · semantic · procedural, bitemporal | Postgres + pgvector | `memory/*` |
| **Aegis Cache** — semantic response cache | Redis | `retrieval/cache.py` |
| **Aegis Retrieval** — hybrid RAG (vector + graph + BM25 → RRF → rerank) | Neo4j/LightRAG + pgvector | `retrieval/*` |
| **Aegis Signal** — ensemble + conformal intervals + SHAP | XGBoost + MAPIE + SHAP | `ml/*`, `adapter/ml_spec.py` |
| **Aegis Guardrails** — injection, PII, schema, content rails | programmatic + NeMo Colang | `guardrails/*` |
| **Aegis Evals** — trace-level + answer evaluation | RAGAS-style proxies + LLM judge | `ops/trace_eval.py`, `eval/*` |
| **Aegis Loop** — trace → eval → diagnose → tiered release | native | `ops/*` |
| **Aegis Governance** — RBAC, budgets, RLS, audit | Postgres RLS + JWT | `data/*`, `core/*` |
| **Aegis Trace** — end-to-end tracing (glass box) | OpenTelemetry → Phoenix | `observability/*` |
| **Aegis Tools / MCP** — risk-tiered tools + human gate over MCP | native + MCP SDK | `adapter/tools.py`, `mcp/server.py` |

## The golden rule for changing it

- **New business problem?** Rewrite only `app/adapter/*` (personas, tools, ML feature spec,
  memory fact schema, roster, knowledge corpus). The engine (`app/*`) does not change — it
  reaches the domain only through the hooks in `agent/deps.py`. See
  [`learn/50-extend-for-your-domain.md`](learn/50-extend-for-your-domain.md).
- **Everything is verified**: `cd backend && python -m pytest tests -q` and
  `ruff check src tests` must stay green; frontend `pnpm build && pnpm lint`. The honesty
  audits (`docs/HONESTY_AUDIT.md`, `docs/AUDIT_ROUND2.md`) exist because "claimed but not
  real" is the one thing this project does not ship — where a feature is optional or
  not-wired, the docs say so plainly.

### Deeper reference docs

| Doc | What it covers |
|---|---|
| [`docs/EVAL_STRATEGY.md`](EVAL_STRATEGY.md) | The three-layer evaluation strategy (trace-level, deterministic retrieval gate, LLM-as-judge). |
| [`docs/SECURITY_OWASP_AGENTIC.md`](SECURITY_OWASP_AGENTIC.md) | OWASP Top-10-for-Agentic-Apps mapping — each risk to the control that addresses it. |
