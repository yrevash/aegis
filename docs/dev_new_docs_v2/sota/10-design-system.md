# 10 — The design system: IBM Plex, GSAP, and the boundary that has to be a test

> **STATUS: PLAN. No source file was modified writing this.** The owner has already decided the
> four things this document plans around — the typeface changes to IBM Plex, GSAP is adopted
> alongside Motion, Impeccable is a read-only detector, and DESIGN.md may change to accommodate all
> of it. None of those is re-litigated below. What is planned is *how*, and what has to be true
> afterwards for the result to still be checkable.

> **Source of every claim here.** `[MEASURED]` — run on this machine, today, and the command is
> given. `[SOURCE]` file:line — read in this repo or in an installed package. `[DOC]` — vendor
> documentation. Where this document asserts something it did not establish, it says so in the
> same sentence.

---

## Three things the brief said that turned out not to be true

These are stated first because the plan below is built on the corrected facts, and because two of
them invert the risk.

**1. Fonts are not loaded via `next/font`.** They are loaded by a plain `<link>` to Google Fonts in
the document head **[SOURCE]** `web/src/app/layout.tsx:41-44`, and the file says why in a comment
it kept deliberately **[SOURCE]** `layout.tsx:12-15`:

> *Fonts (Inter / Space Grotesk / JetBrains Mono) are loaded via a plain `<link>` at runtime rather
> than `next/font` so `next build` never depends on a network fetch.*

That comment is a decision with a reason, and the reason still holds on a hackathon machine. **The
migration keeps the `<link>`, it does not introduce `next/font`.** Section A plans it that way and
says what is given up.

**2. IBM Plex Sans is NARROWER than Inter, not wider.** Measured in Chromium against the real
`latin` woff2 subsets of both faces, on seven strings taken from this product **[MEASURED]**:

| String (13px / 400) | Inter | IBM Plex Sans | Δ |
|---|---|---|---|
| `northwind-policy-handbook-2024.pdf` | 232.48px | 221.23px | **−4.84%** |
| `Llama-3.2-90B-Vision-Instruct` | 187.94px | 180.53px | −3.94% |
| `Simulation & what-if` (longest nav row) | 123.14px | 118.92px | −3.43% |
| `The rate actually paid, not the list price.` | 242.58px | 229.63px | −5.34% |
| `Northwind Traders Ltd` | 137.66px | 130.20px | −5.41% |
| `DECIDED BY` | 77.75px | 73.48px | −5.49% |
| `SCREEN · RETRIEVE · RERANK · GENERATE · GUARD` | 322.33px | 306.84px | −4.80% |
| **mean** | | | **−4.75%** |
| **least favourable case** | | | **−3.43%** |

**Not one string got wider.** The 390px line budget goes *up*: 47 characters of 13px Inter fit in
342px of content, 49 characters of IBM Plex Sans do **[MEASURED]**.

**The real risk is the opposite one, and it is worse because it is invisible to an overflow sweep.**
IBM Plex Sans has a **smaller x-height and a smaller cap-height** than Inter at the same `px`
**[MEASURED]**, canvas ascents at 100px:

| Face | x-height | cap-height | descender |
|---|---|---|---|
| Inter | 54.59 | 72.75 | 21.58 |
| **IBM Plex Sans** | **51.60** | **69.80** | 21.20 |
| JetBrains Mono | 55.00 | 73.00 | 18.00 |
| **IBM Plex Mono** | **51.60** | **69.80** | 21.20 |
| Space Grotesk | 49.40 | 70.00 | 20.00 |

IBM Plex Sans reads **5.5% smaller** at the same declared size. This console's smallest functional
text is `.t-mono` at `0.72rem` = 11.52px and `.eyebrow` at `0.68rem` = 10.88px **[SOURCE]**
`globals.css:296, 377`. A 5.5% apparent shrink there lands under the legibility floor Section E
adopts. **And the obvious fix is the trap:** compensating with a ~5% size bump gives back exactly
the width that was won, and *that* is what breaks the tables. The plan therefore compensates on
`line-height` and ink, never on size, and Section A says where the one deliberate size change goes.

**3. The `.animate-*` call-site count is 142, not ~104, and only 41 of them are ours.** Broken out
**[MEASURED]** (`grep -rho "animate-[a-z-]*" --include=*.tsx --include=*.ts src/`): `animate-spin`
55, `animate-none` 37, `animate-pulse` 7, `animate-in`/`animate-out` 2 — all Tailwind built-ins —
and **41 call sites across the 14 Aegis keyframes**: `pip` 11, `reveal` 8, `trace-in` 6, `beat` 3,
`trust-shimmer` 2, `section` 2, `beat-open` 2, and one each of `probe-land`, `probe-impact`,
`probe-next`, `mark-spin`, `mark-draw`, `mark-core`, `flow-pulse`. That number matters in Section C:
the restraint budget is already spent on 41 sites, and GSAP must not add a 42nd idiom.

---

## The system as it stands, verified

Everything in this section was read today, so the plan below can be checked against it rather than
against a memory of it.

**`web/src/app/globals.css` — 650 lines [MEASURED].** Tailwind v4, CSS-first: `@import 'tailwindcss'`
at line 1, tokens in `:root`, utilities exposed through `@theme inline`, and **no `tailwind.config.js`
anywhere in `web/`** **[MEASURED]** — confirmed, the config is the stylesheet.

- **119 CSS custom properties** declared across `:root` and `@theme inline` **[MEASURED]**, 59 of
  them in the `:root` value block.
- **Font declarations at `globals.css:165-167`** **[SOURCE]** — three lines, and they are the whole
  surface area of the typeface change:
  ```css
  --font-sans: 'Inter', ui-sans-serif, system-ui, sans-serif;
  --font-display: 'Space Grotesk', 'Inter', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SFMono-Regular', monospace;
  ```
- **The blue namespace reset at `globals.css:209`** — `--color-blue-*: initial;` closes Tailwind's
  own blue scale before reopening it with only the eight DESIGN.md steps **[SOURCE]** `:209-217`.
  The comment states the mechanism: *"a half-overridden scale is how an off-system hue gets in."*
- **14 `@keyframes`** **[MEASURED]**, at `:342, 391, 409, 425, 440, 457, 471, 511, 528, 549, 573,
  596, 613, 626` **[SOURCE]**. Three of them carry their own local
  `@media (prefers-reduced-motion: reduce)` override (`:562`, `:588`).
- **The global reduced-motion kill switch, `globals.css:642-650`** **[SOURCE]** — the single most
  important eleven lines in this plan:
  ```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after {
      animation-duration: 0.001ms !important;
      animation-iteration-count: 1 !important;
      transition-duration: 0.001ms !important;
    }
  }
  ```

**Motion 13 is imported by 7 files; only 3 of them use the animation engine [MEASURED].**

| File | Imports | Engine call sites (`<motion.*` / `<AnimatePresence`) |
|---|---|---|
| `console/ChatConsole.tsx` | `motion, useReducedMotion` | 2 |
| `console/LaneBoard.tsx` | `motion, useReducedMotion` | 2 |
| `jobs/PipelineIso.tsx` | `motion, useMotionValue, useReducedMotion, useSpring, useTransform` | 6 |
| `console/RunMark.tsx` | `useReducedMotion` only | 0 |
| `console/FlowCanvas.tsx` | `useReducedMotion` only | 0 |
| `console/useRevealedText.ts` | `useReducedMotion` only | 0 |
| `guardrail/RailFiringLine.tsx` | `useReducedMotion` only | 0 |

**`useReducedMotion` appears at 15 call sites across those 7 files [MEASURED].** Four of the seven
files import Motion *purely* for that hook — a 46.4 kB gzip dependency **[MEASURED]**
(`gzip -c node_modules/motion/dist/motion.js`, un-tree-shaken) used as a `matchMedia` wrapper.
Section C notes that but does not touch it: Motion stays, by the owner's decision.

**The established idiom, and it is not "use the engine".** Read
`console/RunMark.tsx:114, 142, 150, 167` and `guardrail/RailFiringLine.tsx:195, 236, 267, 327`
**[SOURCE]**: both call `useReducedMotion() ?? false` once, then **conditionally apply a CSS
`.animate-*` class**. Motion supplies the *predicate*; CSS supplies the *animation*; the
`globals.css:642` kill switch is the backstop underneath. `RunMark.tsx:60-73` even documents the
reduced-motion *channel* — `screening` differs from `idle` by stroke weight, not by motion, *"so a
viewer who has turned motion off still sees the state change rather than nothing at all"*
**[SOURCE]**. That is the discipline GSAP has to be fitted into rather than around.

**The eight design tests, and exactly what each enforces.** All eight are in `web/tests/design/`.

