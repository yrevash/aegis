# Phase 1 — audit gate

**Commits under audit:** `e0bbea5` (IBM Plex Sans + text floor), `54286e8` (GSAP boundary), branch `docs/wow-pass-plan`.
**Audited:** 2026-08-27, against the running stack (web :3001, backend :8110), Chromium via Playwright.
**Auditor method:** every claim below is tagged `[MEASURED]` (observed in a running browser or by executing the test) or `[SOURCE]` (read from the tree). No claim is carried over from a commit message.

---

## VERDICT: **FAIL**

One blocker, five majors. The *substrate* of Phase 1 is sound — the font swap is real and rendering, the 211-utility raise caused **zero** visual regressions across 46 screen/width combinations, the reduced-motion branch genuinely works, and the page survives with JavaScript off. What fails is the part the phase said mattered most: **"the fence matters more than the motion."** Both fences are defeatable, one of them by a single line, and the one new motion ships the exact flash the plan warned about.

Remediation is small in absolute terms — roughly one CSS rule, two test predicates, nine inline `fontSize` values and three sentences of DESIGN.md.

| # | Severity | Finding | Category |
|---|---|---|---|
| 1 | **blocker** | Hero paints, blanks, then re-animates on every load (284 ms – 1.7 s) | B |
| 2 | major | `gsapBoundary.test.mjs` passes with three real violations in the tree | B |
| 3 | major | `textFloor.test.mjs` passes with `text-xs` rendering at 8px across 206 call sites | C |
| 4 | major | "Nothing renders below 11px" is false — 9 sites render 10px / 9.5px functional labels | A / F |
| 5 | major | The product's own "Small" text step puts 305 elements per screen at 9.9px | C |
| 6 | major | Chart `fontSize` px values ignore the text-size control entirely | C |
| 7 | minor | `web/README.md:14` still advertises Inter / Space Grotesk | E |
| 8 | minor | `declarationsSeen > 5` guard sits on a knife edge at exactly 6 | C |
| 9 | minor | DESIGN.md §3 type ramp says `title 16/22`; `.t-title` is 18px | F |
| 10 | minor | `devops/security` eyebrow labels overhang their grid cell by 9px (pre-existing) | A |

---

# A. Did the text-floor raise break anything visually?

## Method

Signed in as `northwind.admin` (tenant_admin), `devops`, `northwind.analyst` (ai_team) and `northwind.client`, and walked **32 sections × 2 widths (1440, 1280)**, plus a second sweep of **14 sections at 390px under both the 90% and 125% text-size steps** — 46 + 28 = **74 screen/width/scale combinations**. Full-page screenshots taken at 1440 for every one.

Overflow alone was explicitly *not* the test. For every element whose computed `font-size` was ≤ 11.6px and that owned a direct text node (`sr-only` excluded), I recorded `scrollWidth > clientWidth`, `scrollHeight > clientHeight`, `text-overflow`, and rendered line count. I then ran two **attribution** passes so a finding could be blamed on the raise rather than on pre-existing layout:

1. Force every ≤11.6px element to 10px and re-measure — anything that stops clipping is attributable to the raise.
2. Inject `.eyebrow { font-size: 0.68rem !important }` — the *exact* pre-commit value — and diff `scrollWidth`/`clientWidth`/height per element, matched by text content.

And I looked at the screenshots, on the dense screens named in the brief.

## Result: no regression found `[MEASURED]`

The `0.68rem → 0.6875rem` toggle produced, across `tenant_admin/audit` (18 eyebrows), `tenant_admin/roles` (7), `client/risk` (20) and `ai_team/console` (2), exactly one class of difference: **the line box grew from 16px to 17px**. No element that fitted before overflows now. No element that was one line is now two. Representative row:

```
{"t":"aegis.admin · /admin/users","now":{"sw":217,"cw":217,"h":17},
                                  "old":{"sw":215,"cw":215,"h":16}}
```

Horizontal document overflow was **0 on all 74 combinations**, including 390px at the 125% step. Console errors: **0** across every portal.

The two apparent clip clusters are false positives, checked individually: 45 "clipped" nodes on `tenant_admin/roles` and 9 on `tenant_admin/audit` are all `span.sr-only` (`clientWidth: 1` by construction), and the 209 "wrapped" nodes on `audit` are padding-inflated line-count arithmetic, not wraps. Screenshot evidence: `phase-1-audit-shots/tenant-audit-table.png` — the densest table in the product, clean, aligned, nothing clipped.

