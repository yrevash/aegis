# Aegis Console — `web/`

The Aegis console: **Next.js 15** (App Router) + **React 19**, styled with the Aegis
**signal-palette** design system. **Light theme only.**

This is *the* console. The original Vite app in `../frontend/` has been retired and
deleted — `web/` fully replaced it.

## Stack

- **Next.js 15** (App Router) + **React 19** + **TypeScript** (strict)
- **Tailwind CSS v4** (CSS-first, `@tailwindcss/postcss`)
- **lucide-react** icons, **Recharts** + **react-force-graph** for charts/graph
- Fonts (**IBM Plex Sans** for interface, **JetBrains Mono** for every figure) via a
  runtime `<link>` (not `next/font`) so `next build` never blocks on a font fetch.
  `--font-mono` is deliberately still JetBrains rather than Plex Mono: the two share an
  identical 0.6000em advance, but Plex Mono's x-height is 6.2% smaller, and the figure
  is the thing this console is read for

## Commands

```bash
npm install
npm run dev     # http://localhost:3000  → /login
npm run build   # production build
npm run lint    # ESLint (next/core-web-vitals + next/typescript)
```

The dev server expects the backend on `http://localhost:8000`:

```bash
NEXT_PUBLIC_API_BASE=http://localhost:8000 NEXT_PUBLIC_HEALTH_PATH=/health npm run dev
```

## Routes

- `/` → redirects to `/login`
- `/login` — real credential login against `POST /auth/login`; the returned JWT
  role decides which portal you land in
- `/app/[role]` → redirects to that role's default section
- `/app/[role]/[section]` — portal shell + section page, guarded by `PortalGuard`

Four role portals, from `ROLE_SECTIONS` in `lib/portal.ts`:

| Portal    | Sections (nav order) |
| --------- | -------------------- |
| `admin`   | Overview · Governance · Approvals · Audit · Roles & Access |
| `ai_team` | Console · Harness · MLOps · LLMOps · Evals · Token opt · Memory · RAG · Graph · Cache · Guardrails · Simulation |
| `devops`  | Overview · Tech Stack & Versions · Patch Check · Security · Red-team · Latency · Audit |
| `client`  | Overview · Savings · Risk Map · Simulation |

Every section renders a real view backed by a live accessor. Panels with no data
say so rather than inventing numbers.

## Auth & RBAC

- `lib/auth/AuthContext.tsx` — session provider. Signs in via `POST /auth/login`,
  persists the session to `localStorage`, and mirrors the JWT into
  `lib/api/authToken.ts` so the REST client and SSE transport can attach
  `Authorization: Bearer <token>` on every live call.
- `components/auth/PortalGuard.tsx` — blocks children until the persisted session
  has hydrated, and bounces a session that reaches the wrong portal.

> **Note for view authors:** `AuthProvider` restores the session in an effect, and
> React runs *child* effects before *parent* effects. A component that fetches on
> mount must therefore read `const { session, hydrated } = useAuth()`, guard with
> `if (!hydrated) return`, and include `[token, hydrated]` in its dependency array.
> Never hardcode a `null` token, and never end a fetch chain in a bare
> `.catch(() => {})` — a swallowed 401 leaves a spinner running forever.

## Live vs. mock

`lib/api/mode.ts` probes the backend on boot (`probeBackend`). Live-first: if the
backend answers, every accessor reads real data. If it doesn't, the app falls back
to the in-browser fixtures in `src/mock/` and renders an explicit
**"Offline demo — mock data"** banner. The offline mode is a deliberate demo
affordance, and it is always labelled — the UI never passes fixtures off as live.

Force it either way with `NEXT_PUBLIC_USE_MOCK=true|false` or `?mock=1`.

## Layout

```
src/
  app/
    layout.tsx                     root layout (fonts + globals)
    globals.css                    design tokens (light only)
    page.tsx                       → /login
    login/page.tsx                 real login
    app/[role]/layout.tsx          portal shell (Sidebar + Topbar)
    app/[role]/[section]/page.tsx  section page
  components/
    layout/{Sidebar,Topbar}.tsx
    auth/PortalGuard.tsx
    dashboard/AdminCommandCenter.tsx
    <section>/…View.tsx            one view per portal section
    charts/, ui/, primitives/      chart + design-system primitives
  lib/
    portal.ts                      ROLE_SECTIONS + SECTIONS catalogue
    auth/AuthContext.tsx           session + JWT
    stream.ts / streamNames.ts     StreamEvent contract; mirrors
                                   aegis.core.stream_names.ALL (17, exact match)
    api/                           typed REST client, SSE decoder, live/mock mode
  mock/                            offline-demo fixtures
```

## Honest caveats

- **Graph is empty without Neo4j.** `/graph` degrades to `{nodes: [], edges: []}`
  when Neo4j isn't running; the view renders that honestly instead of faking nodes.
- **Most surfaces are empty on a fresh database.** Cost, savings, approvals and
  usage figures only populate after real agent runs.
- **No test suite in `web/` yet.** Type safety is enforced by `tsc --noEmit` and
  `next lint`; there are no component tests.