| Test | What it enforces | Its anti-vacuity floor |
|---|---|---|
| `lightThemeOnly.test.mjs` | No Tailwind `dark:` variant on any line of any `.ts/.tsx/.css` under `src/`. Regex `/\bdark:[a-z[-]/` — the *variant*, not the word, so `dark` in prose or a token name is fine **[SOURCE]** `:46`. | `assert.ok(files.length > 100)` **[SOURCE]** `:39` |
| `oneRamp.test.mjs` | Two rules. (a) No retired subject alias `agent`/`graph`/`ml` in any utility or variable spelling **[SOURCE]** `:32`. (b) Every `blue-N` named anywhere under `src/` is one of the eight ramp steps `50 100 200 400 600 700 800 900` **[SOURCE]** `:29`. | `files.length > 100` **[SOURCE]** `:51` |
| `badgeContrast.test.mjs` | **Recomputes** WCAG contrast from the live token hexes in `globals.css` and the live `SIGNALS` map in `src/config/signals.ts`, compositing each `bg-x/15` wash over white, and fails any badge tone under **4.5:1 at 12px** **[SOURCE]** `:32, :87-96`. Second test: a status ink is never the same token as its own fill **[SOURCE]** `:98-104`. | `rows.length >= 7` on the parsed signal map **[SOURCE]** `:68` |
| `figureTruncate.test.mjs` | No `<Figure>` carries `truncate` in its `className` — `Figure` is an `inline-flex`, so `text-overflow` never renders and the text clips mid-glyph with **no document overflow to measure** **[SOURCE]** `:1-17`. `truncate` is a prop. | `files.length > 50` **[SOURCE]** `:43` |
| `navGroups.test.mjs` | The rail and the mobile drawer render one list: `navSectionIds(portal)` equals `ROLE_SECTIONS[portal]` as a set *and* by length (no duplicates); catalogue order survives within a heading; an ungrouped section is placed not dropped; the active section is read only off the portal route; a tenant-pinned principal is not offered `cache` **[SOURCE]** `:21-94`. | `assert.equal(PORTALS.length, 5)` **[SOURCE]** `:23` |
| `navTooltipLength.test.mjs` | Every `SECTIONS[*].tooltip` is **≤ 12 words**; and `PortalNav.tsx`'s rendering contains no `title=` at all — the cap alone would leave 34 native tooltips that clip, time out and take no keyboard focus **[SOURCE]** `:34, :67-79`. | `entries.length > 20`, plus a **≥ 3-word minimum** so an empty gloss cannot pass the cap **[SOURCE]** `:40, :63` |
| `receiptText.test.mjs` | `receiptText()` prints a label the origin already carries **once** (`Source: Source: …` was the defect); an absent detail leaves no dangling ` · `; `splitDetail` puts measured facts inline and prose in a tip and **never drops either**; `formatUsdAuto` never renders a real 48-cent saving as `$0`; `reductionPct` returns `null` rather than 100% with no baseline **[SOURCE]** `:22-89`. | Round-trip assertion — *"a receipt may be moved, never dropped"* **[SOURCE]** `:64` |
| `tipLength.test.mjs` | Every `<InfoTip>` body under `redteam/`, `graph/`, `compliance/`, `documents/` is **≤ 40 words**, counting the longest string literal of a `{…}` expression because a conditional renders one branch **[SOURCE]** `:44, :55-63`. | `found.length > 8` **[SOURCE]** `:123` |

**Every one of the eight has an explicit anti-vacuity floor.** That is the house pattern, and the
new test in Section B inherits it — a scan whose subject can silently become empty proves nothing
when it passes.

**Baselines, measured today.**

| Command | Result |
|---|---|
| `cd web && npx tsc --noEmit` | exit **0**, no diagnostics **[MEASURED]** |
| `cd web && npm test` | **tests 399 · pass 399 · fail 0**, 4.24s **[MEASURED]** |
| `cd web && npx impeccable detect src --json` | exit **2**, **3 findings** **[MEASURED]** |
| `reactStrictMode` | **`true`** **[SOURCE]** `web/next.config.mjs:3` |
| `gsap` / `@gsap/react` installed | **3.15.0** / **2.1.2** **[MEASURED]**, `node -p "require('./node_modules/gsap/package.json').version"` |

---

# A. Typeface migration — IBM Plex Sans + IBM Plex Mono

## A0. The recommendation on JetBrains Mono, first, because it changes the size of the job

**Keep JetBrains Mono. Do not migrate the mono face.** Three measured reasons and one risk reason.

**It is not on Impeccable's overused list.** The `overused-font` rule's actual set is
`inter, roboto, open sans, lato, montserrat, arial, helvetica, fraunces, instrument sans,
instrument serif, geist, geist sans, geist mono, mona sans, plus jakarta sans, space grotesk,
recoleta` **[SOURCE]**
`~/.npm/_npx/…/impeccable/cli/engine/shared/constants.mjs:23-31`. **Inter and Space Grotesk are both
on it; JetBrains Mono, IBM Plex Sans and IBM Plex Mono are all absent.** So swapping the two sans
faces clears the finding completely, and the mono face was never the problem.

**Migrating it would buy no layout coherence, because there is none to buy.** Both monos have an
identical **0.6000em advance** at every size tested — 11.52px, 12px, 13px, 16px **[MEASURED]** —
and every real product string measures byte-identical: `run_01JQ8Z4K2M9X` 110.59px in both,
`2026-08-27T14:03:12Z` 138.25px in both, `$0.4788` 48.39px in both **[MEASURED]**. Column alignment
is unchanged either way, so "family coherence" is the only argument for the swap.

**And it would cost the one thing this console cannot spare.** JetBrains Mono's x-height is
**55.00**; IBM Plex Mono's is **51.60** — a **6.2% apparent shrink** **[MEASURED]** — at the
smallest, densest, most-read text in the product: `.t-mono` 11.52px and `.eyebrow` 10.88px, across
**324 `font-mono` call sites** **[MEASURED]**. JetBrains Mono's tall x-height is its whole design
brief. Aegis uses mono for *every numeral, id, cost, count and timestamp* through `Figure`
**[SOURCE]** `DESIGN.md:165` — the figures a jury reads off a projector.

**The risk reason:** the mono swap is 324 call sites of blast radius for zero measured layout gain
and a measured legibility loss. **`--font-mono` does not change.**

## A1. What actually changes

**Three lines of CSS and one `<link>` href.** That is the whole mechanical change.

`web/src/app/globals.css:165-167` becomes:

```css
  --font-sans: 'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif;
  --font-display: 'IBM Plex Sans', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'JetBrains Mono', ui-monospace, 'SFMono-Regular', monospace;
```

Note `--font-display` **loses its Inter fallback and becomes the same family as `--font-sans`**.
That is the point of a superfamily: the display voice comes from **weight and tracking**, not from a
second face. Keeping the token (rather than deleting it) matters — there are 11 `font-display` call
sites plus `.t-hero`/`.t-metric`/`.t-title` **[MEASURED]** — and keeping it also leaves one lever to
reintroduce a distinct display face later without touching a call site.

`web/src/app/layout.tsx:42` becomes:

```
https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap
```

**Space Grotesk is dropped from the request entirely.** The weight sets are preserved exactly as
they are today — 400/500/600 sans, 400/500 mono — because every weight the system uses is already
declared in `globals.css` (`.t-body` 400, `.t-label` 500, `.t-title`/`.t-metric`/`.t-hero` 600,
`.eyebrow` 500 **[SOURCE]** `:322-379`) and nothing needs a new one. Space Grotesk's 700 was never
used by any utility.

**Do not migrate to `next/font`.** `layout.tsx:12-15` records the reason it was rejected: `next build`
must not depend on a network fetch **[SOURCE]**. What is given up by staying on `<link>` is real and
should be written down rather than discovered: no self-hosting, no build-time subsetting, no
automatic `size-adjust` fallback metrics, and a `display=swap` FOUT on a cold cache. **The FOUT gets
worse with this change, not better** — the fallback for `--font-sans` is `ui-sans-serif`/`system-ui`,
which on macOS is SF and on the Windows demo box is Segoe UI, and IBM Plex Sans is 4.75% narrower
than either. The swap-in will *reflow*. Mitigation, which is one line and no new dependency: add
`size-adjust` fallback `@font-face` rules in `globals.css` so the fallback matches Plex's metrics.
If the reflow measures unacceptable at rehearsal, the escape hatch is self-hosting the two woff2
files under `web/public/fonts/` with `font-display: optional` — **and that is a Phase-11-style
decision that should be taken with a measurement, not pre-emptively.** The `latin` subsets are
small: IBM Plex Sans 400 is **17,604 bytes**, JetBrains Mono 400 is **21,212 bytes** **[MEASURED]**
(`curl` of the gstatic URLs in the `css2` response).

## A2. The layout risk, correctly aimed

Because Plex is *narrower*, **no screen can newly overflow from the swap alone.** The `shoot.mjs`
sweep will therefore come back green, and **a green sweep is not evidence the change was good.**
The failure this migration can actually produce is legibility, and it has three shapes:

**Shape 1 — small text drops below the floor.** 5.5% off a smaller x-height at 10.88px and 11.52px.
Mitigated **without a size change**: `.eyebrow` keeps `0.68rem` but its `letter-spacing` drops from
`0.16em` to `0.12em` (Plex's wider default sidebearings make `.16em` read as gappy at these sizes,
and the measured `DECIDED BY` case already came back 5.49% narrower, so there is width to spend on
tracking), and `.t-mono` is untouched because the mono face is untouched.

