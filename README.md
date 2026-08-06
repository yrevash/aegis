# Aegis — a domain-agnostic enterprise agentic-AI platform

Aegis runs an AI **agent** that plans, retrieves knowledge, calls tools that can take
real actions, and answers — while staying **cheap** (small models where possible),
**trustworthy** (a real statistical model with calibrated uncertainty), **secure**
(guardrails + a human gate on risky actions), **multi-tenant** (roles + budgets + row
isolation), **observable** (every step traced), and **self-improving** (it grades its own
runs and can propose a better prompt a human approves).

The **engine** (`backend/src/app/*`) knows nothing about any business problem. All domain
meaning lives in one folder — `backend/src/app/adapter/*` — which is the only thing you
rewrite to point Aegis at a new domain. The shipped example is a customer-support /
service-request assistant; that is just the current adapter.

It deliberately embodies two reference architectures:

1. **The Harness** — the runtime that assembles context (system prompt + memory +
   retrieved knowledge + the ML signal + the question), runs a bounded *plan → act →
   reflect* loop, gates risky actions to a human, and streams the answer. It is the
   LangGraph state machine in `backend/src/app/agent/graph.py`.
2. **The LLM-Ops closed loop** — every answer is traced, evaluated, diagnosed, and any
   improvement flows back into the harness as a new (human-gated) system prompt
   (`backend/src/app/ops/*`).

---

## Quick start

Three run modes, one script each (`.sh` for macOS/Linux, `.ps1` twins for Windows):

```bash
./scripts/bootstrap.sh              # install backend + frontend deps, write .env files
./scripts/start.sh safe             # frontend only, in-browser mock — no backend, no DB
./scripts/start.sh lite             # backend with NO databases (SQLite) + live frontend
./scripts/start.sh full             # backend with Postgres/pgvector + Neo4j + Redis
```

- **`safe`** → open <http://localhost:5173>. The console ships a full in-browser **mock
  transport**, so the entire UI (streaming agent trace, knowledge graph, SHAP + conformal
  panel, human-approval gate, dashboards) runs with **zero backend**. Best for a first look.
- **`lite`** → a real FastAPI backend with **no external stores** (SQLite, in-process),
  streaming live over SSE. The fastest way to see the real agent run.
- **`full`** → the complete stack with all stores and in-process Phoenix tracing.

Demo logins (mock + lite): `admin`/`admin` (admin portal) · `user`/`user` (user portal).

Prefer to run it by hand, or wiring up the full stack? See **[`INSTALL.md`](INSTALL.md)**
for the copy-pasteable manual (prerequisites, env vars, store setup, verification).

---

## Architecture

```
   Browser (Vite + React)          FastAPI (async, SSE)
   ─────────────────────           ─────────────────────────────────────────────
    Admin  /admin      ┌─────────▶  JWT auth (tenant claims) → Governance (budget/RLS)
    Inbox  /approvals  │            POST /query → Guardrail → Hybrid retrieval (RRF)
    User   /app  ──────┘              → ml_predict (signal) → Plan → tool-risk gate ┬→ act
                       ▲                                    (LangGraph + PostgresSaver) │
    SSE stream of  ────┘              ML informs the plan; the tool risk tier          │
    agent-step events                drives the human gate ────────────────────────▶ Human gate
                                      → Tool/Action (exactly-once) → Output rail → answer

   All model calls funnel through one LiteLLM gateway  →  cost + role routing +
   per-tenant budget / RPM / TPM  (an OpenAI-compatible endpoint, configurable)

   Stores:  Neo4j (graph) · Postgres + pgvector (tenants/users/budgets/ledger/
            approvals/checkpoints/audit/vectors) · Redis (semantic cache) · Phoenix (traces)

   Trust stack:  conformal signal (MAPIE) + SHAP → tool-risk human gate → guardrails
                 → per-tenant governance → OpenTelemetry + audit
```

Every model call passes through the LiteLLM chokepoint, where a `contextvars`-threaded
`GovernanceContext` enforces the tenant/user budget **before** spend (a breach ⇒ a clean
terminal `budget_exceeded`, not runaway cost) and writes a durable usage ledger — while the
adapter and graph nodes never see tenancy. A gated run **checkpoints durably** and can
resume on any worker from a persisted approvals-inbox row (exactly-once tool execution).

### Aegis modules

Every capability is a first-class **Aegis module** — a branded name paired with its
**honest underlying tech** (branding, never hiding). This table mirrors the live manifest
in `backend/src/app/capabilities.py`, served at `GET /platform/capabilities` and `GET /about`.

