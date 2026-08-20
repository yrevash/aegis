# Aegis frontend redesign — platform_admin first

## Context

Aegis's backend is finished and audited (phases 1–10, `backend 1170 / aegis 2266 / web 156`,
all pushed). The frontend is not. Tested on a phone against the live deployment, the verdict was
"text hell… no soul… 80-year-old UI", and it is correct.

**Why it turned out this way.** I optimised the frontend for *honesty* — every figure sourced,
every absence stated, every mechanism explained — and treated that as sufficient. It is not. A
jury looks for ten seconds and buys what it sees. Measured across the 12 platform_admin screens:

| Screen | prose blocks | charts | tables |
|---|---|---|---|
| MCP | ~24 | 0 | 5 |
| Jobs | ~30 | **0** | ~6 |
| Settings | ~18 | 0 | 2 |
| Forecast | ~16 | 2 | 1 |
| Database | ~10 | 0 | 1 |
| Governance | 2 | **0** | 3 |

Three structural causes, none of which a per-screen polish pass can fix:

1. **Two competing component systems in concurrent use.** `primitives/card.tsx` (`rounded-xl`,
   `primitives/badge.tsx`) and `ui/Card.tsx` (`rounded-lg`, `ui/Badge.tsx`). Half the screens use
   each. Guaranteed incoherence.
2. **The sidebar is white** — `bg-surface: #ffffff` against a `#f9f9fa` canvas, 1px border. The
   rail is *lighter* than the page, so the app has no spatial anchor and reads flat.
3. **Prose became the fallback for every uncertainty.** Where a figure could not be sourced I
   wrote a paragraph. Right instinct, wrong medium — honesty should survive as a marker on a
   chart, not three sentences replacing it.

Plus: `.t-hero` (`clamp(2.25rem → 3.5rem)`) is used on exactly one screen (Settings), which is
the oversized heading seen in the screenshots.

**Scope: platform_admin only, all 12 sections, end to end.** Prove the language on one portal,
then replicate. The other four portals reuse the same shell, primitives and screens, so most of
the work carries over for free.

---

## Step 0 — the plan document

Create `docs/dev_new_docs_v2/frontend-redesign/` containing:
- `00-DESIGN-DIRECTION.md` — the visual system (below), with the palette, type scale, depth,
  motion and the component inventory it replaces.
- `01-SCREENS.md` — the 12 platform_admin screens, each with its current defect list and its
  target composition.
- `02-SEQUENCING.md` — waves, owners, gates.

`DESIGN.md` at the repo root is rewritten to match — it is the file every agent reads as the
authority, and its current austerity ("borders not shadows, density 5, motion 2") is what
produced the flat result.

---

## The visual direction — soft depth, not flat

**`DESIGN.md` as written is the problem.** "Borders not shadows · radius 6px · no gradients ·
motion 2" is a correct system for a dense internal tool and it is what produced the flat,
wireframe result. The user's reference images say otherwise, and they are the brief.

**What the references actually share** (a soft-3D dashboard, a minimal product page, a blue/white
UI kit, two 3D-illustration landing pages):
- Soft white cards **floating** on a light blue-grey canvas — elevation, not hairlines
- Generous rounding (12–16px), not 6px
- One dominant blue, with a **gradient** on primary actions only
- Large, clear numerals as the focal point of a tile
- Radial gauges and sparklines doing the work text does today
- 3D that is **simple geometry** — cubes, a torus, matte rounded solids. No characters, no clutter

### Tokens

```
--rail:        #0b1f3f   deep navy — the sidebar, the one dark surface
--rail-hover:  #12305e
--rail-active: #1570ef
--rail-text:   #a8c0e0

--background:  #eef2f8   light blue-grey canvas (was #f9f9fa, near-white → no separation)
--surface:     #ffffff   cards, now genuinely floating against it
--radius:      0.875rem  14px (was 6px)

--grad-primary: linear-gradient(135deg, #2b7fff 0%, #1570ef 100%)   primary actions only
```

`shadow-card` / `shadow-hover` / `shadow-pop` already exist in `globals.css` and are **entirely
unused**. They get used. Borders stay only inside tables, where they separate rows.