**Shape 2 — the display voice goes flat.** Space Grotesk was carrying the hierarchy at `.t-title`
18px; Plex Sans at the same size and weight is **7.9% narrower** on `Model & routing policy` and
**7.8% narrower** on `Retrieval-augmented generation` **[MEASURED]**. That is a visible loss of
presence on 12 `.t-title` call sites and 6 `.t-hero` **[MEASURED]**. **This is where the one
deliberate size change goes:** `.t-title` moves `1.125rem → 1.1875rem` (18px → 19px), which recovers
the presence and still lands *narrower than today* on both strings. `.t-metric` needs no change —
at 28px/600 Plex is **+4.27%** on `1,284` and **+5.13%** on `99.97%` **[MEASURED]**, i.e. hero
numerals actually gain weight on the page, which is the right direction.

**Shape 3 — the `Figure` primitive.** `Figure` sets `font-mono` **[SOURCE]**
`primitives/Figure.tsx:98` and its `display` size is `text-[1.75rem]` with `tracking-[-0.02em]`
**[SOURCE]** `:15`. Since mono does not change, `Figure` does not change. **Verify it anyway** — it
is the single most-repeated element in the product and the one a jury reads.

## A3. The highest-risk screens, named, and how each is verified

Not "check everything." These five, in this order, because each is the *worst instance* of one
failure mode.

| # | Screen | File | Why it is the worst case | Verification |
|---|---|---|---|---|
| 1 | **Corpus table** | `components/jobs/CorpusPanel.tsx` (has its own `overflow-x-auto` **[MEASURED]**) | Longest untruncatable strings in the product — document filenames. `northwind-policy-handbook-2024.pdf` is the −4.84% case, so it *gains* room, but it is also the screen where the `figureTruncate` failure mode originally shipped. | `node scripts/shoot.mjs --portal ai_team` at 390/834/1440; then **eyes on 390**: does the filename column still ellipsize where it should, and is the `.t-mono` id column still readable? |
| 2 | **Admin — roles & access** | `components/admin/RolesAccess.tsx`, `SeatsPanel.tsx`, `DelegationMap.tsx` | Densest label-per-pixel surface: role names, seat counts and delegation arrows in one grid, at `.t-label` 13px/500 where the measured delta is **−4.28% mean** **[MEASURED]**. Small-text legibility, not overflow. | `node scripts/shoot.mjs --portal platform_admin`; eyes on 390 and **at the 125% text step** (`components/settings/textScale.ts` — the scale that already produced the four-of-six-unreadable-rows defect **[SOURCE]** `figureTruncate.test.mjs:1-17`). |
| 3 | **Admin — create tenant / create user forms** | `components/admin/CreateTenantForm.tsx`, `CreateUserForm.tsx`, `BudgetForm.tsx`, `AccessDrawer.tsx` | Form labels + helper text + validation messages all at or below 13px, in a drawer with a fixed width. The one place a *narrower* face can hurt: labels that were tight against their field now float. | Same sweep; eyes on the drawer at 1440 — a label that no longer reads as attached to its input is the defect. |
| 4 | **Console run strip** | `components/console/DecisionStrip.tsx`, `RunStages.tsx`, `BudgetLine.tsx`, `WidthReceipt.tsx` | The money shot. `SCREEN · RETRIEVE · RERANK · GENERATE · GUARD` measures −4.80% **[MEASURED]**, so the strip gets *slacker*, and slack in a strip that was tuned to be tight reads as unfinished. Also carries `.eyebrow` at 10.88px and `Figure` numerals side by side — the two faces meeting. | `--portal ai_team`, section `console`; **watch a live run**, not a static shot: the strip's spacing is only wrong while it is filling. |
| 5 | **Guardrail firing line** | `components/guardrail/RailFiringLine.tsx` (672 lines, own `overflow-x-auto`) | Longest sustained mixed sans/mono content, at 18px `.t-title` — the −6.55% display case **[MEASURED]** — over rows that animate in. Checks Shape 2 and the `.t-title` bump together. | `--portal ai_team`, section `guardrails`, at all three widths; confirm the 19px `.t-title` reads as a title against 13px body. |

**And one non-screen:** the login page and the landing page (`components/landing/`) are the first
thing a jury sees and are the only place `.t-hero`'s `clamp(2.25rem → 3.5rem)` still lives
**[SOURCE]** `globals.css:322-327` — DESIGN.md §3 retired `.t-hero` from product screens
**[SOURCE]** `DESIGN.md:187`. Shoot them separately; a hero set in a face 5% shorter than the one it
was composed in is the most visible possible regression.

---

# B. GSAP, done safely

## B1. The constraint, proven rather than asserted

Aegis enforces reduced motion with **one global CSS rule** — `globals.css:642-650` — that zeroes
`animation-duration`, `animation-iteration-count` and `transition-duration` on `*`. **GSAP is
structurally unreachable by it.** Proven here, not reasoned about **[MEASURED]**:

A page carrying `globals.css:642-650` verbatim, one element animated by `@keyframes` and one by
`gsap.to(..., { x: 300, duration: 2 })`, loaded in Chromium under
`newContext({ reducedMotion: 'reduce' })`, sampled at t = 0.7s:

```
prefers-reduced-motion:no-preference   media-query matches=false
   CSS  @keyframes element translateX at t=0.7s : 104.5px
   GSAP tween element   translateX at t=0.7s : 105.5px

prefers-reduced-motion:reduce          media-query matches=true
   CSS  @keyframes element translateX at t=0.7s : 300.0px      ← killed, snapped to end
   GSAP tween element   translateX at t=0.7s : 105.0px      ← unchanged. still moving.
   GSAP inline style attribute:
     translate: none; rotate: none; scale: none; transform: translate3d(105px, 0px, 0px);
```

The CSS element snapped to its end state; **the GSAP element moved exactly as it did with reduced
motion off.** The last line is the mechanism: GSAP writes a `transform` into the element's **inline
`style` attribute** on every frame. `animation-duration` and `transition-duration` do not apply to
an inline transform being rewritten by JavaScript. There is no CSS rule — at any specificity, with
any number of `!important`s — that reaches it.

**So today the console has one reduced-motion boundary, expressed in one place, and adopting GSAP
puts a second class of motion entirely outside it.** Everything in the rest of Section B exists to
make that second class impossible to write incorrectly.

## B2. The sanctioned pattern

Every GSAP tween in this repo is written **inside `useGSAP`, inside a `gsap.matchMedia()` block with
an explicit `(prefers-reduced-motion: reduce)` conditional.** Both halves are load-bearing and they
solve different problems.

**`useGSAP` solves StrictMode.** `reactStrictMode: true` **[SOURCE]** `next.config.mjs:3`, so in
development React mounts, unmounts and remounts every component — effects run twice. A raw
`gsap.to()` in a `useEffect` leaves the first tween alive and running against a detached element,
and the two tweens fight over the same inline `transform`. `useGSAP` wraps the callback in a
`gsap.context()` and reverts it on cleanup — `useIsomorphicLayoutEffect(… return () =>
context.current.revert())` **[SOURCE]** `node_modules/@gsap/react/src/index.js:36-45`. It also
resolves `useLayoutEffect` to `useEffect` when `document` is undefined **[SOURCE]** `:13`, which is
what stops the SSR warning.

**`gsap.matchMedia()` solves reduced motion** — and unlike a `useReducedMotion()` boolean read once
at mount, it **reverts every tween created inside the block when the media query flips**, so a user
who turns reduced motion on mid-session gets the animations undone rather than merely not restarted.
`gsap.matchMedia` and `gsap.context` both exist in the installed 3.15.0 **[MEASURED]**
(`typeof gsap.matchMedia === 'function'`).

The pattern, verified end to end **[MEASURED]** (`opacity`/`y` sampled in Chromium at t=50/300/900ms
under both media states):

```
no-preference:  t=50ms opacity=0.20 y=8    t=300ms opacity=0.89 y=1.1   t=900ms opacity=1.00 y=0
reduce:         t=50ms opacity=1.00 y=0    t=300ms opacity=1.00 y=0     t=900ms opacity=1.00 y=0
```

Under `reduce` the element is at its **final state on the first sampled frame** — not snapped
partway, not faded quickly. That is the behaviour to reproduce, and the shape that produces it:

```tsx
'use client'
import { useGSAP } from '@gsap/react'
import gsap from 'gsap'
import { useRef } from 'react'

export function RevealGroup({ children }: { children: React.ReactNode }) {
  const scope = useRef<HTMLDivElement>(null)

  useGSAP(
    () => {
      const mm = gsap.matchMedia()
      mm.add(
        {
          reduce: '(prefers-reduced-motion: reduce)',
          ok: '(prefers-reduced-motion: no-preference)',
        },
        (ctx) => {
          const { reduce } = ctx.conditions as { reduce: boolean }
          // The reduce branch is not "do nothing" — it is "arrive". The CSS
          // initial state below hides the element, so skipping the tween would
          // leave it invisible for ever.
          if (reduce) {
            gsap.set('[data-reveal]', { opacity: 1, y: 0, clearProps: 'transform' })
            return
          }
          gsap.set('[data-reveal]', { opacity: 0, y: 10 })
          gsap.to('[data-reveal]', {
            opacity: 1, y: 0,
            duration: 0.32,           // --dur-slow
            ease: 'power2.out',       // the token curve, not a bounce
            stagger: 0.04,
          })
        },
      )
      return () => mm.revert()
    },
    { scope },      // scope confines every selector string to this subtree
  )

  return <div ref={scope}>{children}</div>
}
```

