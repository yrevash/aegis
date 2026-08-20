# What is done, and what is left — the other four portals

Written 2026-08-21, from measurement rather than memory. Hand this to an agent.

---

## Done: `platform_admin`, all 12 screens

Dashboard · Analytics · Approvals · Governance · Roles · Forecast · Jobs · Audit ·
Database · MCP · Console · Settings.

Measured before/after, in the browser at 1440×1000, counting elements in `<main>`
whose **own** text exceeds 100 characters:

```
jobs      50 prose blocks → 1      22,959px → 2,232px
settings  70 → 9                    8,388px → 2,615px
database  11 → 1                    2,458px → 1,005px
mcp       11 → 3                    2,756px → 2,222px
total    143 → 15
```

Plus, across the whole app: one component system instead of two (215 call sites),
a navy rail with measured contrast, `PageHeader`/`DataPanel`/`StatCard(trend)` as
shared primitives, 20 analytics boards on real charts, an ER diagram derived from
the live schema, an isometric Jobs pipeline driven by real `job_runs`, and a
console that no longer scrolls (settled turn 3,232px → 900px).

**The console, analytics, jobs, audit, database, approvals, governance, roles and
settings screens are shared** — `tenant_admin` and `client` inherit them already.

---

## Left: 21 screens the platform_admin pass never touched

| portal | sections | shared (done) | unique (left) |
|---|---|---|---|
| `tenant_admin` | 13 | 10 | **3** — documents, llmops, memory |
| `ai_team` | 16 | 3 | **13** — harness, mlops, llmops, evals, tokenopt, memory, rag, graph, cache, voice, vision, guardrails, simulation |
| `devops` | 9 | 3 | **6** — stack, patch, security, redteam, cache, latency |
| `client` | 11 | 6 | **5** — documents, savings, risk, memory, simulation |

Deduplicated, that is **21 distinct screens**.

### The state of each, measured

| screen | file | lines | charts | DataPanel | scene |
|---|---|---|---|---|---|
| redteam | `redteam/RedteamView.tsx` | 728 | — | 7 | 2 |
| guardrails | `guardrail/GuardrailsView.tsx` | 542 | — | — | — |
| harness | `harness/HarnessView.tsx` | 539 | — | — | — |
| cache | `cache/CacheView.tsx` | 524 | — | — | — |
| patch | `devops/PatchCheck.tsx` | 374 | — | 3 | — |
| mlops | `ml/MLOpsView.tsx` | 374 | **2** | — | — |
| vision | `vision/VisionView.tsx` | 357 | — | — | — |
| tokenopt | `gateway/TokenOptView.tsx` | 354 | — | — | — |
| evals | `evals/EvalsView.tsx` | 352 | — | — | — |
| simulation | `sim/SimulationView.tsx` | 351 | — | — | — |
| savings | `client/SavingsView.tsx` | 333 | **1** | — | — |
| risk | `client/RiskMap.tsx` | 332 | — | — | — |
| stack | `devops/StackVersions.tsx` | 323 | — | 4 | — |
| voice | `voice/VoiceView.tsx` | 315 | — | — | 2 |
| documents | `documents/DocumentsView.tsx` | 299 | — | 3 | 3 |
| security | `security/SecurityView.tsx` | 298 | — | 3 | — |
| graph | `graph/GraphView.tsx` | 285 | — | — | — |
| latency | `latency/LatencyView.tsx` | 260 | — | 3 | 2 |
| llmops | `ops/LLMOpsView.tsx` | 217 | — | — | — |
| memory | `memoryctl/MemoryControlView.tsx` | 185 | — | — | — |
| rag | `retrieval/RagView.tsx` | 132 | — | — | — |

**The headline: 19 of 21 screens draw no chart at all.** `PageHeader` is on every
one — an earlier pass landed that — but the visual language stopped there.

### What a partial pass already fixed

A persona lane took the four portals from **170 prose blocks to 92** and should
not be redone:

```
devops/stack     82 → 22    9,673px → 4,593px
client/risk      11 →  2    2,309px → 1,709px
devops/redteam   20 → 18    5,833px → 3,746px
ai_team/tokenopt  3 →  0
```

