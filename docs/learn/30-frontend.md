# 30 · The console (frontend)

The frontend is a **React + Vite + TypeScript** app under `frontend/src/`, styled with
**Tailwind CSS v4** (CSS-first). It is a *live-first* console: it talks to the real
backend when reachable and falls back to a clearly-labelled in-browser mock otherwise.
Charts use `recharts`; the knowledge graph uses `react-force-graph`.

## Surfaces and navigation

There are only **three real router routes** (`App.tsx` → `AppRoutes`):

| Route | Renders |
|---|---|
| `/login` | `routes/LoginPage.tsx` |
| `/app` | `RequireRole role="user"` → `Portal role="user"` |
| `/admin` | `RequireRole role="admin"` → `Portal role="admin"` |
| `/` and `*` | `RootRedirect` → `homePathFor(session.role)` or `/login` |

Everything else is a **tab inside `routes/Portal.tsx`**, switched by local
`useState(active)` — *not* the router. `Portal` builds its tabs from `sectionsFor(role)`;
each `Section` carries a `NavItem` (id, label, icon, mono `hint`) and a `render`. The list
is handed to `AppShell` → `Sidebar`, which groups items by `NavItem.group`.

```mermaid
flowchart TB
    App[App.tsx] --> Providers["BackendModeProvider → AuthProvider → TooltipProvider → BrowserRouter"]
    Providers --> Routes[AppRoutes]
    Routes --> Login[/login → LoginPage/]
    Routes --> Portal["/app · /admin → Portal (RequireRole)"]
    Portal --> Shell[AppShell = Sidebar + Topbar + main]
    Shell --> Tabs{"active tab (useState)"}
    Tabs --> Console & Overview & Memory & AccessDemo & Improvement & Approvals & Governance & Audit
```

The tabs (note: several **labels differ from their id** — documented so the code and UI
line up):

| id | UI label | mono hint | Component | Who sees it |
|---|---|---|---|---|
| `console` | **Console** | `LangGraph` | `components/console/MoneyShotConsole.tsx` (eager) | all |
| `dashboard` | **Overview** | `value at a glance` | `components/dashboard/Dashboard.tsx` (lazy) | all |
| `memory` | **Memory** | `pgvector` | `components/memory/MemoryView.tsx` (lazy) | all |
| `simulation` | **Access demo** | `RBAC scope` | `components/sim/SimulationView.tsx` (lazy) | all |
| `ops` | **Improvement** | `trace → eval → release` | `components/ops/OpsView.tsx` (lazy) | admin |
| `approvals` | **Approvals** | `human gate` | `components/approvals/ApprovalsInbox.tsx` (lazy) | admin |
| `admin` | **Governance** | `tenants · budgets` | `components/admin/AdminSettings.tsx` (lazy) | admin |
| `audit` | **Audit** | `OpenTelemetry` | `components/admin/AuditLog.tsx` (lazy) | admin |

Every non-console surface is code-split (`React.lazy` + a `<Suspense key={active}>` that
cross-fades on tab change via `.animate-section`). Sub-panels behind the composite
surfaces:

- **Console** (`MoneyShotConsole`): the glass-box — reasoning lane, orchestration map,
  knowledge graph, rerank scoreboard, conformal + SHAP panels, guardrail reveal, approval
  spotlight, answer panel (assembled from `components/console/*`, `graph/*`, `ml/*`,
  `retrieval/*`, `guardrail/*`).
- **Memory** (`MemoryView`): `SemanticFactsPanel`, `StructuredProfilePanel`,
  `EpisodicSessionsPanel`, `WriteLogPanel`, `RecallDebugPanel`.
- **Improvement / Ops** (`OpsView`): `DiagnosePanel`, `EvalTrend`, `PendingReleases`,
  `PromptDiff`, `PromptTimeline`.
- **Governance / Admin** (`AdminSettings`): `TenantsView`, `UsersView`, `BudgetsView`,
  `UsageView`.