Four things in that block are non-negotiable and the test in B4 checks the first three:

1. **`useGSAP`**, never a bare `useEffect` — StrictMode-safe cleanup.
2. **`gsap.matchMedia()` with a `(prefers-reduced-motion: reduce)` conditional** — the reduced-motion
   boundary the CSS kill switch cannot provide.
3. **The `reduce` branch sets the final state**, it does not merely return. A `from()`-style tween
   plus a CSS initial state plus an early return is an element that never appears.
4. **`{ scope }`**, so a selector string cannot reach outside this component. Without it,
   `'[data-reveal]'` matches the whole document.

## B3. SSR — the flash, and the fix

Motion serialises `initial` into the server-rendered markup, so a Motion element arrives already in
its start state. **GSAP applies nothing until `useGSAP` runs after mount.** A `gsap.from({opacity:0})`
therefore renders at **full opacity in the SSR HTML**, paints, and then jumps to 0 to begin its
tween. That is a flash of the finished state, and on a slow first paint it is the *only* thing some
viewers see.

**The fix is a CSS initial state that the server can already carry, plus `gsap.set()` to re-assert
it, plus a reduced-motion escape in the same CSS.** In `globals.css`, alongside the existing
utilities:

```css
@layer utilities {
  /* The server-rendered start state for every GSAP entrance. GSAP re-asserts it
     with gsap.set() after mount; this exists so the markup is never briefly
     correct-looking before the tween takes over. */
  [data-reveal] {
    opacity: 0;
    transform: translateY(10px);
  }
  /* And the escape: a viewer who gets no tween at all — reduced motion, a JS
     failure, a hydration that never happens — must still see the content. The
     global kill switch at the end of this file cannot do this, because there is
     no animation here to kill. */
  @media (prefers-reduced-motion: reduce) {
    [data-reveal] {
      opacity: 1;
      transform: none;
    }
  }
}
```

**Use `gsap.set()` then `gsap.to()`, never `gsap.from()`.** `from()` reads the element's current
computed value as its destination, which makes the destination depend on whether CSS or a previous
tween happened to be applied first. `set()` + `to()` names both ends explicitly. The B4 test flags
`gsap.from` and `gsap.fromTo` as findings for this reason.

**One more SSR hazard worth naming rather than discovering:** `[data-reveal] { opacity: 0 }` in CSS
means that **if JavaScript fails, the content is invisible**. The reduced-motion escape above covers
one case; it does not cover a JS error. Restrict `[data-reveal]` to surfaces where the content is
also reachable another way, and **never put it on a whole page's primary content**. Section C's
placement list respects that.

## B4. The new test — `web/tests/design/gsapBoundary.test.mjs`

Without this the boundary decays in one phase. Someone writes a one-line `gsap.to()` in a
`useEffect` because it is faster, it works, nobody notices, and Aegis quietly has an accessibility
regression that no existing test can see — the CSS kill switch will keep passing every audit while
covering less and less of the motion in the product.

Written in the same style as `lightThemeOnly.test.mjs`: a **source scan** over `src/`, with an
**anti-vacuity floor**, offenders collected into an array and compared with `deepEqual` so the
failure message names every file and line.

```js
/**
 * GSAP is the one animation in this console that `prefers-reduced-motion` cannot reach,
 * and that has to be checkable.
 *
 * `globals.css:642-650` zeroes `animation-*` and `transition-*` on `*`. GSAP writes
 * `transform` into the element's inline `style` attribute on every frame, so that rule is
 * not merely weak against it — it is inapplicable. Measured: under
 * `reducedMotion: 'reduce'`, a `@keyframes` element snapped to its end state at t=0.7s
 * while a `gsap.to(…, {x:300, duration:2})` element sat at 105px, exactly where it sat
 * with reduced motion off.
 *
 * So every tween lives inside `useGSAP` (StrictMode is on — `next.config.mjs:3` — and a
 * raw useEffect tween survives the double-mount and fights its own second copy) AND
 * inside a `gsap.matchMedia()` block carrying a `(prefers-reduced-motion: reduce)`
 * conditional, which is the only reduced-motion boundary this class of animation has.
 *
 * The failure this closes is silent in every other kind of test: the animation works, the
 * page looks right, the CSS audit passes, and a viewer who asked for stillness gets none.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))

function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (/\.(tsx?|css)$/.test(entry)) found.push(path)
  }
  return found
}

/** Files that import gsap in any spelling. */
const IMPORTS_GSAP = /^\s*import\s[^\n]*\bfrom\s+['"](gsap|gsap\/[\w/-]+|@gsap\/react)['"]/m
/** A tween or timeline call, whatever the local alias for the core object is. */
const TWEEN = /\bgsap\s*\.\s*(to|from|fromTo|timeline|registerPlugin|quickTo|quickSetter)\b/

test('every file that animates with GSAP does it inside useGSAP and gsap.matchMedia', () => {
  const files = sources()
  // A scan whose subject can silently become empty proves nothing when it passes.
  assert.ok(files.length > 100, `the source scan came back near-empty (${files.length} files)`)

  const offenders = []
  let animating = 0

  for (const path of files) {
    const source = readFileSync(path, 'utf8')
    const where = path.slice(SRC.length)

    const imports = IMPORTS_GSAP.test(source)
    const tweens = TWEEN.test(source)
    if (!imports && !tweens) continue

    // A file may import gsap for a type or an ease and never tween. That is fine.
    if (!tweens) continue
    animating += 1

    if (!/\buseGSAP\s*\(/.test(source)) {
      offenders.push(`${where} — tweens outside useGSAP (StrictMode double-mount leaks it)`)
    }
    if (!/\bgsap\s*\.\s*matchMedia\s*\(/.test(source)) {
      offenders.push(`${where} — no gsap.matchMedia(): unreachable by the reduced-motion switch`)
    } else if (!/prefers-reduced-motion:\s*reduce/.test(source)) {
      offenders.push(`${where} — matchMedia with no (prefers-reduced-motion: reduce) conditional`)
    }
    // from()/fromTo() read the live computed value as an endpoint, which under SSR is the
    // element's FINAL state — the flash this pattern exists to prevent. set() + to().
    if (/\bgsap\s*\.\s*(from|fromTo)\s*\(/.test(source)) {
      offenders.push(`${where} — gsap.from/fromTo flashes its end state during SSR; use set() + to()`)
    }
    // A selector string with no scope reaches the whole document.
    if (!/\bscope\b/.test(source)) {
      offenders.push(`${where} — useGSAP without { scope }: selectors escape the component`)
    }
  }

  assert.deepEqual(
    offenders,
    [],
    'GSAP writes transform straight to element.style, so globals.css:642-650 cannot ' +
      'touch it. The sanctioned shape is useGSAP({ scope }) wrapping gsap.matchMedia() ' +
      'with a (prefers-reduced-motion: reduce) branch that SETS the final state.',
  )

  // The second way this file passes vacuously: GSAP gets removed, or the import spelling
  // changes, and the scan sweeps nothing while reporting success. Set this to the real
  // count on the day the first tween lands and let it fail loudly if it drops to zero.
  assert.ok(animating > 0, 'no file animates with GSAP — has the import spelling changed?')
})

test('the CSS initial state for a GSAP entrance always has a reduced-motion escape', () => {
  // The other half of the SSR fix. `[data-reveal] { opacity: 0 }` in the server markup is
  // what stops the flash — and it is also content that is invisible to anyone whose tween
  // never runs. Every such rule owes a `prefers-reduced-motion: reduce` counterpart.
  const css = readFileSync(join(SRC, 'app', 'globals.css'), 'utf8')
  const declared = [...css.matchAll(/\[data-reveal[^\]]*\]/g)].length
  assert.ok(declared >= 2, `expected a [data-reveal] rule AND its reduced-motion escape, found ${declared}`)
  const reduceBlocks = css.slice(css.indexOf('[data-reveal'))
  assert.match(
    reduceBlocks,
    /@media\s*\(prefers-reduced-motion:\s*reduce\)[\s\S]{0,200}\[data-reveal/,
    'a [data-reveal] initial state with no reduced-motion escape hides content for ever ' +
      'from anyone whose tween never runs',
  )
})
```

**Two honest limits of this test, which belong in its own header when it is written.** It is a
per-file scan, so a file that contains *both* a sanctioned `useGSAP`/`matchMedia` block *and* a
stray unsanctioned `gsap.to()` passes. Closing that needs an AST, and an AST test is a bigger
maintenance surface than the failure justifies today — the per-file gate stops the realistic
regression, which is a *new* file written the quick way. And it cannot see a tween built at runtime
from a string. Both are acceptable; neither should be discovered later and mistaken for a bug.

## B5. Where GSAP is registered, and what does not happen