### Finding 10 — minor — `web/src/components/devops/…` security card, eyebrow overhang

`devops/security` renders two `dt.eyebrow` labels that overhang their `grid-cols-3` cell:

```
{"t":"max plan iterations","sw":84,"cw":75,"ov":"visible"}
{"t":"hazard categories",  "sw":84,"cw":75,"ov":"visible"}
```

`overflow: visible`, so the second word spills 9px sideways rather than clipping. **This is pre-existing, not a Phase-1 regression** — at 10.88px the same string measures ~83px against the same 75px cell. Screenshot: `phase-1-audit-shots/devops-security-eyebrow.png`.
**Fix (optional):** `hyphens: auto` or a narrower letter-spacing on `dt.eyebrow` inside three-column definition lists.

---

# B. Is the GSAP boundary genuinely sound?

## B.1 Reduced motion — the claim **HOLDS** `[MEASURED]`

Sampled `getComputedStyle(.eyebrow).opacity` on every `requestAnimationFrame` for 2.5 s in a `reducedMotion: 'reduce'` context. **154 samples, minimum opacity 1.00, never below 1 at any frame.** The inline attribute goes from `null` to `opacity: 1;` at t≈479 ms — GSAP's `gsap.set()` firing and writing the end state, exactly as `RevealGroup.tsx:82` claims. Under `no-preference` the same element dips to 0 and tweens back, so the branch is genuinely being selected, not accidentally inert.

## B.2 JavaScript disabled — **HOLDS** `[MEASURED]`

`javaScriptEnabled: false` context: the served HTML contains the headline and **no `opacity: 0` anywhere**. Screenshot `phase-1-audit-shots/nojs-hero.png` shows the complete hero — eyebrow, headline, body, both CTAs — rendered. The hiding is done purely by JS, so no-JS fails open. Correct.

## Finding 1 — **BLOCKER** — the hero flashes: visible → blank → re-animate

**`web/src/components/shared/RevealGroup.tsx:85`**

```js
gsap.set(targets, { opacity: 0, y: 10 })
```

This is the failure mode the plan warned about, and it is present. `useGSAP` is a layout effect: it cannot run until React has hydrated. The server sends the hero at full opacity, the browser paints it, and *then* JS hides it and fades it back in. Measured on the landing page, three CPU rates, repeatable: `[MEASURED]`

| CPU throttle | first-contentful-paint | hero blanks at | **visible-then-blank window** |
|---|---|---|---|
| 1× (none) | — | 507 ms | **284 ms** |
| 4× | — | 1404 ms | **1219 ms** |
| 6× | 412 ms | 2117 ms | **1705 ms** |

The 6× trace, sampled frame by frame:

```
t=2071  op=1     h1op=1      first-contentful-paint@412
t=2117  op=0     h1op=0      <- hero blanks, 1.7s after it was painted
t=2159  op=0.379 h1op=0.064
t=2193  op=0.624 h1op=0.393
```

Caveat, stated honestly: this is the **dev server**, where hydration is slower than a production build. But the mechanism is structural, not a dev artefact — the SSR HTML paints at opacity 1 by design (that is what makes B.2 pass), and no amount of bundle optimisation moves a layout effect before the first paint. The 284 ms figure at **zero throttle on localhost** is the floor, not the typical case; a real network and a mid-range laptop land between the 4× and 6× rows.

**Recommended fix.** Set the initial hidden state in CSS, gated on a class the client adds, so it is never visible-then-hidden — and keep the no-JS escape:

```css
/* globals.css */
.js .reveal-group > * { opacity: 0; }
@media (prefers-reduced-motion: reduce) { .reveal-group > * { opacity: 1 !important; } }
```
with `document.documentElement.classList.add('js')` in the existing synchronous `<head>` script (`TEXT_SCALE_BOOT` already establishes that this app is willing to run one). GSAP then only ever tweens *up* from a state the very first paint already had. Delete the `gsap.set(…, {opacity: 0})` line.

## Finding 2 — major — `gsapBoundary.test.mjs` can be defeated three ways

**`web/tests/design/gsapBoundary.test.mjs:46, 67, 76, 80, 97, 114`**

Every check is `text.includes('…')` over files matched by one import regex. I wrote three violating components into `web/src/`, ran the suite, and **all four checks passed**: `[MEASURED]`