| Aegis module | Tech underneath | What it is | Status |
|---|---|---|---|
| **Aegis Gateway** | LiteLLM | Single model chokepoint: role routing, budgets, timeout, retry, usage ledger | live |
| **Aegis Router** | LangGraph | Multi-agent supervisor — routes a turn to the right specialist | live |
| **Aegis Memory** | Postgres + pgvector | Long-term memory: episodic · semantic · procedural, bitemporal, consolidated | live |
| **Aegis Cache** | Redis | Semantic response cache | live |
| **Aegis Retrieval** | Neo4j/LightRAG + pgvector | Hybrid RAG: vector + graph + BM25 → RRF → LLM rerank | live |
| **Aegis Signal** | XGBoost + MAPIE + SHAP | Trustworthy ML: ensemble + calibrated conformal intervals + SHAP | live |
| **Aegis Guardrails** | programmatic + NeMo Colang | Input/output rails: injection, PII, schema, content | live |
| **Aegis Evals** | RAGAS-style proxies + LLM judge | Trace-level + answer evaluation | live |
| **Aegis Loop** | native | LLM-Ops self-improvement: trace → eval → diagnose → tiered release | live |
| **Aegis Governance** | Postgres RLS + JWT | Multi-tenant RBAC, budgets, RLS, audit log | live |
| **Aegis Trace** | OpenTelemetry → Phoenix | End-to-end tracing (glass box) | live |
| **Aegis Tools / MCP** | native + MCP SDK | Risk-tiered tool registry + human gate, exposed over MCP | optional |

---

## Reuse it for your domain

The engine is domain-agnostic. To point Aegis at a new problem you rewrite **only**
`backend/src/app/adapter/*` — five thin pieces:

1. **Data schema + synthetic generator** — your entities and a seed dataset.
2. **Tool definitions** — the actions the agent may take, each with a risk tier.
3. **Prompts + personas** — system prompt(s) and role personas.
4. **ML feature spec + target** — what Aegis Signal predicts and explains.
5. **Domain corpus** — the knowledge Aegis Retrieval indexes.

The engine reaches the domain only through the hooks in `agent/deps.py`; domain logic
never leaks into the core. Full contract in
[`docs/learn/50-extend-for-your-domain.md`](docs/learn/50-extend-for-your-domain.md).

---

## Repo layout

```
backend/src/app/
  api/            # FastAPI routes + Pydantic contracts + SSE event schema
  core/           # config-driven model registry + LiteLLM gateway + governance ctx
  agent/          # LangGraph orchestration (harness) + multi-agent router
  memory/         # episodic / semantic / procedural memory (bitemporal, consolidated)
  retrieval/      # hybrid RAG (LightRAG + stores) + semantic cache
  ml/             # XGBoost + MAPIE (conformal) + SHAP
  guardrails/     # input/output rails (programmatic + NeMo Colang)
  eval/           # offline quality gate
  ops/            # the LLM-Ops closed loop (trace → eval → diagnose → release)
  observability/  # OpenTelemetry spans → Phoenix
  data/           # DB models, RLS, audit log, approvals inbox, checkpoints
  mcp/            # MCP tool facade
  capabilities.py # the Aegis module manifest (served at /platform/capabilities)
  adapter/        # the five domain-specific pieces — the only thing you rewrite
frontend/         # Vite + React 19 + TypeScript console
docs/             # architecture, ADRs, and the zero-knowledge learning set (docs/learn/)
scripts/          # bootstrap / preflight / start (safe|lite|full), mac + Windows
```

---

## Verify

**Backend** (from `backend/`, virtualenv active):

```bash
python -m pytest tests -q      # 469 passed, 1 skipped (the opt-in LLM-judge)
ruff check src tests           # All checks passed!
```

**Frontend** (from `frontend/`):

```bash
pnpm test      # 167 passed
pnpm build     # tsc (strict) + vite build — clean
pnpm lint      # oxlint — 0 errors
```

The offline **quality gate** (`tests/eval/`) drives the real hybrid-retrieval path over a
fixed seed corpus and fails if context-precision/recall or groundedness regress.

---

## Learn it from zero

New to the codebase? Start at **[`docs/LEARNING_GUIDE.md`](docs/LEARNING_GUIDE.md)**, then
follow the numbered onboarding set in [`docs/learn/`](docs/learn/) — no prior context
assumed, every claim names the real file:

`00` overview · `10` AI concepts from scratch · `20` backend · `30` frontend ·
`40` one request end-to-end · `50` extend for your domain · `60` run & operate.