- **No `gsap.registerPlugin` for anything not in the free core.** ScrollTrigger is free and is the
  one plugin worth considering later; `MorphSVGPlugin`, `DrawSVGPlugin`, `SplitText` and the rest
  present in `node_modules/gsap/` are **Club GSAP** members-only — the licence header in
  `@gsap/react/src/index.js:5-7` names *"the terms at gsap.com/standard-license or … the agreement
  issued with that membership"* **[SOURCE]**. Shipping one without a membership is a licensing
  problem in a product being demoed to a jury. **Adopt core only, and put that sentence in
  DESIGN.md.**
- **No `ScrollTrigger` in this phase.** DESIGN.md §6 already bans scroll-jacking and parallax
  **[SOURCE]** `DESIGN.md:255`, and that ban survives Section D unchanged.
- **`useGSAP.headless = true`** **[SOURCE]** `@gsap/react/src/index.js:48` — the hook does not
  require a registered window, so nothing extra is needed for the Node test environment.

---

# C. Where the animation goes — platform-wide, and restrained

## C1. The rule that keeps this from becoming slop

**Four motions exist. Every screen uses the ones that apply and invents nothing.** DESIGN.md §6
sets the budget at 4 **[SOURCE]** `DESIGN.md:250`; this keeps that number and spends it on *four
named motions* rather than four per screen. A fifth effect is a DESIGN.md change, not a
component-level decision.

| # | Name | What it is | Engine | Where |
|---|---|---|---|---|
| M1 | **Arrive** | Content enters once: `opacity 0→1`, `translateY 10→0`, `--dur-slow` (320ms), `--ease-out`, `stagger 40ms`, capped at 6 items. | **GSAP** (`useGSAP` + `matchMedia`) | Page-level section groups and card grids, on first mount only |
| M2 | **Settle** | A value that changed draws attention without moving: a 200ms tint wash on the row/cell, no transform. | **CSS** (existing `--dur-base` + `--ease-out`) | Tables and tiles whose numbers update live |
| M3 | **Beat** | The console heartbeat — already built. One pulse per wire event, keyed on `beat.seq`. | **CSS** `.animate-pip` / `.animate-beat` / `.animate-mark-core`, gated by `useReducedMotion()` | Console only. **Do not extend it.** |
| M4 | **Trace** | A row that just arrived slides in from the left, 280ms. Already built. | **CSS** `.animate-trace-in` / `.animate-reveal` | Streaming lists: trace rows, firing-line rows |

**Only M1 is new, and only M1 is GSAP.** M2 is a CSS transition. M3 and M4 exist and work. That is
the whole of "platform-wide but restrained": one new motion, applied consistently, replacing the
per-screen improvisation that 41 existing call sites across 14 keyframes are already flirting with.

**GSAP's job is exactly one thing: orchestration.** A staggered, interruptible, properly-cleaned-up
entrance across a group of sibling elements is the thing CSS `animation-delay` does badly (every
element needs its own delay written by hand, nothing can be reverted, and a re-render restarts them
all) and the thing GSAP does well. If a proposed use of GSAP is not orchestration across siblings,
it is a CSS transition and should be written as one.

## C2. What gets nothing. Explicitly.

This list is the deliverable of Section C, more than the one above.

- **Every table body.** Corpus, routing, stack versions, schema map, ingest log, seats, audit log,
  backtest. A table row does not animate in. M2 (a tint on change) is the only motion a table may
  have, and only where the value genuinely changes live.
- **Every form and drawer.** `CreateTenantForm`, `CreateUserForm`, `BudgetForm`, `AccessDrawer`,
  `ComposerMenu`, `ModelsMenu`, `ModeMenu`. A menu opens; it does not perform.
- **Every admin screen's content region.** M1 applies to the *page section headers* on admin
  screens, not to the data inside them. An operator who opens the roles screen four times an hour
  must not watch a stagger four times an hour.
- **The settings screens.** Nothing.
- **Every governance figure.** DESIGN.md §6 already bans counting-up on a spend cap — *"a spend cap
  that animates looks approximate"* **[SOURCE]** `DESIGN.md:254`. That ban now explicitly covers
  GSAP: no `gsap.to(obj, { value: n, onUpdate })` counters, anywhere, on any number.
- **Any surface that repeats.** M1 fires on **first mount only**, never on a data refresh, never on
  a filter change, never on a tab switch. A staggered entrance that replays every time a poll
  returns is the single fastest way to make this look like a template.

## C3. The console and guardrails screens are finished. Do not touch them.

Both were animated this session and both work. Concretely, do not modify:
`console/RunMark.tsx`, `console/RunStages.tsx`, `console/LaneBoard.tsx`, `console/ChatConsole.tsx`,
`console/FlowCanvas.tsx`, `console/useRevealedText.ts`, `console/motion.ts`,
`guardrail/RailFiringLine.tsx`, `jobs/PipelineIso.tsx`.

**And copy their idiom rather than replacing it.** `RunMark.tsx:114` reads
`useReducedMotion() ?? false` once and then gates a CSS class **[SOURCE]** `:142, :150, :167`;
`RailFiringLine.tsx:195, 236, 267` does the same **[SOURCE]**. That idiom is *better* than GSAP
wherever the animation is a single element's own loop, because it stays inside the
`globals.css:642` kill switch — which is a boundary that cannot be forgotten, whereas GSAP's has to
be written out every time. **Prefer the existing idiom; reach for GSAP only for M1.**

One thing worth stealing from `RunMark.tsx:60-73` **[SOURCE]** and applying to every M1 surface:
*every state is legible without motion.* Motion restates a fact; it is never the fact. If removing
M1 from a screen loses information, M1 was doing work it should not have been doing.

---

# D. DESIGN.md — the sections that change, with replacement text

`/Users/yrevash/aegis/DESIGN.md`, 407 lines. Five edits.

## D1. §3 Type — `DESIGN.md:163-166`

**Current [SOURCE]:**

> ## 3. Type
>
> Inter for interface, **JetBrains Mono for every numeral, id, cost, count and timestamp** via the
> `Figure` primitive, so columns align and figures do not reflow as they tick.

**Replacement:**

> ## 3. Type
>
> **IBM Plex Sans for interface and for display**, **JetBrains Mono for every numeral, id, cost,
> count and timestamp** via the `Figure` primitive, so columns align and figures do not reflow as
> they tick.
>
> The display voice is **weight and tracking, not a second face**. `--font-display` and
> `--font-sans` name the same family; the token stays so a display face can return later without
> touching a call site.
>
> **Why the faces changed.** Inter and Space Grotesk are both on Impeccable's `overused-font` list
> — the set is `inter, roboto, open sans, lato, montserrat, arial, helvetica, fraunces, instrument
> sans, instrument serif, geist, geist sans, geist mono, mona sans, plus jakarta sans, space
> grotesk, recoleta` — and the detector flagged `layout.tsx:42` for it. IBM Plex is an IBM-commissioned
> superfamily under the SIL Open Font License with a real institutional voice, and neither Plex Sans
> nor JetBrains Mono is on that list.
>
> **Two measured facts that govern every layout decision under this face.** IBM Plex Sans is
> **4.75% narrower** than Inter on this product's own strings at 13px — no string measured wider —
> so nothing can overflow from the change alone. And it has a **5.5% smaller x-height** (51.60 vs
> 54.59 at 100px), so it *reads* smaller at the same declared size. **Do not compensate with size.**
> Compensating with a ~5% size bump gives back exactly the width that was won, and that is what
> would break the dense tables. Compensate with line-height, tracking and ink. The one sanctioned
> size change is `.t-title` 18px → 19px, which still lands narrower than Space Grotesk did.
>
> **JetBrains Mono stays.** Its advance is identical to IBM Plex Mono's — both exactly 0.6000em, so
> a swap would move no column — while its x-height is 55.00 against Plex Mono's 51.60. Aegis sets
> mono at 10.88px and 11.52px across 324 call sites; 6% of apparent size is not available there.
> Family coherence is not worth legibility on the numbers a jury reads.

Then, in the size ramp block at `DESIGN.md:170-177`, change `title 16/22` to `title 19/26` and add a
line under the block: *"`.t-title` is 19px under IBM Plex Sans, where it was 18px under Space
Grotesk; measured, Plex at 19/600 still sets narrower than Space Grotesk at 18/600 on every product
title tested."*

## D2. §6 Motion — `DESIGN.md:248-256`

**Current [SOURCE]:**

> ## 6. Motion
>
> Budget **4** (was 2). Motion confirms a state change, reveals structure, or shows work happening.
>
> - `--dur-fast 120ms` hover/focus · `--dur-base 200ms` enter/exit · spring on the flow graph.
> - Card entrance stagger on a page load. Real streaming feel on the console.
> - **No counting-up on a governance figure** — a spend cap that animates looks approximate.
> - No scroll-jacking, no parallax, no infinite ambient loops on operator screens.
> - `prefers-reduced-motion` respected on every animated element, including the 3D.

**Replacement:**