```
✔ every file that imports gsap uses useGSAP, never a bare effect
✔ every gsap tween sits behind a reduced-motion conditional
✔ the reduce branch arrives rather than returning empty-handed
✔ gsap selectors are scoped to their own subtree
ℹ pass 4  ℹ fail 0
```

The three, all deleted afterwards (tree verified clean, `npm test` → 405/405):

**(a) Dynamic import.** `gsapFiles()` at line 46 requires the literal token `from`. A dynamic import is never collected, so the file is not scanned at all:
```tsx
useEffect(() => {
  void (async () => {
    const { gsap } = await import('gsap')
    gsap.to(ref.current, { x: 300, duration: 4, repeat: -1, yoyo: true })  // infinite, ignores reduce
  })()
}, [])
```

**(b) Laundered import.** `src/lib/motion.ts` does `import gsap from 'gsap'; export { gsap }` — that file *is* scanned, and passes because a comment in it contains the five magic strings. Every consumer then writes `import { gsap } from '@/lib/motion'`, which matches neither regex and is never scanned. This is the most plausible of the three: a barrel file is a normal thing to add.

**(c) Magic strings in a comment.** The worst spirit-violation that passes on its own merits — a direct import, a bare `useEffect`, an **unscoped document-wide selector**, an infinite repeat, no `matchMedia` at all:
```tsx
import gsap from 'gsap'
/** useGSAP · gsap.matchMedia · (prefers-reduced-motion: reduce) · gsap.set( · { scope } */
export function Spinner() {
  useEffect(() => { gsap.to('.eyebrow', { opacity: 0.2, duration: 3, repeat: -1, yoyo: true }) }, [])
}
```
Note what (c) does: `gsap.to('.eyebrow', …)` reaches every eyebrow in the document. The `{ scope }` check at line 114 exists precisely to stop that, and a comment satisfies it.

**Recommended fix.** Strip comments before scanning (`text.replace(/\/\*[\s\S]*?\*\/|\/\/.*$/gm, '')`) — that kills (c) alone. Then add `await import\(['"]gsap` and a barrel guard to the collector: assert that `gsap` and `@gsap/react` are imported **only** from an allowlist of files (today, `RevealGroup.tsx`), which closes (a) and (b) permanently and is a one-line invariant rather than an arms race. A stronger version still: assert `gsap.to`/`gsap.from`/`gsap.timeline` appear only *inside* a `mm.add(` block, by brace-matching rather than by file membership.

---

# C. Is the textFloor test defeatable? Yes — and one line is enough.

## Finding 3 — major — `--text-xs: 0.5rem` re-introduces sub-floor text globally, and the test stays green

**`web/tests/design/textFloor.test.mjs:66, 95`** vs **`DESIGN.md:179`**

DESIGN.md says: *"The test checks the token sheet too, so one line cannot reintroduce the problem globally."* That is the specific sentence this finding falsifies. `globals.css` uses Tailwind v4's `@theme inline` block; the named type scale is defined by `--text-*` custom properties, but the second test only matches `font-size:` **declarations**. There are 206 `text-xs` call sites under `src/`.

I added exactly one line to the `@theme inline` block, plus two sub-floor rules in unmatched syntax, and ran both the test and a browser: `[MEASURED]`

```
globals.css:176   --text-xs: 0.5rem;                    /* 206 call sites → 8px */
globals.css:389   .t-nano { font-size: calc(0.5rem + 1px); }   /* 9px  — calc() unmatched */
globals.css:392   .t-pt   { font-size: 7pt; }                  /* 9.33px — unit unmatched */

$ node --test tests/design/textFloor.test.mjs
✔ no arbitrary text utility under src/ resolves below the floor
✔ the token ramp itself does not dip below the floor
ℹ pass 2  ℹ fail 0

$ (live browser, localhost:3001)
RENDERED: {"textXs":"8px","tNano":"9px"}
```

Green test, 8px text in the running product. All reverted; `globals.css` is byte-identical to `e0bbea5` and `npm test` is 405/405.

The full list of holes in the first check's regex `/text-\[(\d*\.?\d+)(px|rem|em)\]/`, each verified not to match:
- inline `style={{ fontSize: '8px' }}` — **already used in the tree, see Finding 4**
- `text-[7pt]`, `text-[62.5%]`, `text-[calc(0.5rem+1px)]`, `text-[length:var(--x)]`
- any `.css` file other than `globals.css` (test 1 scans `.css` files but only for the utility pattern; test 2 hard-codes `globals.css`)