Bonus: **projector / present mode** — pressing `F` in `Portal` toggles `presenting`
(`Esc` exits); `AppShell` then strips the chrome and enlarges the console.

## State management

No Redux/Zustand — just **React Context + hooks + one pure reducer**.

- **`auth/AuthContext.tsx`** provides `{ session, signIn, signOut }`. `Session =
  { role, token, username, tenantId }`, persisted to `localStorage` under
  `aegis.session` and rehydrated on load (a refresh keeps you logged in). `signIn` calls
  `apiLogin` from `api/client.ts`.
- **`state/backendMode.tsx`** (`useBackendMode()`) runs the live/mock boot probe once and
  exposes `{ mode, reason, ready }` (see next section).

The **console's SSE stream → UI state** pipeline is the important bit:

```mermaid
flowchart LR
    MSC[MoneyShotConsole] --> URS[useRunStream]
    URS -->|"start(query, persona, token)"| T[createTransport per run]
    T -->|onEvent / onError / onClose| URS
    URS -->|"dispatch {kind:'event'}"| RR[runReducer<br/>pure StreamEvent reducer]
    RR --> STATE[RunState<br/>events · phase · usage · ml · mlGate · retrievalScores<br/>candidates · provenance · guardrails · toolCalls · approval]
    STATE --> MSC
```

- `useRunStream` (`state/useRunStream.ts`) wraps `useReducer`. On `start` it creates the
  transport **per run** (so it reads the resolved live/mock mode at run time), then feeds
  each SSE event into the **pure** `runReducer` (`state/runReducer.ts`), which is
  deterministic and unit-tested (`runReducer.test.ts`).
- `runReducer` derives a `RunPhase` (`idle · streaming · awaiting_approval · abstained ·
  completed · blocked · error`) plus the structured views the console renders. This is a
  direct projection of the `StreamEvent` stream described in `40-request-flow.md`.
- `resolveApproval(decision)` calls back into the run controller with the captured
  `approval_id`, resolving a live HITL gate mid-stream.
- The console separately fetches the accumulated graph via `getGraph(token)` and
  efficiency figures via `useMetrics(token)` (`state/useMetrics.ts`).

## The API client and mock mode

The app is **live-first with a labelled mock fallback**. The decision lives in
`api/config.ts` + `api/mode.ts`:

- **Config** (`api/config.ts`) reads Vite env: `VITE_API_BASE` (base URL),
  `VITE_HEALTH_PATH` (default `/health`), and `FORCE_MOCK = VITE_USE_MOCK === 'true' ||
  ?mock=1`.
- **Mode resolution** (`api/mode.ts`): `decideMode(forceMock, reachable)` →
  `forced-mock` (env), `probe-failed` (unreachable), or `probe-live`.
  `probeBackend()` does a `GET {API_BASE}{HEALTH_PATH}` with a 2.5s `AbortController`
  timeout; any failure → mock. `BackendModeProvider` runs it once on mount; `factory.ts`
  and `client.ts` read the cached result synchronously.
- **Transport selection** (`api/factory.ts`): `createTransport()` returns
  `isMock() ? createMockTransport() : createLiveTransport()`. Both satisfy `RunTransport`
  (`api/transport.ts`): `start(query, persona, token, handlers) → RunController`.
- **REST client** (`api/client.ts`): every function checks `isMock()` first and returns
  an in-browser fixture when mocking, else calls the real route through a private
  `request<T>(path, init, token)` helper (adds `Authorization: Bearer <token>`, throws on
  non-ok).