> ## 6. Motion
>
> Budget **4**, and it is now four *named* motions used everywhere rather than four per screen:
> **Arrive** (a group enters once — GSAP, staggered), **Settle** (a changed value takes a 200ms
> tint, no transform — CSS), **Beat** (the console heartbeat, one pulse per wire event — CSS,
> console only), **Trace** (a just-arrived row slides in — CSS). A fifth effect is a change to this
> file, not a component-level decision.
>
> - `--dur-fast 120ms` hover/focus · `--dur-base 200ms` enter/exit · `--dur-slow 320ms` Arrive ·
>   spring on the flow graph.
> - **Arrive fires on first mount only.** Never on a refresh, a filter, a tab switch or a poll.
> - **No counting-up on a governance figure** — a spend cap that animates looks approximate. This
>   covers `gsap.to(obj, { onUpdate })` counters as well as CSS ones.
> - No scroll-jacking, no parallax, no infinite ambient loops on operator screens. **No
>   ScrollTrigger.**
> - Tables, forms, drawers, menus and the settings screens get **nothing**. Admin screens get
>   Arrive on section headers, never on the data inside them.
> - `prefers-reduced-motion` respected on every animated element, including the 3D.
>
> ### GSAP is outside the CSS reduced-motion switch, and that is a rule, not a caveat
>
> `globals.css:642-650` zeroes `animation-*` and `transition-*` on `*`, and it is the reason
> "reduced motion is respected" has been true here without anyone having to remember it. **GSAP
> writes `transform` into the inline `style` attribute frame by frame, so that rule cannot reach
> it.** Measured under `prefers-reduced-motion: reduce`: a `@keyframes` element snapped to its end
> state at t=0.7s while a GSAP-tweened element sat mid-flight at 105px — the same place it sat with
> reduced motion off.
>
> So **every GSAP tween lives inside `useGSAP({ scope })` inside a `gsap.matchMedia()` block with a
> `(prefers-reduced-motion: reduce)` conditional whose branch SETS the final state**, and
> `web/tests/design/gsapBoundary.test.mjs` fails the build on any file that animates outside that
> shape. Use `gsap.set()` + `gsap.to()`, never `from()`/`fromTo()`: `from()` reads the live computed
> value as an endpoint, which under SSR is the element's finished state, and the result is a flash.
>
> **Prefer the CSS idiom.** Where an animation is one element's own loop, gate a CSS `.animate-*`
> class on `useReducedMotion()` — the shape `console/RunMark.tsx` and `guardrail/RailFiringLine.tsx`
> already use. It stays inside the global switch, which is a boundary nobody can forget. GSAP is for
> **orchestration across siblings** and nothing else.
>
> **Club GSAP plugins are not licensed here.** Core only. `MorphSVGPlugin`, `DrawSVGPlugin`,
> `SplitText` and the rest are members-only despite being present in `node_modules`.

## D3. §7 — the bundle-cost paragraph, and the honest note this plan owes

The `+305 kB` claim is at `DESIGN.md:294` **[SOURCE]**, in the §7 technique table:

> | **Knowledge graph** | **See the note below** | `react-force-graph-3d` is **+305 kB gzip** over
> the 2D build already shipped, and the migration is **not a drop-in**. |

and its neighbours at `:291-296` reject WebGL at 235–236 kB and Spline at 544 kB **[SOURCE]**.

**That table is not amended — those rejections stand, and none of them was about GSAP.** What is
added, immediately after the table, is the honest note:

> **A second animation library now ships, and that sits uneasily with the paragraphs above.**
> Measured on the installed packages: `gsap/dist/gsap.min.js` is **28,356 bytes gzip**, and
> `motion/dist/motion.js` is **46,441 bytes gzip** un-tree-shaken. Neither is close to the 235 kB
> and 305 kB figures that got WebGL and `react-force-graph-3d` rejected, so this is not that
> decision being reversed — but the *reasoning* above is "one way to do a thing, and the cheapest
> one", and shipping two animation engines is not that.
>
> **It is done anyway, by the owner's decision, and the decision is recorded here rather than
> rationalised away.** What the code owes in return: GSAP is used for exactly one motion (Arrive,
> §6), imported only from `gsap` core, and Motion is not extended to new surfaces. Four of the seven
> files that currently import Motion import it *only* for `useReducedMotion` — a 46 kB dependency
> used as a `matchMedia` wrapper. If a future phase wants the bundle back, **that** is the thread to
> pull, not GSAP.

## D4. §9 Anti-slop — `DESIGN.md:390-395`

The list already bans *"animation for its own sake"* **[SOURCE]** `:393`. Append three entries that
name the specific slop a second animation library makes newly available:

> · **a different entrance effect per screen** — there are four named motions (§6) and no fifth ·
> **an entrance that replays on every data refresh** · **a number that counts up** ·
> **a thick coloured border on one side of a card** (Impeccable calls this `side-tab` and describes
> it as *"the most recognizable tell of AI-generated UIs"*) · **functional text below 11px**

## D5. §10 Workflow and conflict order — `DESIGN.md:396-404`

**Current [SOURCE]:**

> Inspect the code → read this file → `redesign-existing-projects` if the screen exists → taste
> anti-slop pass → implement → check 390 / 834 / 1440 / 1920 → run `/web-interface-guidelines` →
> fix findings → final review.
>
> **Conflict order:** existing functionality > this file > taste principles > Vercel usability.
> Accessibility and correctness always win.

**Replacement:**

> Inspect the code → read this file → `redesign-existing-projects` if the screen exists → taste
> anti-slop pass → implement → `npx tsc --noEmit` → `npm test` → `node scripts/shoot.mjs` at
> 390 / 834 / 1440 / 1920 → **`npx impeccable detect src`** → run `/web-interface-guidelines` →
> fix findings → final review.
>
> **Impeccable is a read-only detector, run by a human or by CI. It is never installed as an agent
> hook** — a design detector that edits code is a second author with no taste and no context.
> Exit `0` is clean, exit `1` is a tool failure, **exit `2` means findings were reported**. Rules
> Aegis genuinely cares about get promoted into `web/tests/design/` as native tests, because a
> finding that only appears when someone remembers to run a detector is not enforced.
>
> **Conflict order:** existing functionality > accessibility and correctness > this file > taste
> principles > Vercel usability > Impeccable findings.

Note the conflict order changed in one way beyond appending Impeccable: *accessibility and
correctness* moves from a trailing sentence into the ordering itself. The trailing form —
*"Accessibility and correctness always win"* — is true but unranked, and this plan introduces a
class of animation where accessibility and "existing functionality" can genuinely conflict (an
`opacity: 0` initial state is content that is invisible when JS fails). Ranking it makes the answer
readable off the list.

---

# E. Impeccable in the workflow

## E1. How it is run

```
cd /Users/yrevash/aegis/web
npx impeccable detect src --json
```

**Verified today [MEASURED]:** `impeccable@3.6.0`, **exit 2**, **3 findings**, all `severity:
"warning"`, `category: "slop"` — and the JSON is a flat array of objects carrying
`antipattern, name, description, severity, category, file, line, snippet`, plus `importedBy` where
the tool can resolve it.

**Exit codes.** `0` clean · `1` the tool itself failed · **`2` findings were reported**. That
distinction matters for a gate: `if [ $? -eq 1 ]` is a broken toolchain and must not be confused
with `2`, which is a design finding a human decides about. **Do not wire `detect` into a
non-interactive gate that treats 2 as fatal** — several of the 59 rules are matters of taste, and a
build that refuses to compile over an em-dash is a build people learn to bypass.

**Two flags worth knowing.** `--scope type,layout` narrows to a design domain. `--no-advisory`
suppresses advisory rules, which are *"detected and listed in a separate section, but never counted
as failures and never changing the exit code"* **[DOC]** `impeccable detect --help`. Aegis's
`DESIGN.md` is read automatically for the `design-system-*` rules unless `--no-design-system` is
passed **[DOC]** — which is a reason to keep DESIGN.md's type and colour sections precise after the
Section D edits.

**And one thing that is not obvious and would otherwise be discovered wrongly.** `detect src` is a
**regex scan over source files** — *"Non-HTML files: regex pattern matching (CSS, JSX, TSX, etc.)"*
**[DOC]**. The layout- and legibility-class rules (`tiny-text`, `undersized-ui-text`,
`low-contrast`, `line-length`, `text-overflow`, `nested-cards`) live in the **browser-injected**
engine **[SOURCE]** `impeccable/cli/engine/rules/checks.mjs` and need a *URL* scan against a running
dev server. **A clean `detect src` therefore proves much less than it looks like it does.**

## E2. The three findings, and what to do with each

| Finding | Location | Verdict |
|---|---|---|
| `overused-font` — *"Google Fonts: inter"* | `src/app/layout.tsx:42` **[MEASURED]** | **Fixed by Section A.** IBM Plex Sans and JetBrains Mono are both absent from `OVERUSED_FONTS` **[SOURCE]** `constants.mjs:23-31`, so the finding clears for both the body and the display face at once. |
| `side-tab` — *"borderRight: `6px solid"* | `src/components/client/RiskDumbbell.tsx:237` **[MEASURED]**, `importedBy: ["RiskMap.tsx"]` | **A false positive — waive it inline, do not "fix" it.** Read `:233-241`: it is `borderTop: 5px solid transparent; borderBottom: 5px solid transparent; borderRight: 6px solid …` on a `size-0` span — **the CSS triangle idiom**, drawing an arrowhead on a dumbbell chart. It is not a card accent and there is no card. Waive with the tool's own mechanism: `// impeccable-disable-next-line side-tab: CSS triangle, not a card accent` **[DOC]**. |
| `side-tab` — *"border-l-2"* | `src/components/compliance/ComplianceView.tsx:141` **[MEASURED]** | **A real finding. Fix it.** `:139-145` is a `<p>` carrying `border-l-2 py-1 pl-3` with `border-risk` or `border-border` — the gap text on a compliance control. This is exactly the pattern the rule names. The Aegis-native replacement already exists: DESIGN.md §4 says a state is *"a glyph, a word and a hue — all three, never a hue alone"* **[SOURCE]** `DESIGN.md:230`, and the system has `Badge` for it. Replace the left border with a `Badge` on the gap line. |