For the record, two things that **do** hold: `.5rem` (leading dot) *is* caught, and `text-xs` in this Tailwind v4 setup resolves to the default `0.75rem` = **12px** today, so the named scale is currently above the floor. It is simply unguarded.

**Recommended fix.** Three additions, all cheap:
1. Match `--text-*` and `--font-size-*` custom properties in the `@theme` block, not just `font-size:` declarations.
2. Scan JSX `fontSize:` / `fontSize={…}` and reject numeric or px values under 11.
3. Reject *any* `text-[…]` or `font-size:` whose value the parser cannot resolve to px (`calc`, `var`, unknown units) — an unparseable size should fail loudly, not be silently skipped. That converts the whole class of bypass into a single "state the size in px, rem or em" rule.

## Finding 4 — major — nine sites already render functional text below the floor

`[MEASURED]` — on `devops/redteam`, 12 elements render at **10px**; on `client/risk`, 12 render at **9.5px**. These are not decoration:

| file:line | size | what it is |
|---|---|---|
| `web/src/components/client/RiskMatrix.tsx:184,197` | 10px | risk-matrix axis numerals |
| `web/src/components/client/RiskMatrix.tsx:208,218` | **9.5px** | `LIKELIHOOD →` / `IMPACT →` axis titles |
| `web/src/components/redteam/BlockRateTrend.tsx:128,139` | 10px | date + rate axis ticks |
| `web/src/components/ops/EvalTrend.tsx:136,143` | 10px | eval-trend axis ticks |
| `web/src/components/forecast/HorizonChart.tsx:221` | 10px | forecast axis ticks |
| `web/src/components/graph/KnowledgeGraph.tsx:257` | **2.5px floor** | `Math.max(11/scale, 2.5)` canvas node labels |

Screenshots: `phase-1-audit-shots/client-riskmatrix-9_5px.png`, `phase-1-audit-shots/devops-redteam-full.png`. The `IMPACT →` label is additionally clipped (`scrollWidth 78 > clientWidth 37`).

The textFloor docblock argues at length that *"Aegis's smallest text lives inside SVGs and is real content … an exemption for ornament is wrong for a diagram."* It is right, and then the test it introduces does not look at any of it. A chart axis tick is exactly the diagram label that argument is about.

**Fix:** raise the nine values to 11 and change `KnowledgeGraph.tsx:257`'s floor from 2.5 to 11; add the JSX scan from Finding 3.

## Finding 5 — major — the "Small" text step drops the whole product under the floor

**`web/src/components/settings/textScale.ts:40`** — `{ percent: 90, label: 'Small' }`

The type system is rem-based, which is what makes the text-size control work; it also means the 11px floor is 11px only at 100%. `[MEASURED]` on the running app with `aegis.textScale = 90`:

```
root font-size: 14.4px    .eyebrow computed: 9.9px
```

Counted at 390px on the 90% step, elements owning real text below 11px, per screen:

```
tenant_admin/audit    305    devops/redteam    180    devops/stack   105
ai_team/guardrails    101    tenant_admin/roles 89    client/risk     83
tenant_admin/settings  68    ai_team/evals      59    ...
```

All 211 raised utilities and `.eyebrow` land at **9.9px** — below where `.eyebrow` sat *before* Phase 1 (10.88px). One click in Settings undoes the entire commit. The `textScale.ts` docblock records that the **top** step was verified at four widths; nothing verified the bottom one, and the floor commit did not revisit it.

**Fix:** either drop the 90% step (100% is already the small end of a 100/110/125 ladder), or move the floor to a `--text-floor` custom property whose value is `max(11px, 0.6875rem)` so it cannot scale below 11px — the second is better, because the reader who picks "Small" has told you they want density, not illegibility.

## Finding 6 — major — the text-size control cannot reach the chart labels at all

**`web/src/components/settings/textScale.ts:5`** claims:

> *"`globals.css` sets no root font-size and declares no `px` font-size anywhere … one property on `<html>` scales every word in the product proportionally."*

