# Aegis Console — frontend

The watch-it-work surface for the platform: a dark **control-room** where an agent
takes real actions and the **trust stack is visible in real time**. It renders the
[money-shot](../docs/hackathon.md#7-the-money-shot-demo-design-toward-this-single-screen)
— streaming agent reasoning, an animating knowledge graph, an ML score with its
conformal interval and SHAP attribution, a human approval gate, and live
token/cost telemetry — all on one screen, each element a rubric axis being scored.

```
Every autonomous action is uncertainty-bounded, explainable, guarded,
human-approved, and fully traced.
```

The console ships **demo-ready**: with no backend it plays the full scenario from
an in-browser mock, so `pnpm install && pnpm dev` shows everything standalone.

## Run it

```bash
pnpm install
pnpm dev            # http://localhost:5173  (mock mode — no backend needed)
```

On the login screen use **Enter as user** or **Enter as admin** (demo mode), or
type any username — one containing `admin` lands in the admin portal.

Other scripts:

```bash
pnpm build          # tsc -b (strict typecheck) + production build
pnpm lint           # oxlint
pnpm preview        # serve the production build
```

To stream from a real backend, copy `.env.example` → `.env.local` and set
`VITE_USE_MOCK=false` and `VITE_API_BASE`.

## Stack

| Concern | Choice | Notes |
| --- | --- | --- |
| Build/dev | **Vite 8** + **React 19** + **TypeScript 6 (strict)** | scaffold toolchain, strict mode on |
| Styling | **Tailwind CSS v4** (CSS-first) + **shadcn/ui** | tokens via `@theme` in `src/index.css`; primitives are copy-owned in `src/components/ui` |
| Charts | **Recharts 3** with Tremor-style wrappers | see note below |
| Graph | **react-force-graph-2d 1.29** | canvas 2D force graph, animated on retrieval |
| Streaming | **hand-rolled SSE** via `fetch` reader | no CopilotKit / AG-UI / Vercel AI SDK |
| Routing | **react-router 7** | two role-scoped portals |
| Fonts | **Space Grotesk** (display) · **IBM Plex Sans** (body) · **JetBrains Mono** (telemetry) | self-hosted via `@fontsource` — no CDN, portable offline |

### A note on "Tremor" and Tailwind v4

The brief lists Tremor. The npm package `@tremor/react` (3.18.7) is **pinned to
Tailwind v3.4 and stale (~2 years)**; the current Tremor ("Tremor Raw") is
copy-paste **Recharts** components for Tailwind v4. Since shadcn + Vite now default
to Tailwind v4, we build the charts on **Recharts — Tremor's own charting engine —**
with small Tremor-flavoured wrappers (`data` / `index` / `category` API) in
`src/components/charts`. This keeps one modern toolchain and full styling control.

## The contract (do not drift)

`src/types/stream.ts` and `src/types/api.ts` are a **faithful mirror** of
`backend/src/app/api/schemas.py` — the locked `StreamEvent` discriminated union
and the endpoint request/response models. They are the single source of truth on
the frontend; regenerate from the Pydantic models rather than hand-editing shapes.

SSE wire format: `POST /query` streams events whose `data:` payload is a
`StreamEvent` JSON (carrying its own `type` discriminant). Because the request
needs a body we use a `fetch` reader, not `EventSource` (GET-only) — see
`src/api/sse.ts`.

## Architecture

Two **role-scoped surfaces**, gated by the authenticated role (RBAC):

- **`/admin`** — live console, dashboard, and the audit trail.
- **`/app`** — live console and a scoped dashboard.

```
src/
  types/          stream.ts, api.ts        — mirror of schemas.py (source of truth)
  api/            sse.ts (fetch-reader SSE parser), client.ts (REST),
                  transport.ts (interface), liveTransport.ts, config.ts, factory.ts
  mock/           mockTransport.ts (scripted money-shot), fixtures.ts
  state/          runReducer.ts (pure event → RunState), useRunStream.ts, useMetrics.ts
  auth/           AuthContext.tsx, RequireRole.tsx
  config/         signals.ts (the trust-colour taxonomy), personas.ts (domain adapter)
  components/
    ui/           shadcn primitives (button, card, badge, tabs, dialog, …)
    charts/       AreaChart, BarChart, DonutChart (Recharts wrappers)
    layout/       AppShell, Sidebar, Topbar, TrustBar (the signature)
    brand/        Logo
    trace/        AgentTracePanel, TraceRow, describeEvent   — view 2
    graph/        KnowledgeGraph, useElementSize             — view 3
    metrics/      KpiTile, EfficiencyPanel                    — view 4
    ml/           ShapPanel, ShapBar, ConformalInterval       — view 5
    approval/     ApprovalCard                                — view 6
    dashboard/    Dashboard, RoutingTable, data.ts            — view 1
    admin/        AuditLog
    console/      MoneyShotConsole, QueryBar, AnswerPanel     — the composed screen
  routes/         LoginPage, Portal
```

### The six views (`docs/frontend.md §4`)

1. **Live dashboard** — `dashboard/Dashboard.tsx`: KPI tiles + charts from `GET /metrics`.
2. **Streaming agent-trace panel** — `trace/AgentTracePanel.tsx`: renders
   `node_started` / `tool_call` / `tool_result` / `retrieval` / `guardrail` events live.
3. **Knowledge-graph viz** — `graph/KnowledgeGraph.tsx`: animates as `retrieval`
   events arrive (their `touched_nodes` / `touched_edges` light up, particles flow).
4. **Eval + token/cost panel** — `metrics/EfficiencyPanel.tsx`: cache-hit rate,
   small-model share, cost per 1k, quality score + this run's `run_finished` usage.
5. **SHAP explanation panel** — `ml/ShapPanel.tsx`: attribution bars + the conformal interval.
6. **Human-in-the-loop approval** — `approval/ApprovalCard.tsx`: renders
   `approval_required`, Approve/Reject → `POST /approval`.

### The signature — the Trust Bar

`layout/TrustBar.tsx` renders the winning sentence as a live pipeline: each clause
lights in its **signal hue** the moment the run reaches it. Colour is the taxonomy
of trust (`config/signals.ts`): reasoning=cyan, retrieval=periwinkle, human
gate=amber, guardrail=rose, healthy/efficiency=emerald, ML=violet — reused
everywhere those subsystems appear.

## Design identity

A committed **dark control-room** theme (projector-legible, high contrast), a
faint instrument grid, mono telemetry read-outs, and the six-hue signal system as
the one bold move. Not the default shadcn look. Tokens live in `src/index.css`
(`@theme`); `prefers-reduced-motion` is respected.

## Swapping the domain (on the day)

Only the **adapter** config changes: `config/personas.ts` (personas + sample
queries) and the mock substrate `mock/fixtures.ts` (the context graph, metrics,
ML explanation). The scenario in `mock/mockTransport.ts` is the demo script. None
of the core views contain domain logic.
