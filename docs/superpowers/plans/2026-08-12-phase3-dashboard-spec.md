# Phase 3 — Dashboard Design Spec (to the core)

Companion to `2026-08-12-phase3-nextjs-frontend.md`. Next.js (App Router) + TS + Tailwind in `web/`,
**TailAdmin** design base, **light theme only**, 4-portal separation. Every panel names its REAL backend
data source (accessor / endpoint / stream event) — one source per number, nothing fabricated.

## A. Design system
- **Tokens** (ported from `frontend/src/index.css`): signal palette — agent · graph · risk · block · ml · ok
  (each fill + ink); neutrals (surface/background/border, cool-biased); fonts — Space Grotesk (display),
  JetBrains Mono (data/numbers), Inter (body). Utilities `.eyebrow`, `.tabular`, `.shadow-card/hover/pop`.
- **TailAdmin components → React, restyled to our tokens** (not TailAdmin blue): `Sidebar` (grouped nav),
  `Topbar` (breadcrumb + Present + user + mobile drawer), `Card`, `Badge`, `StatCard/KpiTile`, `Table`,
  `Gauge`, `InfoTip`, `Tabs`, `Toggle`, chart wrappers.
- **Chart primitives** (port pure-TS math as-is): `ShapWaterfall` (waterfallLayout), `ConformalBand`
  (conformalScale), `NodeGantt` (ganttLayout), `AreaChart`/`BarChart`/`DonutChart`/`Sparkline`, and a
  **deterministic entity graph** (typed nodes + relation edges + kind legend).

## B. Data / stream architecture
- Typed API client mirroring the backend endpoints; **SSE decoder** (port `agui/decode.ts`); `streamNames.ts`
  mirroring EVERY `aegis.core.stream_names.ALL`. A run-state reducer folds the `/query` stream.
- **Per-dashboard hooks** each read ONE source (an accessor endpoint or a stream event) — the data-consistency
  rule. Mock + live mode (probe `/healthz`, honest offline banner), like the current app.