- **Offline banner** (`components/layout/OfflineBanner.tsx`): renders only when
  `mode === 'mock'`, above every route. It distinguishes forced mock ("Set
  `VITE_USE_MOCK=false`…") from an unreachable backend ("Backend unreachable — showing
  scripted demo data").

> The switch is driven by **env vars + a boot health probe**, not localStorage.
> localStorage only stores the auth session (`aegis.session`).

**SSE detail** (`api/sse.ts`): `/query` is streamed with a hand-rolled `fetch` reader (not
`EventSource`, because the stream needs a POST body). `readSSEStream` splits frames on
blank lines, `JSON.parse`s each `data:` line into a `StreamEvent`, and skips malformed
frames rather than tearing down the stream.

### Endpoints the client calls (all real backend routes)

| Function | Method + path |
|---|---|
| `login` | `POST /auth/login` |
| streaming run | `POST /query` (SSE, body `{query, persona}`) |
| `getGraph` / `getMetrics` / `getAudit` | `GET /graph` · `GET /metrics` · `GET /audit` |
| `mlExplain` | `POST /ml/explain` |
| `postApproval` / `getApprovals` / `postApprovalDecision` | `POST /approval` · `GET /approvals` · `POST /approvals/{id}/decision` |
| admin | `GET /admin/tenants` · `GET /admin/users` · `GET|POST /admin/budgets` · `GET /admin/usage` |
| memory | `GET /memory/{facts,profile,sessions,sessions/{id}/messages,writes,recall_debug}` |
| ops | `GET /ops/{prompts,prompts/active,evals,releases/pending}` · `POST /ops/{diagnose,release,rollback,releases/{id}/decide}` |
| health probe | `GET /health` |

These map one-to-one to the endpoints in `backend/src/app/api/routes.py` (see
`20-backend.md` §API). Note `POST /approval` is live-only (the mock resolves approvals via
the mock transport's controller instead).

## The design system (`index.css`)

Tailwind v4, CSS-first: tokens are CSS custom properties on `:root` (light) and `.dark`,
re-exported as utilities via `@theme inline`. **Theming is light-first, dark opt-in via a
`.dark` class** (`@custom-variant dark`) — there is *no* `data-theme` attribute.

- **Radius:** `--radius: 0.75rem` (+ derived `--radius-sm/md/lg/xl`).
- **Motion:** `--dur-fast` (120ms), `--dur-base` (200ms), `--dur-slow` (320ms),
  `--dur-count` (900ms), `--ease-out`, `--ease-inout`.
- **Neutrals:** `--background`, `--foreground`, `--surface`, `--surface-2`, `--card`,
  `--muted`, `--muted-foreground`, `--border`, `--input`, `--ring` (+ `-foreground` pairs).
- **Signal palette ("taxonomy of trust")** — each has a soft *fill*, a readable *ink*, and
  an on-fill *foreground*: `--agent` (mint), `--graph` (blue), `--risk` (amber),
  `--block` (rose), `--ok` (green), `--ml` (purple). e.g. classes `text-ml-ink`,
  `bg-block`, `text-agent-ink`. This palette is why each subsystem in the console has a
  consistent hue.
- **Charts:** `--chart-1 … --chart-5`. **Semantic:** `--success`, `--danger`, tints.
  **shadcn aliases:** `--primary`, `--secondary`, `--accent`, `--destructive`.
- **Typography:** `--font-sans` (Inter), `--font-display` (Space Grotesk), `--font-mono`
  (JetBrains Mono); type-scale utilities `.t-hero`, `.t-metric`, `.t-title`, `.t-body`,
  `.t-label`, `.t-mono`.
- **Elevation:** `.shadow-card`, `.shadow-hover`, `.shadow-pop`.
- **Motion utilities** (all disabled under `prefers-reduced-motion`): `.animate-beat`
  (the active-subsystem pulse), `.animate-trace-in`, `.animate-flow-pulse`,
  `.animate-reveal`, `.animate-section` (tab cross-fade), `.animate-chart-in`, etc.

## Verify the frontend

From `frontend/` (see `60-run-and-operate.md`):

```bash
pnpm build   # tsc (strict) + vite build — clean, chunks < 500 kB
pnpm lint    # oxlint — 0 errors
pnpm test    # vitest (runReducer, mode, roi, sla, orchestration, … unit tests)
```
