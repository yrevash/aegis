# Aegis Console + ai_team Portal Redesign — Design Spec

- **Date:** 2026-08-12 · **Branch:** continue on `feat/aegis-module-contract` (or a new `feat/console-redesign`)
- **Status:** Approved design direction (white instrument-panel mockup approved); building.
- **Design reference:** approved mockup `.superpowers/brainstorm/23333-1786498530/content/mission-control-white-v2.html`

## 1. Goal

Make Aegis's "show your work" **seen** — a SOTA, white, **instrument-panel** console that renders the live agent
run as real charts (SHAP waterfall, conformal band, per-node cost/latency gantt, rerank + provenance, trust
chain, tokens/cost), with **clarity + legible numbers as a hard requirement**. Keep the portal separation, and
give the `ai_team` portal **clear distinct pages: Console · MLOps · LLMOps · Memory · Simulation**.

## 2. Hard rules (from the user)

- **Light / white theme ONLY.** Extend the existing light token system in `frontend/src/index.css`; never a dark UI.
- **Keep portal separation** — `admin`/`devops`/`client` portals keep their own pages, untouched. Only `ai_team` is restructured.
- **Clarity + number legibility is mandatory.** Values live in **aligned tabular-mono columns, never inside bars**
  (`.tabular` + `font-mono`, right-aligned, min 12–13px, ink or accent — the exact fix from the mockup). Generous
  spacing, uppercase `.eyebrow` section labels with a subsystem color dot, strong contrast.

## 3. Reuse (do NOT rebuild)

- **Data layer as-is:** `state/useRunStream.ts` → `RunState` (has `ml` {shap_attribution, conformal_interval,
  conformal_confidence, prediction_set_size}, `retrievalScores`, `nodeLedger` {duration_ms, tokens, cost_usd per
  node}, `usage`, `guardrails`, `provenance`, `reasoning`, `answer`, `toolCalls`, `activeNodeIds`, `lastSignal`…).
  The **mock transport drives the full rich scenario offline** — build/verify against it.
- **Design tokens:** `index.css` — `--surface`/`--background`/`--surface-2`/`--border`, the signal palette
  (agent/graph/risk/block/ok/ml, each fill+ink), fonts (Space Grotesk display, JetBrains Mono data), `.t-*`,
  `.eyebrow`, `.tabular`, `.shadow-card/hover/pop`, motion tokens + sanctioned animations. `config/signals.ts` mirror.
- **Chart kit:** `components/charts/*` (AreaChart/BarChart/DonutChart/ChartTooltip), `components/ui/Gauge.tsx`,
  `components/metrics/Sparkline.tsx`. Recharts 3.10.
- **LLMOps:** `components/ops/OpsView.tsx` (EvalTrend/PendingReleases/DiagnosePanel/PromptTimeline/PromptDiff) —
  the trace→eval→diagnose→release loop already exists; relabel + light polish, don't rebuild.
- **Orchestration model:** `components/console/orchestration.ts` (`resolveFlow(state)`, FLOW_NODES/EDGES).

## 4. Build (what's new / restyled)

### 4a. Chart primitives (new — pure logic `.ts` + `.tsx` + `.test.ts`, colored from SIGNALS)
- **`ShapWaterfall`** — cumulative waterfall: base → each signed feature step → prediction. Pure `shapWaterfall.ts`
  (compute cumulative positions from `ShapFeature[]` + base) + `ShapWaterfall.tsx`. Red raises / green lowers; value
  labels in a right column. Replaces the top-3 `ShapBar` divs with a real, complete waterfall (show all drivers).
- **`ConformalBand`** — a proper band plot from `conformalScale.ts` (exists: `conformalDomain`/`toPercent`/
  `formatValue`): number line, shaded interval that spans the true endpoints, point marker, endpoint labels bold ink,
  plain-language caption. Fix the mockup's band-vs-label alignment (band spans exactly lo→hi).
- **`NodeGantt`** — per-node cost/latency timeline: pure `nodeGantt.ts` (scale `nodeLedger` durations to start-offset
  + width) + `NodeGantt.tsx`. Bars show the timeline; **ms + $ in two aligned right columns** (the legibility fix).
- Reuse `Gauge` for confidence; consider a small `TrustChain` strip (conformal ✓ SHAP ✓ guarded ✓ traced ✓ gated ✓).

### 4b. Console rebuild (restyle existing panels to the instrument look + the bento)
Rebuild `components/console/MoneyShotConsole.tsx` to the approved 3-column instrument bento, wiring the (restyled)
panels — all still pure `RunState`-prop components:
- Left: **Reasoning** (streaming CoT) · **Retrieval** (rerank bars + provenance donut).
- Center: **Orchestration** node-flow (from `resolveFlow`) · **Glass-box `NodeGantt`** · **`ShapWaterfall`**.
- Right: **Conformal** (Gauge + `ConformalBand`) · **Efficiency** (tokens/cost/saved/cache stats) · **Trust & guardrails**
  (guardrail pills + trust chain).
- Keep `QueryBar`, `StreamBanners`, `ApprovalSpotlight`/`ApprovalCard`, the live-beat pulse. The `KnowledgeGraph`
  force-graph can stay as an optional hero/toggle, but the node-flow + charts are the primary "show your work".
- Every panel: `.eyebrow` label + subsystem dot, numbers in aligned tabular-mono columns, `.shadow-card`, hairline.

### 4c. ai_team portal restructure (`routes/Portal.tsx`)
`ROLE_SECTIONS.ai_team = [console, mlops, llmops, memory, simulation]` (+ keep `dashboard` as "Overview" if desired).
- **Console** — the rebuilt live-run console (relabel from current `console`).
- **MLOps** (NEW section `mlops`) — the `aegis.ml` surface: a **model card** (features/target, ensemble members,
  conformal coverage guarantee), the latest/explore SHAP explanation (`ShapWaterfall`) + conformal calibration
  (`ConformalBand`), driven by `POST /ml/explain` + `mockMlExplain` fixture. An "explain a prediction" interactive panel.
- **LLMOps** (relabel `ops`) — the existing `OpsView` loop (prompts/evals/diagnose/releases), clearly labeled "LLMOps".
- **Memory**, **Simulation** — keep.
Add the `mlops` lazy import + `SECTIONS.mlops` entry + `SECTIONS.llmops` (or relabel `ops`). admin/devops/client
`ROLE_SECTIONS` untouched.

## 5. Testing & proof

- **Pure-logic tests** (node-env, co-located `*.test.ts` — the repo convention): `shapWaterfall.test.ts` (cumulative
  math + sign), `nodeGantt.test.ts` (scaling/offsets), extend `conformalScale.test.ts` for band endpoints. Keep all
  new chart math in sibling `.ts` so it's testable without a DOM.
- **Full frontend suite green:** `cd frontend && npx vitest run` (235 baseline) + `npx oxlint` clean.
- **Visual proof:** the console renders the full mock run (SHAP waterfall, conformal band, gantt, rerank, provenance,
  trust chain, tokens) with legible numbers; MLOps + LLMOps pages render and route correctly in the `ai_team` portal;
  admin/devops/client portals unaffected. Build passes: `npx vite build` (or `tsc` typecheck).

## 6. Definition of done

The `ai_team` portal has clear **Console / MLOps / LLMOps / Memory / Simulation** pages; the Console is a white
instrument-panel rendering the live run as real, legible charts (numbers in aligned tabular-mono columns, no
clipping); the new chart primitives (SHAP waterfall, conformal band, node gantt) have passing pure-logic tests; the
frontend suite + lint + build are green; other portals are untouched.
