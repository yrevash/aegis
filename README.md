# TAIF S2 — Domain-Agnostic Agentic Platform ("the weapon")

> A pre-built, SOTA agentic core for the TAIF S2 Mumbai Regional Finals. The
> hackathon problem is revealed **blind on the day**, so we do **not** build a
> solution ahead of time — we build a reusable platform where only **five thin
> adapter pieces** change once the problem is known.

## Architecture (at a glance)

Production-grade upgrade (see `docs/ARCHITECTURE_REVIEW.md` and ADRs 0005–0008):
**multi-tenant governance** at the gateway, a **durable checkpointed** agent with an
**async approvals inbox**, **explicit hybrid retrieval** (RRF), and a **risk-tiered
human gate** (ML is a solution signal that informs the plan; the tool risk tier — not
ML confidence — decides when a run defers to a human).

```
                         ┌───────────────────────────────────────────────────────────┐
  Browser (Vite+React)   │   SSE stream of structured agent-step events               │
  ──────────────────────▶│  JWT auth (tenant + coarse_role claim) ─▶ Governance       │
   4 role-scoped portals │  POST /query ─▶ Guardrail ─▶ rewrite ─▶ hybrid+agentic RAG  │
   admin  /admin         │  (RRF) ─▶ ml_predict (signal) ─▶ Plan ─▶ tool-risk gate     │
   ai_team /ai-team      │  ┬▶ act   (LangGraph + PostgresSaver)     └▶ Human Gate     │
   devops /devops        │  answer-cache short-circuits the generation call            │
   client /client        │  ML informs the plan; the tool risk tier drives the gate    │
                         │  ─▶ Tool/Action (exactly-once) ─▶ Output rail ─▶ answer     │
                         └───────────────┬───────────────────────────────────────────-┘
                                         │
   LiteLLM gateway ◀── all model calls ──┤   custom OpenAI-compatible provider;
   → genailab.tcs.in fleet               │   cost + routing + **per-tenant budget/RPM/TPM**
                                         │
   Durable inbox: approvals table (PENDING→RESUMING lock) + SLA sweeper + resumer
   Stores:  Neo4j (graph) · Qdrant (vectors — ANN) · Postgres (tenants/users/budgets/
            ledger/approvals/checkpoints/audit) · Redis (near-exact cache) · Phoenix

   Trust stack:  conformal signal (MAPIE) + SHAP → tool-risk human gate → guardrails
                 → per-tenant governance → OTel + audit  (ML informs; risk tier gates)
```

Every model call funnels through the LiteLLM chokepoint, where a `contextvars`-threaded
`GovernanceContext` enforces the tenant/user budget **before** spend (a breach ⇒ a clean
terminal `budget_exceeded`, not runaway cost) and writes a durable usage ledger — while
the adapter and graph nodes never see tenancy. A gated run **checkpoints durably** and
can resume on any worker from a persisted approvals-inbox row.

**The winning sentence:** *cheap enough to scale, measurable enough to trust,
secure enough to buy — and it takes real actions, explainably.*

## Stack

| Layer (Aegis module)         | Tech                                                        |
|------------------------------|-------------------------------------------------------------|
| API                          | FastAPI (async, SSE), OpenAPI auto-docs                      |
| **Aegis Governance** (Postgres RLS + JWT) | **JWT** (`pyjwt`) + **Argon2id** (`argon2-cffi`) · **four-role RBAC** (`admin`/`ai_team`/`devops`/`client`, a signed `coarse_role` claim + fine `platform_admin`/`tenant_admin`/`user` tier; per-role guards + admin role-assignment with last-platform-admin lockout) · per-tenant budget/RPM/TPM + usage ledger · Postgres RLS enabled at startup (`create_all` + `bootstrap_rls()`, not a migration) on `users`/`usage_ledger`/`approvals`; `audit_log`/`chunks` are application-scoped |
| **Aegis Gateway** (LiteLLM)  | **LiteLLM** → custom OpenAI-compatible provider (`genailab.tcs.in`), budget-enforced chokepoint |
| **Aegis Router** (LangGraph) | LangGraph (plan-and-execute + tool loop) · **durable `PostgresSaver`** checkpoints · durable approvals **inbox** (SLA sweeper + idempotent resumer) |
| **Aegis Retrieval** (Neo4j/LightRAG + Qdrant) · **Aegis Cache** (Redis) | Context-aware **query rewrite** → bounded **agentic/Self-RAG retrieval loop** → hybrid: vector + graph + BM25 → **Reciprocal Rank Fusion** → LLM rerank · LightRAG · Neo4j · Qdrant vectors · Postgres KV · near-exact retrieval Redis cache **+ per-tenant/persona/role answer-level semantic cache** |
| **Aegis Signal** (XGBoost + MAPIE + SHAP) | XGBoost + MAPIE (conformal) + SHAP · **solution signal only** — informs the plan; never gates/defers/abstains (the human gate fires on tool risk tier). Graded bands (autonomous/defer/abstain) exist as an inert contract used only by the frontend mock |
| **Aegis Guardrails** (programmatic + NeMo Colang) | Guardrails AI / NeMo + API injection classifier · **Garak** red-team runner (`backend/scripts/garak_scan.py`, executed on the day) |
| **Aegis Evals** (RAGAS-style proxies + LLM judge) | Offline deterministic gate (`app/eval/`) · optional reasoning-model LLM-as-judge |
| **Aegis Trace** (OpenTelemetry → Phoenix) | OpenTelemetry `gen_ai.*` → Arize Phoenix (local, in-process) |
| Frontend                     | Vite + React + TS + Tailwind/shadcn + Recharts (Tremor-style API) + react-force-graph |