The blue ramp (`--blue-50 … --blue-900`) and the reserved status set (`--risk`, `--block`, `--ok`)
are unchanged — validated, and `tests/design/oneRamp.test.mjs` enforces them. This adds depth and
a canvas tint; it does not add hues.

**Type.** `.t-hero` retired from product screens. Page titles 28px, one display size per screen.
Numerals large and confident via `Figure size="display"` — the references make the number the hero.

**Motion budget 2 → 4.** Card entrance stagger, real streaming feel on the console, spring on the
flow graph. `prefers-reduced-motion` respected throughout.

### Libraries to add (`web/package.json`)

- `@xyflow/react` — the live agent flow. ~80% of the logic exists already in
  `web/src/components/console/orchestration.ts` (551 lines: layered DAG layout, live status
  resolution, traversed edges, back-edge handling) — ported, not rewritten.
- `react-force-graph-3d` — knowledge graph in 3D. `react-force-graph-2d` is already a dependency;
  a sibling swap on `web/src/components/graph/KnowledgeGraph.tsx`.
- `motion` (Framer Motion) — there is no animation library today; all motion is hand-written CSS.
- `three` + `@react-three/fiber` + `@react-three/drei` — the 3D accents below.

### Where 3D goes — all four, kept restrained

The user's instruction: *"not too gimmicky, extreme simple 3d not extreme."* So: **matte rounded
solids, one soft light, slow drift, no characters, no scene clutter.** Every instance must be
reduced-motion-aware and must never be the only carrier of information.

1. **Landing hero** — one cinematic moment, the highest-value spend.
2. **Live agent flow (React Flow)** — 2D, but the marquee visual: the real 17-node graph animating
   as a run executes, sub-agent lanes spawning on fan-out.
3. **Knowledge graph in 3D** — entities/relations in rotatable space, lighting up as retrieval
   touches them. Real data, one library swap.
4. **Jobs, lightly 3D** — isometric stage blocks along the six-stage ingest pipeline, in the
   reference's cube language: a job advancing lights the next block. **The queue stays a normal
   table beneath it** — the 3D is the header visual, never the way you read a job's state.

---

## Step 1 — collapse the two component systems

**This blocks everything else.** Pick `ui/` as the survivor (it is the newer, denser set:
`Card`/`CardHeader`/`CardBody`, `Badge` reading `SIGNALS`, `StatCard`, `Table`), migrate every
`primitives/card.tsx` and `primitives/badge.tsx` call site, and delete the losers.

Keep from `primitives/`: `Figure`, `Receipt`/`Absence`, `SectionHeader`, `States`, `InfoTip`,
`Gauge`, `input`, `button`, the Radix wrappers. Those are unique and good.

Then add the missing primitives the redesign needs:
- `PageHeader` — one composition for all 12 screens (eyebrow, 28px title, actions), replacing the
  three different header treatments in use.
- `MetricTile` — the stat tile with a real sparkline (`shared/MiniTrend.tsx` already exists and is
  dependency-free).
- `DataPanel` — card + optional toolbar + scroll container, so every table stops re-inventing it.

---

## Step 2 — demo data with a kill switch

**The root cause of every empty screen.** `backend/src/app/seed.py` populates identity and
governance only — users, tenants, budgets, documents, approvals. It writes **nothing** to
`usage_ledger`, `runs`, `job_runs`, `redteam_runs`, `audit_log`, or chat. That is five of the six
tables the analytics views read, plus the one table the forecast reads. Hence "2 of 71
observations" — arithmetic over an empty table (`3×14 + max(29,15,28) = 71`).

New `backend/src/app/demo.py`, invoked `python -m app.demo`:
- ~90 days of `usage_ledger` rows with weekday/weekend rhythm, a realistic model mix, and two
  deliberate spikes so the forecast band has a story.
- `runs`, `job_runs`, `audit_log`, `approvals` history and a couple of `redteam_runs`.
- Reuses `backend/src/app/adapter/generator.py::generate_synthetic_sync()`, which already produces
  ~1400 realistic in-memory events across hundreds of days and currently persists none of them.
