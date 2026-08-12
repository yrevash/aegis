# Aegis Console — `web/` (Next.js rebuild)

The Next.js (App Router) rebuild of the Aegis console, on the **TailAdmin** design
base restyled to the Aegis **signal-palette** design system. **Light theme only.**

This lives alongside the existing Vite app in `../frontend/` (which stays live);
`web/` is the new surface under active construction.

## Stack

- **Next.js 15** (App Router) + **React 19** + **TypeScript** (strict)
- **Tailwind CSS v4** (CSS-first, `@tailwindcss/postcss`) — matches the templates
- **lucide-react** icons
- Fonts (Inter / Space Grotesk / JetBrains Mono) via a runtime `<link>` (not
  `next/font`) so `next build` never blocks on a font fetch

## Commands

```bash
npm install
npm run dev     # http://localhost:3000  → /login
npm run build   # production build (prerenders every portal/section route)
npm run lint    # ESLint (next/core-web-vitals + next/typescript)
```

## Routes

- `/` → redirects to `/login`
- `/login` — role-select stub (auth is wired later)
- `/app/[role]` → redirects to the role's default section
- `/app/[role]/[section]` — the portal shell + section page

Four role portals mirror `ROLE_SECTIONS` from the Vite `Portal.tsx`:

| Portal    | Sections (nav order)                                   |
| --------- | ------------------------------------------------------ |
| `admin`   | Overview · Approvals · Governance · Audit · Roles      |
| `ai_team` | Console · MLOps · LLMOps · Memory · Access demo        |
| `devops`  | Overview · Tech Stack · Patch Check · Audit            |
| `client`  | Overview · Savings · Risk Map · Access demo            |

Every section is a titled placeholder page for now, except **Console** (ai_team),
which renders the query bar + a "live run will render here" canvas. Real SSE
wiring lands in the next task.

## Layout

```
src/
  app/
    layout.tsx                     root layout (fonts + globals)
    globals.css                    ported design tokens (light only)
    page.tsx                       → /login
    login/page.tsx                 role-select stub
    app/[role]/layout.tsx          portal shell (Sidebar + Topbar)
    app/[role]/page.tsx            → default section
    app/[role]/[section]/page.tsx  section page (+ generateStaticParams)
  components/
    layout/{Sidebar,Topbar}.tsx    TailAdmin shell → our tokens
    ui/{Card,Badge,StatCard,Table,Chart}.tsx
    console/ConsolePlaceholder.tsx
    portal/SectionPlaceholder.tsx
  lib/
    portal.ts                      ROLE_SECTIONS + SECTIONS catalogue
    stream.ts                      StreamEvent contract (ported verbatim)
    streamNames.ts                 mirrors aegis.core.stream_names.ALL (17)
    api/{config,client,sse,types}.ts   typed REST client + SSE decoder
```

## Ported from the Vite app / backend

- **Design tokens** (`globals.css`) — the signal palette (agent/graph/risk/block/
  ml/ok fill+ink), fonts, radius/shadow, `.eyebrow`/`.tabular`, type scale.
  Dark theme intentionally dropped (light only).
- **Stream contract** (`lib/stream.ts`) — copied verbatim from
  `frontend/src/types/stream.ts`.
- **Stream names** (`lib/streamNames.ts`) — mirrors **every** entry of
  `aegis/src/aegis/core/stream_names.py::ALL` (17, exact set match).
- **SSE decoder** (`lib/api/sse.ts`) — `readSSEStream` + `decodeAguiStream`.
- **API client** (`lib/api/client.ts`) — every endpoint from
  `frontend/src/api/client.ts` (`/query` SSE, `/graph`, `/metrics`, `/ml/explain`,
  approvals, admin, memory, ops, stack, risk/savings). Live-only for now.

## Not yet wired (honest caveats)

- Auth (role/JWT) — login is a stub that routes straight into each portal.
- Mock/live mode toggle (the Vite `api/mode.ts` probe) — client is live-only.
- All section pages except Console are placeholders; Console is a static canvas.
- `lib/api/types.ts` response shapes are lean scaffold mirrors, not yet the full
  field-by-field fidelity of the Vite `types/api.ts`.
