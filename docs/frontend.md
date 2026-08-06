# frontend.md — Frontend Context

> The frontend is where three rubric axes are won or lost live: **innovation (show edge), solution quality (clarity), and the jury's eyes.** Build it to be watched. Read `hackathon.md` first for the rubric and the money-shot.

---

## 1. Stack (finalized)

- **Vite + React + TypeScript** — fast dev, typed, projector-ready web app (NOT React Native; the demo runs on a laptop/projector).
- **Tailwind CSS + shadcn/ui** — copy-owned components, no runtime overhead, full control of styling.
- **Recharts (Tremor-style API)** — analytics dashboards and charts. We use Recharts directly with a thin Tremor-style component wrapper; the Tremor library itself is not a dependency.
- **Graph visualization** — `react-force-graph` (2D) is the default for the live, animated knowledge graph. Alternatives if it fights you: `react-flow` or Neo4j's NVL. **AGENT: research current versions/APIs of the chosen graph lib before wiring — these libraries change.**
- **Streaming** — **hand-rolled SSE** via the browser `EventSource` API (or a `fetch` reader). No CopilotKit, no AG-UI, no Vercel AI SDK — we own the stream for control and simplicity.

**Do NOT add:** CopilotKit, AG-UI, Vercel AI SDK, or any component library beyond shadcn/Recharts (Tremor-style API) without asking. More libraries = integration cost + maintainability points lost.

---

## 2. Design principles

- **Distinct visual identity.** AI tools default to identical shadcn layouts. Spend real effort on a custom color system + typography so the app does not read as "AI-generated default." This is a cheap, high-return edge.
- **Clarity over flash.** The jury must understand every screen in seconds. Legible at projector distance. Enterprise-grade restraint, not a toy.
- **Show the work.** The frontend's job is to make the backend's intelligence *visible* — streaming reasoning, animating graphs, live metrics. A spinner is a wasted opportunity.

---

## 3. Architecture: two role-scoped surfaces

- **Admin portal (`/admin`)** and **User portal (`/app`)** are **separate URLs with separate, role-scoped views.** This is both a UX and a **security** requirement (RBAC — see `security.md`).
- Routing is driven by the authenticated role returned from the backend. Admin sees system controls, audit log, eval/observability dashboards, agent configuration. User/client sees their own scoped data and interactions.
- **Personas must be detailed**, especially for client-facing dashboards. Each persona drives what data and actions its dashboard exposes. Keep persona definitions in a clearly-named config so they're part of the swappable domain adapter (see `hackathon.md` §5).

---

## 4. Key views (build these components)

1. **Live dashboard** — the enterprise home surface; KPI tiles, charts (Recharts, Tremor-style API), status.
2. **Streaming agent-trace panel** — the show edge. Stream the agent's *intermediate steps*, not just the final answer: "searching knowledge base… retrieved N articles… checking rules… drafting… awaiting approval." Render each step as it arrives over SSE.
3. **Knowledge-graph viz** — the Neo4j graph, **animating as entities are traversed** during retrieval. This is the visual centerpiece.
4. **Eval + token/cost panel** — live tiles: cache-hit rate, small-model share, cost per 1000 queries, and a quality score — a **grounding proxy** (the fraction of completed runs that touched the knowledge graph before answering), not an eval-harness/LLM-judge score. Tokens are visible to the jury, so make efficiency a *number on screen*.
5. **SHAP explanation panel** — for a selected ML prediction, show the feature attribution + the conformal uncertainty interval. Enterprise trust made visual.
6. **Human-in-the-loop approval UI** — when the agent pauses on a high-risk / high-uncertainty action, present the proposed action + context + an Approve/Reject control. Central to the "bounded autonomy" story.

---

## 5. Streaming (the show edge) — how to do it right

- Backend exposes **SSE endpoints** (FastAPI). Frontend consumes via `EventSource`/`fetch` stream and appends to React state.
- **Stream the agent trajectory, not just tokens.** The backend emits structured step events (node started, tool called, retrieval done, awaiting approval, token/answer chunk). The frontend renders these live so the jury watches the agent *think and act*.
- Cached responses appear near-instant (fast path); streamed misses feel alive. Pair the graph animation with the trace panel for the money-shot.
- **Security note:** streaming raw output means the output guardrail can't scan before the user sees it. Coordinate with backend: either buffer briefly for the output guard, or scan post-hoc and redact. Name this tradeoff in the demo — it signals security maturity.

---

## 6. Backend interface contract (agree before building; mock against it)

- **Auth:** login → returns role + token; all requests role-scoped.
- **Query endpoint:** accepts a user query; returns an **SSE stream** of step events + answer chunks.
- **Graph endpoint:** returns nodes/edges for the current context (for the viz), ideally updated as retrieval runs.
- **ML explain endpoint:** returns SHAP attribution + conformal interval for a prediction.
- **Metrics endpoint:** returns live token/cost/cache-hit/eval numbers for the dashboard.
- **Approval endpoint:** submit approve/reject for a paused agent action.

**AGENT: confirm the exact event schema with the backend module before implementing the stream renderer. Do not guess the shape.** See `backend.md` for the API surface.

---

## 7. Quality bar

- TypeScript strict mode; typed props and API responses.
- Component structure: small, single-responsibility components; no god-components.
- Responsive and legible on a projector (test at low resolution / large font).
- Accessible components (shadcn gives this by default — don't break it).
- The repo is machine-graded: clear folder structure, named components, a README section for the frontend.

---

## 8. Agent directives (behavior for this codebase)

- **Research before wiring** any library whose API you're unsure of (graph viz, Recharts chart props, SSE patterns) — verify current usage, don't rely on memory.
- **Ask, don't guess**, on: the SSE event schema, persona/role definitions, and any visual-identity choices with tradeoffs.
- **Do not over-build.** Build the six key views to a demoable state first; polish after the Day-1 slice works.
- Document components as you go; keep the frontend README current.
