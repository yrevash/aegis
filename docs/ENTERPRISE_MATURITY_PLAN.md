# Enterprise Maturity Plan — Aegis platform

Turn the working hackathon solution into a **cohesive, reusable, enterprise AI product**:
one platform identity, a clean repo, zero-knowledge onboarding docs, and a polished,
less-texty, less-"AI-ish" UI with real charts/motion. We still bill it for our specific
problem statement, but the architecture is **domain-agnostic and integrable** — any other
department/problem drops in by swapping only `app/adapter/*`.

## 1. Product identity — "Aegis" and its modules
Every capability is a first-class **Aegis module**, presented with a branded name **plus the
honest underlying tech** (branding, never hiding — keeps the no-fakes bar). Canonical map:

| Aegis module | What it is | Tech underneath |
|---|---|---|
| **Aegis Gateway** | single model chokepoint: role routing, budgets, timeout, retry, usage ledger | LiteLLM |
| **Aegis Router** | multi-agent supervisor — routes a turn to the right specialist | LangGraph |
| **Aegis Memory** | long-term memory: episodic · semantic · procedural, bitemporal, consolidated | Postgres + pgvector |
| **Aegis Cache** | semantic response cache | Redis |
| **Aegis Retrieval** | hybrid RAG: vector + graph + BM25 → RRF → LLM rerank, spotlighting | Neo4j/LightRAG + pgvector |
| **Aegis Signal** | trustworthy ML: ensemble + calibrated conformal intervals + SHAP | XGBoost + MAPIE + SHAP |
| **Aegis Guardrails** | input/output rails: injection, PII, schema, content | programmatic + NeMo Colang |
| **Aegis Evals** | trace-level + answer evaluation | RAGAS-style proxies + LLM judge |
| **Aegis Loop** | LLM-Ops self-improvement: trace → eval → diagnose → tiered release | (native) |
| **Aegis Governance** | multi-tenant RBAC, budgets, RLS, audit log | Postgres RLS + JWT |
| **Aegis Trace** | end-to-end tracing (glass box) | OpenTelemetry → Phoenix |
| **Aegis Tools / MCP** | risk-tiered tool registry + human gate, exposed over MCP | native + MCP SDK |

Applied in three places: a **capabilities manifest** (a real backend module + optional
`/about` endpoint listing modules/tech/status), the **UI labels** (nav + panel titles), and
the **docs**. Underlying code/packages are unchanged — this is presentation + productization.

## 2. Reusability / integrability (the platform story)
Already true architecturally (domain-agnostic `app/*`; domain meaning only in `app/adapter/*`
— personas, tools, ML spec, memory spec, skills, corpus). Make it explicit: a capabilities
manifest, an "integrate Aegis in your platform" doc (adapter contract, the 6 adapter pieces,
what a new domain must supply), and clean module boundaries. No constraint from our specific
problem leaks into the core.

## 3. Repo cleanup (delete dead weight — from docs/AUDIT_ROUND2.md)
- Retired **autonomy-band machinery** (~200 LOC): `deps.assess_uncertainty/relative_width/
  classify_autonomy`, `AutonomyBand`, `events.abstained`, `state.band/abstained`,
  `autonomy_policy_for`, `ml_explanation`'s 4 always-None args, `AgentConfig.uncertainty_*/
  abstain_*/high_risk_never_autonomous/autonomy_bands_enabled` + their tests.
- **`retrieval/pgvector_index.py`** (unused module) + test, or wire into bootstrap.
- **`graph._select_checkpointer`** duplicate → collapse to `data/session.get_agent_checkpointer`.
- **`log_level`** setting: apply in logging setup or remove (+ INSTALL rows).
- Sweep for any other unreferenced files/fixtures/scratch. Keep the suite green throughout.

## 4. Learning docs — zero-knowledge onboarding structure (`docs/learn/`)
A teammate with NO context can learn the whole system. Structured set, not one file:
- `00-overview.md` — what Aegis is, the two reference architectures, the big diagram.
- `10-ai-concepts.md` — the AI ideas from scratch (LLM, RAG, embeddings, agents, tools,
  guardrails, conformal ML, memory, the LLM-Ops loop) — plain-language, no jargon dumps.
- `20-backend.md` — every Aegis module, its files, and how they connect (per §1).
- `30-frontend.md` — the console: surfaces, state, api client, mock mode, design system.
- `40-request-flow.md` — one request end-to-end, step by step, with a sequence diagram.
- `50-extend-for-your-domain.md` — the adapter contract; how another team reuses Aegis.
- `60-run-and-operate.md` — install, run (lite + full), env, day-of runbook.
Each: real architecture + usage diagrams (mermaid), concrete file references, no fakes.
Keep `LEARNING_GUIDE.md` as the one-page entry that links into `docs/learn/`.

## 5. Frontend rework (the visual maturity pass)
Current UI is good but: **too much text** for some readers, reads **too "AI-ish"**, and is
**too linear** (stacked rows). Target — a premium, calm, executive SaaS product:
- **Less text:** lead with numbers/visuals; move prose to tooltips/expandanbles; short labels.
- **Less "AI-ish":** professional product copy (branded Aegis modules), drop jargon from
  primary surfaces; keep the glass-box depth one layer down.
- **Better views (not straight lines):** varied, purposeful layouts — bento grids, hero
  KPIs, side-by-side comparisons, a capability map — instead of stacked full-width rows.
- **Real charts + motion:** meaningful recharts (trends, donuts, sparklines, funnels) and
  tasteful micro-animations (count-ups, transitions, reveal-on-scroll) — never gratuitous.
- Cohesive design tokens, light+dark, responsive, accessible. A design SPEC precedes the build.

## 6. Sequence (multi-agent)
1. **Cleanup** (backend dead code) — verified green. 2. **Frontend design spec** (research +
concrete redesign spec) in parallel. 3. **Capabilities manifest + branding** (backend module +
docs + UI labels). 4. **Frontend rework** (per-surface build agents to the spec). 5. **Learning
docs** set (reflects the cleaned, branded system). 6. **Audit + verify + push.** Each wave
verified (backend `pytest`+`ruff`; frontend `pnpm build/lint/test`) and committed before the next.