`[MEASURED]` — at the **125%** step, `devops/redteam` still renders 9 elements at 10px and `client/risk` still renders 12 at 9.5px. The nine sites in Finding 4 are numeric px passed to Recharts/SVG in JS; `<html> { font-size: 125% }` does not touch them. A low-vision reader who turns text up to the maximum gets a 9.5px axis label either way. This is the same nine sites as Finding 4, but it is a distinct defect: it breaks the accessibility *control*, not just the floor.

**Fix:** the same raise, expressed in `em` or read from a CSS variable so the ticks scale.

## Finding 8 — minor — the anti-vacuity guard is one refactor from a false failure

**`web/tests/design/textFloor.test.mjs:105`** — `assert.ok(declarationsSeen > 5, …)`. `globals.css` contains exactly **6** matching declarations (a 7th, `.t-hero`, uses `clamp()` and does not match). I tripped this twice by accident while probing: converting any single `font-size` to `clamp()`, `calc()` or `var()` — all normal, correct refactors — drops the count to 5 and fails the test **for the wrong reason**, which trains the next reader to raise the threshold rather than read it. `[MEASURED]`

**Fix:** count *all* `font-size` declarations for the guard (including unparseable ones) and only apply the floor to the ones that resolve; or drop the threshold to `> 0` and rely on the Finding-3 fix.

---

# D. Font correctness — **CONFIRMED** `[MEASURED]`

Every check passed. Method: fresh context, recorded every request/response, awaited `document.fonts.ready`, then read `document.fonts`, canvas glyph metrics and computed styles.

**Network — the only three font requests made, all 200:**
```
200  fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600
                              &family=JetBrains+Mono:wght@400;500&display=swap
200  fonts.gstatic.com/s/ibmplexsans/v23/…woff2
200  fonts.gstatic.com/s/jetbrainsmono/v24/…woff2
```

**`document.fonts` — six faces reach `loaded`:** IBM Plex Sans 400/500/600 and JetBrains Mono 400/500. Nothing is stuck at `unloaded` in the used weights.

**Glyph metrics — genuinely Plex, not a silent fallback.** Canvas `measureText` of the hero string at 100px:
```
"IBM Plex Sans"                     1733.40 px
ui-sans-serif, system-ui, sans-serif 1625.93 px   (Δ 6.6%)
Arial                                1684.62 px   (Δ 2.9%)
```
Three distinct widths — the stack is not resolving to either fallback. Computed `font-family` on `<h1>` and `<body>` is `"IBM Plex Sans", ui-sans-serif, system-ui, sans-serif`; the rendered `<h1>` is 503.98px wide. `.eyebrow` computes to `"JetBrains Mono", …` at `11px`, confirming both the mono decision and the floor value. Visually confirmed in `phase-1-audit-shots/nojs-hero.png`.

**No Space Grotesk, no Inter request.** Zero requests to any URL containing either name. (One caveat on method, stated so it is not over-read: `document.fonts.check('600 16px "Space Grotesk"')` returns `true`, but that is `check()` reporting that *something* can render the string, not that the face is present — the network log is the authority here, and it is clean.)

---

# E. Stale survivors

## Finding 7 — minor — `web/README.md:14` `[SOURCE]`

```
- Fonts (Inter / Space Grotesk / JetBrains Mono) via a runtime `<link>` (not
  `next/font`) so `next build` never blocks on a font fetch
```

The identical sentence in `layout.tsx` was updated by `e0bbea5`; this copy of it was not. It is the first place a new contributor looks. **Fix:** `IBM Plex Sans / JetBrains Mono`.

## Checked and clean

- **`web/src/`** — zero references to Inter or Space Grotesk in any `.ts`, `.tsx` or `.css` file. `[SOURCE]`
- **`web/tests/`** — zero. `[SOURCE]`
- **`DESIGN.md`** — zero. `[SOURCE]`
- **`docs/dev_new_docs_v2/sota/10-design-system.md`** — 11 hits, all correct: it is the *plan* document, quoting the pre-change state it was written to change. Line 1106 states the expected post-change verification ("requests IBM Plex Sans + JetBrains Mono and no Space Grotesk"), which is what D confirms. Not stale.
- **`docs/design/CLAUDE-REFERENCE.md`**, **`scripts/build-teaching-html.mjs`** — hits for "Inter", both unrelated to the console's font stack (an external design reference and the teaching-doc HTML generator). Not stale.

---

# F. DESIGN.md honesty

## Finding 4 (restated as a documentation defect) — major — **`DESIGN.md:177`**