- Gated on `AEGIS_DEMO_DATA=1`; every row tagged so `python -m app.demo --wipe` removes exactly
  the demo rows and nothing else. **Wiped before the hackathon; the blind problem's data lands on
  a clean platform.**

---

## Step 3 — provision Superset (user's decision)

`board_data()` is `return await self._live_client().board_data(...)` — there is no Superset-free
path, and the user chose to **provision Superset here** rather than build a native one. So:

1. Superset 6.1.0 is already installed and running locally on `:8088` from earlier in this
   project (`uv`, no Docker, admin/admin).
2. `python -m aegis.analytics --role aegis_superset --password '…' | psql` — creates the six
   `analytics_*` views and the read-only role. It prints SQL and never executes; nothing runs at
   boot.
3. Register a Superset dataset per view; paste each numeric id into `datasourceId` in
   `docs/operations/superset/aegis-boards.json` (every board currently carries the placeholder
   `0`, and `embeddedUuid` is an un-pasted placeholder too).
4. Set `AEGIS_SUPERSET_ENABLED=true`, `_BASE_URL`, `_USERNAME`, `_PASSWORD`, `_BOARDS` in
   `backend/.env` and restart — the service is `@lru_cache`d.
5. The charts need **rows**, which is Step 2.

**Risk to state plainly:** this makes analytics depend on a Superset process being up on demo
day, on a 6.1.0 build that has already shipped three broken paths in this project. Mitigation —
the screen already degrades honestly to a "turned off" card rather than erroring, and the
`AEGIS_SUPERSET_EMBED_ENABLED` gate is separate, so charts work even if the embed does not. If it
proves fragile in rehearsal, the native path stays available as a fallback and is roughly a day's
work.

---

## Step 4 — the console, rebuilt

This is the screen a jury watches longest and the shape is wrong. Today `ChatConsole.tsx` renders
a 3-column grid with a session rail, and each turn stacks `TrustBar → StreamBanners → AgentPanel
→ ReasoningLane → ActivityRail → ResultTabs` above a sticky composer.

**Target shape** — Claude/Grok-like:
- Idle: centred Aegis wordmark, one large question field, mode chips beneath it (depth, fan-out,
  persona, model — the wire *already* carries `depth_mode` and `requested_fanout`; the composer
  deliberately does not expose them today).
- On send: the question rises, and the answer streams beneath it.
- **Live agent lanes.** `agent_status` events already carry `agent_id`, `role`, `label`,
  `status: queued|started|thinking|acting|done|failed|timeout` and `detail`, and
  `web/src/components/console/agentLanes.ts` (494 lines, pure, tested) already derives them. Show
  each lane as a live card with its reasoning streaming inside it, so parallel fan-out is
  *visible*, which is Aegis's most impressive behaviour and is currently invisible.
- **Two tabs in the main view: `Run` and `Flow`.**

**The Flow tab — React Flow.** `GET /agent/topology` serves 17 nodes / 23 edges with
entry/terminal/conditional flags, and `web/src/config/graphTopology.json` holds a verified
snapshot. `orchestration.ts` already computes layered positions, per-node live status, visit
counts, traversed edges and ghosting for the road not taken. Port that into React Flow nodes and
animate edges as the run executes. **Also fix `NODE_PRESENTATION`, which has no entries for
`plan_team`, `run_team` or `synthesize`** — the fan-out nodes, i.e. the multi-agent story is
structurally absent from the one visual that exists.

**Fix `describeEvent.tsx` first.** It has no case for `reflection`, `routing`, `agent_status`,
`synthesis` or `memory` — 5 of the 20 stream event types render as a grey dot labelled "Event",
and they are exactly the ones that say what the agents are doing.

---

## Step 5 — the 12 screens

Each gets: `PageHeader`, prose relocated into `InfoTip`s or removed, a real visual where one is
warranted, and the honesty preserved as compact markers rather than paragraphs.