It also fixed two real 390px overflows (grid children missing `min-w-0`, which
lets content set the track width) and removed ten per-screen `TooltipProvider`
wrappers now that the root one exists.

It explicitly did **not** rework `security` (15 blocks) or `cache` (11) after
inspecting them: their blocks are `Receipt` evidence and `Absence` statements,
which are the sanctioned medium, not prose to relocate. **Verify that judgement
before overturning it.**

### Named by the owner

> *"savings page is really really messy and text bulk"*

`client/SavingsView.tsx`, 333 lines, one chart. Start here.

---

## What "done" means for these screens

The platform_admin pass established the language; this is applying it, not
reinventing it.

1. **A real chart wherever there is a real series.** `recharts` is a dependency.
   **No progress-bar lists** — the owner rejected those twice. A chart has a
   quantitative axis a reader can read values off; a filled track with a number
   printed beside it is not a chart. Where there is one number and no shape, use a
   large `Figure` with its `Receipt`, never a one-bar chart.
2. **`ui/DataPanel` for every wide table.** Its scroll container cannot widen the
   page; a plain `CardBody` lets a 900px table make the *document* scroll
   sideways — invisible at 1440px and the whole experience at 390px.
3. **Prose → structure, nothing deleted.** `InfoTip` for explanation, `Receipt`
   for provenance, `Absence` for a stated gap.
4. **Illustrations where they are true.** 46 recoloured scenes in
   `web/public/illustrations/`; `CREDITS.md` maps each to its surface. **Empty
   states are where they earn their keep** — this platform produces a lot of
   honest emptiness by design, and it currently reads as brokenness. Do not
   decorate a dense operational screen.

## Constraints that are not negotiable

- Light theme only, no `dark:`. Only DESIGN.md §2's blue ramp — `oneRamp.test.mjs`
  fails any other step.
- **Three categorical series is the ceiling.** `#175cd3 ↔ #1570ef` measures ΔE 6.4
  against a floor of 15. Four is correct only for *ordinal* use. A fourth category
  folds into a named `Other (n)`.
- `--blue-600` is a fill/border/ring step, **not** a small-text step — 4.57:1 on
  white and 4.07:1 on the canvas. Small blue text is `--blue-700`.
- **Viewport breakpoints (`sm:`/`lg:`/`xl:`) are wrong inside a narrow column.**
  This has caused four separate defects here. Use container queries.
- A root `TooltipProvider` is mounted in `components/auth/Providers.tsx` — never
  wrap a screen.
- **Never fabricate a number.** `StatCard`'s `trend` takes only a series that
  exists. Status hues always ship with an icon **and** a word.
- Presentation only — no auth, routing, tenant-scoping or business-logic change.

## Verifying

```bash
cd web && npx tsc --noEmit && npx next lint --dir src && npm test \
  && AEGIS_DIST_DIR=.next-verify npx next build
```
Baselines as of 2026-08-21: **231 tests · 67/67 pages**. Then load each screen at
**1440 and 390** and confirm `documentElement.scrollWidth === innerWidth`.

Sign in as `northwind.client`, `vertex.client`, `northwind.admin`, `devops` and
`northwind.analyst` — the un-tenanted `client` and `ai` accounts have no tenant, so
every tenant-scoped screen is correctly empty for them and looks broken.

## Also open, not screen work

- **`POST /v1/mcp/servers/{id}/test` hangs forever** when the peer is this
  deployment. Handshake succeeds, tools are discovered, the HTTP response never
  returns.
- **Memory read/write asymmetry** — cross-tenant *write* is 403, cross-tenant
  *read* is 200-with-empty-list. No row crosses either way, but a security surface
  should not say "there is nothing here" when it means "you may not".
- **Skill → agent assignment** exists server-side (`SkillWriteRequest.agent`,
  `SkillsResponse.agents`) and is **absent from the web client**.
  `web/src/lib/api/skills.ts` has no `agent` field, and `SkillsDrawer.tsx` renders a
  paragraph asserting the opposite — that the API has no such field. It does.
- **Chunk prefixes for documents 8–10 still carry the old title.** The titles
  themselves are fixed; closing this needs a `chunk`→`embed`→`index` re-run, which
  spends embedding budget.