> **Nothing renders below 11px.**

Measured false in three independent ways, all above: nine chart sites render 10px and 9.5px on shipping screens (Finding 4); the knowledge graph floors at 2.5px; and the product's own Small text step puts up to 305 elements per screen at 9.9px (Finding 5). This is the phase's most quotable sentence and it does not survive opening `devops/redteam`.

**Fix:** make it true (raise the nine, floor the graph, guard the scale) — or, if the chart ticks are to stay, say so explicitly and name them, which is the honest version and costs one clause.

## Finding 3 (restated) — major — **`DESIGN.md:179`**

> The test checks the token sheet too, so one line cannot reintroduce the problem globally.

One line does exactly that. Proven in C above with a live browser reading 8px while the test is green.

## Finding 9 — minor — **`DESIGN.md:185`**, the type ramp

```
title  16/22  0  600  card title
```
`.t-title` in `globals.css:348` is `font-size: 1.125rem` = **18px**. `[SOURCE]` The ramp block was not edited by either commit, so this is pre-existing — but §3 is a section Phase 1 claims to have updated, and it now carries a corrected font-family paragraph directly above a stale size table. Separately, `10-design-system.md:707` planned `.t-title` 18px → 19px as "the one size change"; that change was not made and is not recorded as dropped.

## What §3, §6 and §7 get right `[SOURCE]` + `[MEASURED]`

Read in full and checked against the code. All of the following are accurate:

- §3 "Plex Sans is a superfamily, so `--font-display` resolves to the same family" — matches `globals.css:176-177`.
- §3 the `--font-mono` non-move and its reasoning — matches `globals.css:178`; JetBrains Mono confirmed loading in D.
- §6 the four named motions, and M1 firing on **first mount only** — `useGSAP(fn, { scope })` with no `dependencies` defaults to `[]`, so it runs once. Correct.
- §6 "the reduced-motion kill switch cannot reach GSAP" and the fix — verified in B.1: under `reduce` the element is at opacity 1 on every one of 154 sampled frames, and the inline `opacity: 1` proves `gsap.set()` is the thing that put it there.
- §7 the note admitting a second animation engine was added against the bundle-cost rule that rejected `react-force-graph-3d` — `motion@^13.1.0` and `gsap@^3.15.0` are both in `web/package.json`, so the tension it describes is real and is stated rather than hidden. This is the strongest paragraph in the diff and should not be edited away.

## Claims 6 and 7 of the phase brief, checked `[SOURCE]`

- `ComplianceView.tsx` — the `border-l-2` accent stripe is gone, replaced by a `Badge`. Correct.
- `RiskDumbbell.tsx:234-241` — the `impeccable-disable-next-line side-tab` waiver now sits on the line immediately above `borderRight`, with the explanation moved above it. Mechanically correct, and the waiver itself is legitimate: it is a CSS-triangle arrowhead on a `size-0` span, not a card accent.

---

# What I could not break

Stated so the clean categories are not read as unexamined:

- **No visual regression from the 211-utility raise.** 74 screen/width/scale combinations, two attribution passes, screenshots of every dense screen named in the brief. The only measurable effect is a 1px taller line box on eyebrows. This part of Phase 1 is genuinely well executed.
- **Reduced motion.** 154 rAF samples under `reduce`, minimum opacity 1.00. The `matchMedia` reduce-branch-sets-final-state pattern does what it says.
- **No-JS.** Hero fully rendered, no `opacity: 0` in the served HTML.
- **Fonts.** Three requests, three 200s, six loaded faces, glyph widths distinct from both fallbacks.
- **Horizontal overflow.** Zero on every combination, including 390px at 125%.
- **Console errors.** Zero across four portals and 32 sections.
- **Suite integrity.** `npm test` → **405 pass, 0 fail** before and after every sabotage; `git status` shows `web/` clean.

## Suggested order of remediation

1. Finding 1 (the flash) — one CSS rule and one class, and it is on the landing page.
2. Findings 4 + 6 — nine numeric values; fixes the false DESIGN.md claim and the accessibility control together.
3. Finding 3 + 8 — the textFloor predicates; do this before Finding 4 so the raise is guarded, not just done.
4. Finding 2 — the gsapBoundary allowlist; one invariant beats three patches.
5. Finding 5 — decide the 90% step.
6. Findings 7, 9, 10 — text edits.