| Screen | The work |
|---|---|
| **Overview** | Bento layout. Replace the three `RankedBars` with real charts (donut for model mix, stacked area for spend-by-destination). Sparklines on the 7 tiles. Fix the ragged 7-in-4-column grid. |
| **Analytics** | Provisioned Superset (step 3) + a chart grid in the reference's card language. |
| **Forecast** | Halve the prose. The `NotRecordedPanel` becomes a collapsed footnote. Charts lead. Demo data makes the band real. |
| **Console** | Step 4. |
| **Approvals** | Card list → a decision queue with a strong risk visual; consent sentence stays (it is load-bearing) but tightened. |
| **Governance** | **Zero charts today.** Add spend-vs-cap gauges per tenant; keep the tables beneath. |
| **Roles & Access** | Merge three stacked panels into one coherent screen; forms into a drawer rather than always-on. |
| **Jobs** | The worst offender (~35 panels). Split: an isometric six-stage pipeline header (light 3D, cube language) + a compact queue table beneath; `IngestLog`'s 21 prose blocks collapse into a stage timeline. The 3D is the header, never how you read a job's state. |
| **Audit** | Closest to right already. Bigger activity chart, keep the InfoTip pattern. |
| **Database** | Enable it — `AEGIS_DB_CONSOLE_ENABLED=1`, `AEGIS_DB_CONSOLE_DSN`, and provision the read-only role from the existing `scripts/sql/aegis-readonly-role.sql`. Then: schema browser, prose into InfoTips. |
| **MCP** | Highest prose count. Per-row consequence sentences → one InfoTip per column; peer cards with status. |
| **Settings** | Kill `.t-hero`. Group into a two-column layout with a category rail. |

---

## Verification

1. `cd web && npx tsc --noEmit && npx next lint --dir src && npx next build && npm test`
   — baselines: 156 tests, 65/65 pages, tsc clean, lint clean.
2. **Screenshot sweep** — the harness is already built at
   `…/scratchpad/shots/shoot.mjs`: signs in per role, captures every section at 390/834/1440/1920,
   and independently asserts **no horizontal body overflow** and **no console errors** per screen,
   writing `problems.json`. This is what catches a `hidden lg:flex`-class defect without a human.
3. **`/web-interface-guidelines`** run by me (it is a user-level command subagents cannot invoke —
   all four previous lanes applied its rule list by hand instead).
4. Backend suites unchanged — this is presentation only; no API, auth, routing, state or
   business-logic change.
5. ngrok link + the screenshots to the user.

---

## Sequencing

**Wave 1 (blocking, sequential):** design direction docs → collapse the two component systems →
new shell (deep blue rail, `PageHeader`) → demo data seeder.

**Wave 2 (parallel, non-colliding):** console rebuild + React Flow · Overview/Analytics/Forecast ·
Governance/Roles/Approvals · Jobs/Audit/Database/MCP/Settings.

**Wave 3:** screenshot sweep → fix findings → `/web-interface-guidelines` → ngrok.

**Then:** replicate to `tenant_admin`, `ai_team`, `devops`, `client` — mostly free, since they
share the shell, primitives and most screens.

**Decisions taken by the user, 2026-08-20:**
- **Analytics:** provision Superset here — not a native path. Risk stated in Step 3.
- **3D:** all four — React Flow agent graph, 3D knowledge graph, landing hero, and Jobs — with the
  explicit constraint *"not too gimmicky, extreme simple 3d not extreme."* Matte rounded solids,
  one soft light, slow drift, no characters. Never the sole carrier of information.
- **Demo data:** rich and toggleable behind `AEGIS_DEMO_DATA`, wiped before the hackathon.

**Design references supplied by the user** (photographed from a search): a soft-3D "Crafting
Status" dashboard, the "Geome" minimal product page, a blue/white UI kit of floating cards with
gauges and sparklines, and two 3D-illustration landing pages. The third is the closest match for
Aegis's product surfaces and drives the token changes above; the others set the 3D language.

`docs/dev_new_docs_v2/frontend-redesign/00-DESIGN-DIRECTION.md` should carry these references and
the extracted rules, so every implementing agent designs against the same brief rather than
against my prose.