**Note what the detector did *not* find.** The undersized text at `PipelineIso.tsx:466` and `:483`
(`text-[10px]`) and `:539` (`text-[9px]`) **[MEASURED]** was **not reported**, for two compounding
reasons: `undersized-ui-text` is a browser-engine rule that a source scan never runs, *and* even on a
URL scan its exempt-context selector includes `svg` and `[aria-hidden="true"]` **[SOURCE]**
`checks.mjs:3412`, and all three of those are `<text>` inside the pipeline SVG. **Impeccable would
never flag them, on any invocation.** That is the argument for E3 by itself.

## E3. Which of the 59 rules become native Aegis tests

**59 rules confirmed [MEASURED]** — `ANTIPATTERNS.length === 59` in
`impeccable/cli/engine/registry/antipatterns.mjs`. Three become Aegis tests. The criterion is not
"which rules are good" — it is **which rules encode a failure this codebase has actually shipped,
and which a source scan can see.**

### E3a. `web/tests/design/textFloor.test.mjs` — the 11px functional-text floor

**Adopt the threshold, reject the exemptions.** Impeccable's own rule text is the right rationale
**[SOURCE]** `checks.mjs:3378-3392`:

> *This rule targets exactly that blind spot: the interactive and short content-bearing text — nav
> items, buttons, labels, table cells, meta rows, timecodes — shipped below an 11px floor. The live
> failure it closes: a build shipped its entire furniture layer at 8px, and the design hook waved it
> through because 8px had been added to the DESIGN.md size ramp. **Being on the ramp is a token
> argument, not a legibility one**, so this rule ignores the design system entirely.*

`const floor = (!isInteractive && isSmallprint) ? 10 : 11` **[SOURCE]** `checks.mjs:3420`.

The Aegis test is a **source scan** for `text-[Npx]` and `text-[N.NNrem]` utilities resolving below
11px, over `src/**/*.tsx`. It **does not inherit the `svg` and `[aria-hidden]` exemptions**, because
Aegis's smallest text lives inside SVGs and is real functional content: `PipelineIso.tsx:466` is the
**stage name** on a pipeline diagram. An exemption written for decorative vector art is wrong here.

Current known offenders, to be fixed as part of adopting it **[MEASURED]**:
`jobs/PipelineIso.tsx:466` `text-[10px]`, `:483` `text-[10px]`, `:539` `text-[9px]` — and those are
the *only* three under `src/`, so the fix is bounded. All three go to `text-[11px]`; the diagram has
room (the labels sit in an isometric SVG whose viewBox scales).

Anti-vacuity floor, in the house style: assert the `.tsx` scan found > 50 files, **and** assert that
the scan sees at least one `text-[...]` utility at all — otherwise a change to the arbitrary-value
syntax would silently sweep nothing.

**Also assert the floor against the token ramp**, so the "it's on the ramp" argument cannot come
back: `--font-size-*` and the `.t-*` utilities in `globals.css` must all resolve ≥ 11px. `.eyebrow`
at `0.68rem` = **10.88px** **[SOURCE]** `globals.css:296` is **already under the floor by 0.12px**.
That is a real decision to take rather than an oversight to inherit: either `.eyebrow` moves to
`0.6875rem` (11px), or the test carries an explicit, commented exception naming the one utility and
saying why. **Recommend moving it to 11px** — the migration is already touching apparent size, and
an exception in the first version of a floor test is how a floor test becomes advisory.

### E3b. `web/tests/design/fontDeclarations.test.mjs` — literal hex and undeclared font

**Two scans in one file**, both of which a source scan genuinely can do.

**The undeclared-font half** is Impeccable's `design-system-font` — *"A font is used that is not
declared in DESIGN.md typography"* **[SOURCE]** `antipatterns.mjs`. The Aegis version is stronger
and cheaper: **assert that `font-family` appears in `globals.css` and nowhere else under `src/`**,
and that the three `--font-*` tokens name exactly the two families DESIGN.md §3 declares. This is
the test that makes the Section A migration *stick* — after it, a component that hard-codes `Inter`
fails the build rather than quietly reintroducing the face the migration removed.

**The literal-hex half** is Impeccable's `design-system-color`, which is **advisory in Impeccable**
— `isAdvisoryRule('design-system-color') === true` **[MEASURED]** — and therefore never changes its
exit code. **In Aegis it should not be advisory**, because `oneRamp.test.mjs` already establishes
that an off-system colour here fails *silently* rather than loudly: `globals.css:209` closes the
blue namespace, so `bg-blue-500` paints **nothing at all** **[SOURCE]** `oneRamp.test.mjs:14-17`.
The new scan covers the case `oneRamp` cannot: a raw `#rrggbb` or `rgb()` literal written in a
`style={{}}` or a chart config, which bypasses the token system entirely rather than resolving to
nothing.

Allowlist, and keep it short and commented: the shadow inks in `globals.css:307-318` (Stripe's
blue-black, deliberately not pure black **[SOURCE]** `:303-306`) and the token declarations
themselves. Everything else names a token.

### E3c. `nested-cards` — adopt as a native test

*"Cards inside cards create visual noise and excessive depth. Flatten the hierarchy — use spacing,
typography, and dividers instead of nesting containers."* **[SOURCE]** `antipatterns.mjs`.

This is a source-scannable rule in Aegis specifically because the codebase has **one** `Card`
primitive with a `data-slot` contract — `globals.css:255-257` keys a base rule off
`[data-slot='card-header'] + [data-slot='card-body']` **[SOURCE]**. So the test is: no `<Card>` JSX
element appears inside another `<Card>`'s children in the same file, using the same tag-matching
approach `figureTruncate.test.mjs` already uses for `<Figure>` **[SOURCE]** `:38-53`. It also
directly serves DESIGN.md §9's existing *"excess cards"* ban **[SOURCE]** `:391` — which is
currently a principle with no enforcement, i.e. exactly the kind of standing rule
`lightThemeOnly.test.mjs`'s header says *"decays quietly"* **[SOURCE]** `:4`.

### E3d. What is deliberately NOT adopted, and why

- **`overused-font`** — a one-time migration, not a standing risk, and E3b's stronger
  undeclared-font scan supersedes it.
- **`side-tab`** — one of the two live findings is a false positive on a CSS triangle. A rule with a
  50% false-positive rate on this codebase's actual matches would be trained away in a week.
  Impeccable keeps it; Aegis does not adopt it. §9's prose ban (D4) is the right weight.
- **`low-contrast`** — `badgeContrast.test.mjs` already does this *better*: it recomputes the ratio
  from the live token values and the live `SIGNALS` map, and composites the alpha wash, so it fails
  on legibility and stays quiet when a hue is legitimately re-tuned **[SOURCE]** `:16-18`.
- **`pulsing-dot`** — Aegis's `.animate-pip` is exactly this shape at 11 call sites, and it is
  correct here: the rule itself allows it, *"Reserve pulse animation for indicators tied to
  genuinely live, changing data"* **[SOURCE]** `antipatterns.mjs`, and `RunStages.tsx:169` gates it
  on `stage.running` **[SOURCE]**. Adopting the rule would mean fighting it forever.
- **`em-dash-overuse`, `marketing-buzzword`, `aphoristic-cadence`, `theater-slop-phrase`** — prose
  rules on a codebase whose comments are deliberately discursive. Leave them to the detector, and
  read them as advice.

---

# MANDATORY VERIFICATION

## V1. The commands, in order, with today's baselines

Run from `/Users/yrevash/aegis/web` unless stated.

| # | Command | Baseline today | Pass condition after |
|---|---|---|---|
| 1 | `npx tsc --noEmit` | exit **0**, no diagnostics **[MEASURED]** | exit 0 |
| 2 | `npm test` | **399 passed, 0 failed** **[MEASURED]** | **≥ 402 passed, 0 failed** — 399 + `gsapBoundary` (2 tests) + `textFloor` + `fontDeclarations` + `nestedCards`. A count that did not go up means a new test file was not picked up by the `tests/**/*.test.mjs` glob **[SOURCE]** `package.json` `"test"`. |
| 3 | `node scripts/shoot.mjs --portal <p>` for all 5 portals | see V2 | exit 0 and `problems.json` empty for every portal |
| 4 | `npx impeccable detect src --json` | exit **2**, 3 findings **[MEASURED]** | exit **0**, 0 findings — after the §A font swap clears `overused-font`, the `ComplianceView` badge replaces `border-l-2`, and `RiskDumbbell` carries its inline waiver |
| 5 | `npx impeccable detect http://localhost:3001/app/ai_team/jobs --viewport 390x844` | **not run today** | run it once per high-risk screen from A3; this is the only invocation that exercises the browser-engine rules, and the only one that can see contrast and layout findings |