- New backend read-endpoints to add (thin, over the Phase-1 accessors) so dashboards have data:
  `/harness/config`, `/latency`, `/security/posture`, `/gateway/optimization`, `/ops/params`, `/governance/dashboard`,
  `/ml/model-card`, `/evals/report`, `/redteam/report` (each returns the accessor's `as_dict()`).

## C. Dashboards, by portal (panels → REAL data source → components)

### ai_team portal
**1. Console** (money-shot live run) — the `/query` SSE stream drives everything:
- QueryBar (persona select, Run/Reset) · TrustBar (uncertainty-bounded/explainable/guarded/approved/traced from run state) · StreamBanners.
- Left: **Activity trace** (ordered events → run reducer) · **Reasoning lane** (`reasoning` events).
- Center: **Orchestration flow** (`run_summary` nodes / node_started+finished → deterministic flow map) · **Glass-box NodeGantt** (`node_finished` duration/tokens/cost) · **Entity graph** (`retrieval_citations` touched entity nodes/edges).
- Right: **Confidence** (Gauge + ConformalBand ← `conformal_interval`) · **Why** (ShapWaterfall ← `shap_explanation`) · **Sources** (rerank scoreboard + provenance donut ← `retrieval_citations` + `retrieval_cache`) · **Efficiency** (tokens/cost ← usage + `model_call`) · **Guardrails** (verdict cards ← `guardrail_verdict` + `guardrail_cache`) · **Answer**.
- **Approval spotlight** on the human gate (`approval_required` → approve/reject).

**2. MLOps** — model card (`GET /ml/model-card` → ensemble members+weights, target/features, conformal coverage, calibration sizes, `data_source`) as StatCards; **Explain a prediction** (`POST /ml/explain` → Gauge + ConformalBand + ShapWaterfall); `ml_model`/`conformal_interval`/`shap_explanation` stream.

**3. LLMOps** — the loop: **EvalTrend** (`eval_result` over runs) · **Diagnose** (drafts + risk tier ← `ops_diagnose`) · **Gate** (eval delta vs margin + risk ← `ops_gate_decision`) · **Releases** (promoted/staged/rejected ← `ops_release`) · **LoopParams** panel (the knobs ← `/ops/params`, shown as tunable data).

**4. Evals** — metric cards (context-precision/recall/groundedness/tool-selection: value vs threshold + pass/fail ← `/evals/report` `metric_configs`) · regression-gate summary · answer-relevancy shown **not-computed** (honest) · per-case table.

**5. Token-opt** — savings hero (cost_saved_usd, small_model_share ← `/gateway/optimization`) · per-role breakdown Table (calls/tokens/cost, small-model flag) · routing config (role→model map, fallbacks, baseline) · `model_call` stream (fallback_fired).

**6. Memory + Cache** — working-memory assembly · semantic facts (bitemporal) · episodic sessions · recall debug (checked + scores ← `memory_recall`) · **CRUD** (list/add/forget) · **Cache panel** (hit/miss/evict/TTL ← `memory_cache`) · write log (`memory_write`).

**7. RAG / Retrieval** — arsenal observability (arms fired vector/graph/bm25 + counts, RRF, rerank scores, spotlight applied, query-rewrite, Self-RAG rounds ← RetrievalObservability on `retrieval_citations`) · provenance donut · rerank scoreboard.

**8. Graph** — the real **entity knowledge graph** (typed entity nodes + relation edges + kind legend), scoped to the query evidence (`/graph` + retrieval touched entities).

**9. Agentic harness** — **run trace** (ordered nodes, timings, gate, tools, iterations, outcome ← `run_summary`) as a timeline/table · **Tweak** panel (11 knobs with type/default/allowed ← `/harness/config`).

**10. Access demo** — two-role side-by-side simulation (port existing).

### admin portal
**11. Overview (admin dashboard)** — 100% REAL: value tiles (savings ← optimization, quality + cache-hit ← `/metrics`), cost-saved, **queries/actions/p95** (p95 ← `/latency` — real now), model-mix donut, routing table, cost-trend (real in-session series), query-volume. **PAUSE for owner's changes.**

**12. Governance** — tenants + per-tenant budget/spend/remaining (← `/governance/dashboard` budget_status) · users + roles · usage summary (calls/tokens/cost) · audit tail.

**13. Approvals** — human-gate inbox (pending, approve/reject).

**14. Roles & Access** — RBAC grants.

### devops portal
**15. Overview** — value at a glance.
**16. Security** — **posture table** (threat → control → module → status: enforced/partial/not_covered, honest partials ← `/security/posture`) + RiskMap (OWASP-Agentic).
**17. Red-team** — attack report (per-category block rate, leaked probes, FP rate, pass/fail ← `/redteam/report`) + "Run red-team" action.
**18. Latency** — per-node p50/p95/max + slowest node + run-duration percentiles (← `/latency`) + latency timeline.
**19. Stack & Versions** (SBOM) · **20. Patch Check** · **21. Audit**.

### client portal
**22. Overview · Savings · Risk map · Access demo** — value/savings/risk/sim (port existing, real data).

## D. Build order (each: build → `next build`+lint+tsc green → renders with real/mock data → commit)
Scaffold (done sep.) → **Console** → MLOps → LLMOps → Evals → Token-opt → Memory+Cache → RAG → Graph →
Agentic harness → Governance → Security → Red-team → Latency → Access-demo/Savings/Risk → **Admin dashboard (PAUSE)** →
devops/client overviews + stack/patch/audit → **parity switch** (retire Vite `frontend/`).

## E. Backend endpoint additions (thin, one commit early in Phase 3)
Add the read-only routes in `backend/src/app/api/` that surface the Phase-1 accessors' `as_dict()`
(`/harness/config`, `/latency`, `/security/posture`, `/gateway/optimization`, `/ops/params`,
`/governance/dashboard`, `/ml/model-card`, `/evals/report`, `/redteam/report`) — RBAC-scoped, data-consistent.