### Aegis modules

Every capability is a first-class **Aegis module** — a branded name paired with its
**honest underlying tech** (branding, never hiding). This table mirrors the live
manifest in `backend/src/app/capabilities.py`, served at `GET /platform/capabilities`
(and `GET /about`).

| Aegis module | Tech underneath | What it is | Status |
|---|---|---|---|
| **Aegis Gateway** | LiteLLM | Single model chokepoint: role routing, budgets, timeout, retry, usage ledger | live |
| **Aegis Router** | LangGraph | Multi-agent supervisor — routes a turn to the right specialist | live |
| **Aegis Memory** | Postgres + Qdrant | Long-term memory: episodic · semantic · procedural, bitemporal, consolidated | live |
| **Aegis Cache** | Redis | Semantic response cache | live |
| **Aegis Retrieval** | Neo4j/LightRAG + Qdrant | Hybrid RAG: vector + graph + BM25 → RRF → LLM rerank, spotlighting | live |
| **Aegis Signal** | XGBoost + MAPIE + SHAP | Trustworthy ML: ensemble + calibrated conformal intervals + SHAP | live |
| **Aegis Guardrails** | programmatic + NeMo Colang | Input/output rails: injection, PII, schema, content | live |
| **Aegis Evals** | RAGAS-style proxies + LLM judge | Trace-level + answer evaluation | live |
| **Aegis Loop** | native | LLM-Ops self-improvement: trace → eval → diagnose → tiered release | live |
| **Aegis Governance** | Postgres RLS + JWT | Multi-tenant RBAC, budgets, RLS, audit log | live |
| **Aegis Trace** | OpenTelemetry → Phoenix | End-to-end tracing (glass box) | live |
| **Aegis Tools / MCP** | native + MCP SDK | Risk-tiered tool registry + human gate, exposed over MCP | optional |

## The five swappable adapter pieces (`backend/src/app/adapter/`)

1. Data schema + synthetic generator  2. Tool definitions  3. Prompts + personas
4. ML features + target  5. Domain corpus. **Domain logic never leaks into the core.**

## Repo layout

```
backend/src/app/
  api/            # FastAPI routes + Pydantic contracts + SSE event schema
  core/           # config-driven model registry + LiteLLM gateway
  agent/          # LangGraph orchestration
  retrieval/      # LightRAG + stores + semantic cache
  ml/             # XGBoost + MAPIE + SHAP
  guardrails/     # input/output rails
  data/           # DB models, audit log, governance (RBAC role assignment)
  observability/  # OTel spans + Phoenix (incl. A2A handoff span attributes)
  platform/       # role-portal surfaces: stack · patches · risk-map · savings
  adapter/        # the five domain-specific pieces (swapped on the day)
frontend/         # Vite + React app
docs/             # mission context + ADRs + threat model
spikes/           # de-risk scripts (tool-calling, model list)
```

## Environment constraints

16 GB **Windows** hackathon machine, **no Docker**, **no GPU**. Everything is
local infra or API. Developed on macOS, deployed to Windows — all artifacts
portable, no OS-specific assumptions.

## Getting started

See `backend/README.md` (API) and `frontend/README.md` (UI). De-risk spikes in
`spikes/`. Mission context and rubric mapping in `docs/`.

### Key docs

- `docs/LEARNING_GUIDE.md` — end-to-end walkthrough of the platform (backend + frontend + AI).
- `docs/EVAL_STRATEGY.md` — the three-layer evaluation strategy (trace-level, deterministic retrieval gate, LLM-as-judge).
- `docs/SECURITY_OWASP_AGENTIC.md` — OWASP Top-10-for-Agentic-Apps mapping to the controls in this codebase.
- `docs/HONESTY_AUDIT.md`, `docs/AUDIT_ROUND2.md` — what is live vs. optional/not-wired, stated plainly.