**Ports, and this has cost two lanes already [SOURCE]** `scripts/shoot.mjs:30-34`: *"the dev server
is `:3001` and the backend is **`:8110`**, not `:8000`. Two implementation lanes checked `:8000`,
concluded no backend was running, and skipped their responsive verification entirely — while both
services were up the whole time."*

**And never `next build` while a dev server is up [SOURCE]** `next.config.mjs` `distDir` comment:
use `AEGIS_DIST_DIR=.next-verify npx next build`, or the build deletes the chunks the running dev
server is still serving.

## V2. The per-screen browser checklist

`shoot.mjs` asserts three things per screen per width, independently of anything an implementer says
**[SOURCE]** `:12-22`: no horizontal body overflow (`documentElement.scrollWidth === innerWidth`),
no console errors, and that the screen actually rendered rather than being a blank shell behind a
dead backend. Widths are `[390, 834, 1440, 1920]` **[SOURCE]** `:41`. It picks its own account per
portal — `--portal platform_admin` with no `--user` used to sign in as an ai_team account and shoot
the wrong portal four times while reporting "0 problems" **[SOURCE]** `:60-68`.

```
node scripts/shoot.mjs --portal platform_admin     # admin: 12 sections
node scripts/shoot.mjs --portal tenant_admin       # 13 sections
node scripts/shoot.mjs --portal ai_team            # 16 sections — console, jobs, guardrails
node scripts/shoot.mjs --portal devops             # 9 sections
node scripts/shoot.mjs --portal client             # 11 sections
```

**Automation cannot see the failure this change actually causes**, so these must be re-shot **and
looked at** at 390 / 834 / 1440:

| Screen | Route | 390 | 834 | 1440 | What you are looking for that `shoot.mjs` cannot see |
|---|---|---|---|---|---|
| **Corpus table** | `/app/ai_team/jobs` | ✓ | ✓ | ✓ | Filenames still ellipsize (`Figure` `truncate` **prop**, never the class); the mono id column still readable at 11.52px |
| **Jobs pipeline SVG** | `/app/ai_team/jobs` | ✓ | ✓ | ✓ | The three `text-[10px]`/`text-[9px]` labels at their new 11px — do they still fit the isometric blocks? |
| **Roles & access** | `/app/platform_admin/roles` | ✓ | ✓ | ✓ | 13px/500 labels at Plex's smaller x-height. **Also at the 125% text step.** |
| **Admin forms / drawer** | `/app/platform_admin/dashboard` → create tenant, create user | ✓ | — | ✓ | A label that no longer reads as attached to its input |
| **Console run strip** | `/app/ai_team/console` | ✓ | ✓ | ✓ | **Watch a live run.** Strip spacing is only wrong while filling. `.eyebrow` at its new tracking next to `Figure` numerals. |
| **Guardrail firing line** | `/app/ai_team/guardrails` | ✓ | ✓ | ✓ | `.t-title` at 19px still reads as a title against 13px body |
| **Landing + login** | `/`, `/login` | ✓ | ✓ | ✓ | `.t-hero` `clamp(2.25rem → 3.5rem)` in a face 5% shorter than the one it was composed in — the most visible possible regression |
| **Client dashboard** | `/app/client/dashboard` | ✓ | ✓ | ✓ | `RiskDumbbell`'s CSS-triangle arrowheads still render after the inline waiver comment is added |
| **Compliance** | `/app/tenant_admin/documents` → compliance | ✓ | — | ✓ | The `Badge` that replaced `border-l-2` still says which controls are partial |

Plus one screen with **nothing to do with fonts**, shot as a control: `/app/devops/stack`. If it
changed, something global moved that was not supposed to.

## V3. Verifying that reduced motion actually suppresses every GSAP tween

**The test in B4 checks the shape of the code. This checks the behaviour.** Both are needed: the
test cannot see a tween built at runtime, and the browser check cannot see a file nobody navigated
to.

**Three layers, in increasing cost.**

**Layer 1 — the source test.** `npm test` runs `gsapBoundary.test.mjs`. Cost: zero. Catches: a new
file written the quick way. This is the layer that stops the decay.

**Layer 2 — a Playwright assertion, added to `scripts/shoot.mjs`.** The harness already drives
Playwright; a second context with `reducedMotion: 'reduce'` costs one browser launch. **The
assertion, and it must be this one:**

> Load the screen under `reducedMotion: 'reduce'`. Sample every element's inline `style` attribute
> at t=100ms and again at t=800ms. **No element's inline `transform` or `opacity` may differ between
> the two samples.**

That is the exact discriminator the B1 measurement established: a suppressed GSAP tween leaves the
inline style *static*, while a running one rewrites it every frame. It does not depend on knowing
which elements are animated, which is what makes it survive new screens.

```js
const inlineMotion = () => page.evaluate(() =>
  [...document.querySelectorAll('[style]')]
    .map((el) => `${el.tagName}#${el.id}.${el.className}|${el.style.transform}|${el.style.opacity}`)
    .join('\n'))
const t100 = await (page.waitForTimeout(100), inlineMotion())
const t800 = await (page.waitForTimeout(700), inlineMotion())
// Under reduce, GSAP must have settled before the first sample and never moved again.
assert.equal(t100, t800, 'an inline transform/opacity changed under prefers-reduced-motion: reduce')
```

**Layer 3 — the one-time manual check, per new M1 surface.** Chrome DevTools → Rendering →
*Emulate CSS media feature prefers-reduced-motion: reduce*, hard reload, and confirm the content is
**already in its final state on first paint** — not that it animates fast, not that it fades. The
B2 measurement is the reference: `t=50ms opacity=1.00 y=0`. Anything else means the `reduce` branch
returned early instead of calling `gsap.set()`, and the content is arriving by accident.

**One trap to check explicitly at Layer 3.** Toggle reduced motion **on while a page is already
open**. `gsap.matchMedia()` reverts its tweens when the query flips; a `useReducedMotion()` boolean
read once at mount does not. If content stays mid-tween after the toggle, `matchMedia` was not
actually used — the file passed the B4 scan by containing the string somewhere it does not govern.

---

## What "done" means

- [ ] `globals.css:165-167` names IBM Plex Sans twice and JetBrains Mono once; `layout.tsx:42`
      requests IBM Plex Sans + JetBrains Mono and no Space Grotesk.
- [ ] `npx tsc --noEmit` exits 0; `npm test` reports **≥ 402 passing, 0 failing**.
- [ ] `gsapBoundary.test.mjs` exists, has both anti-vacuity floors, and **fails** when a
      `gsap.to()` is deliberately written outside `useGSAP` — verified by writing one and watching
      it fail before deleting it. A guard nobody has seen fail is a guard nobody knows works.
- [ ] `npx impeccable detect src` exits **0** with **0 findings**.
- [ ] The `PipelineIso` 10px/9px labels are at 11px, and `.eyebrow` is at 11px or carries a written,
      named exception.
- [ ] All five portals shoot clean at 390/834/1440/1920, and the nine screens in V2 have been looked
      at by a person at 390 and at the 125% text step.
- [ ] Under `prefers-reduced-motion: reduce`, no inline `transform` or `opacity` changes between
      t=100ms and t=800ms on any shot screen.
- [ ] DESIGN.md §3, §6, §7-note, §9 and §10 carry the replacement text in Section D — including the
      sentence saying plainly that two animation libraries ship and that this sits uneasily with §7.
- [ ] No file under `console/` or `guardrail/` in the C3 list was modified.

## Risks, stated plainly

1. **The apparent-size loss, and the wrong instinct about it.** IBM Plex Sans reads 5.5% smaller
   than Inter at the same declared size. The instinct is to bump sizes ~5%, which gives back exactly
   the width the narrower face won and *then* breaks the dense tables — turning a change that cannot
   overflow into one that does. **This is the biggest risk in the plan.** Compensate with
   line-height, tracking and ink; the single sanctioned size change is `.t-title` 18 → 19px.
2. **The GSAP reduced-motion boundary decays without the B4 test.** It is one line of convenience
   away, it works when written wrongly, and no existing test can see it. Ship the test in the same
   commit as the first tween — not after.
3. **A green `shoot.mjs` sweep will not validate this migration.** The face is narrower, so nothing
   overflows, so the automation passes. Section V2's per-screen eyes are the actual verification and
   are not optional.
4. **`detect src` is a regex scan.** Every legibility, contrast and layout rule needs a URL scan
   against `:3001`. A clean source scan is much weaker evidence than it appears.
5. **The `<link>` font load gets a worse FOUT under a narrower face.** Mitigate with `size-adjust`
   fallback metrics; escalate to self-hosted woff2 with `font-display: optional` only on a
   measurement at rehearsal.
6. **`[data-reveal] { opacity: 0 }` hides content if JavaScript fails.** The reduced-motion escape
   covers one case and not that one. Never put it on a page's primary content.
7. **Club GSAP plugins are present in `node_modules` and are not licensed.** Core only, and the
   sentence belongs in DESIGN.md where someone will read it before reaching for `SplitText`.
