---
version: 2
name: Aegis design system
description: >
  An enterprise governance console that is meant to be looked at. Soft white cards floating on a
  light blue canvas, a deep navy rail, one dominant blue, big confident numerals, and restrained
  3D where it earns its place. Replaces v1, whose "borders not shadows, 6px, no gradients"
  austerity produced a flat wireframe.
---

# Aegis design system

**Design read:** a multi-tenant AI governance console for platform operators, tenant
administrators and a hackathon jury — with a clean, premium, enterprise language: floating
surfaces, generous rounding, one blue, and numbers as the hero.

## Why v2 exists

v1 said *borders not shadows · radius 6px · no gradients · motion 2 · density 5*. Every one of
those is defensible for a dense internal tool, and together they produced a product that reads as
a wireframe — "text hell, no soul". Tested on a phone, the verdict was that the backend could be
the best in the room and it would not matter, because **what people see is what they buy.**

v2 keeps every honesty rule from v1 — they are the product's spine — and changes the *medium*.
Where v1 answered an unsourceable figure with a paragraph, v2 answers it with a small marker on a
chart. The discipline survives; the essay does not.

---

## 1. Surface and depth

The v1 error: canvas `#f9f9fa` against `#ffffff` cards separated by a hairline. Nothing floated,
and it read as a table rather than a product.

**The fix is not "more contrast" — it is hue.** Surveying the published token sets of Carbon,
Atlassian, Fluent 2, Ant, Grafana, Polaris, Geist, SLDS and Stripe, every canvas-to-card
relationship sits between **1.03:1 and 1.14:1**. Push past that and the canvas becomes a coloured
panel that makes every card shout.

The highest-leverage move, and the one that actually delivers "blue and white enterprise", is
**Stripe's**: their entire neutral ramp carries a blue hue — `#f8fafd`, `#e5edf5`, `#64748d`,
`#061b31`. You read *blue* from a hue shift, not from a lightness shift, and saturated blue stays
reserved for the single accent.

```
--background   #eef2f8   canvas — blue-tinted, ~1.13:1 against white
--surface      #ffffff   cards
--surface-2    #f2f6fb   inset wells, table stripes
--border       #e5edf5   hairline — blue-tinted, not grey
--muted-fg     #64748d   secondary ink — blue-tinted
--rail         #0b1f3f   deep navy, the ONE dark surface
--rail-hover   #12305e
--rail-text    #a8c0e0
```

### Elevation — a hairline by default, shadow only where something truly lifts

**The two systems in that survey that are actually dense-data dashboards — Grafana and Carbon —
put no shadow on cards at all.** Grafana hard-codes `panel.boxShadow: 'none'` with a 1px 12%
border; Carbon's `Tile` has neither shadow nor radius. At table density, a shadow on every panel
becomes noise.

So, following Atlassian's published rule that surface and shadow tokens are paired:

- **Default panels:** 1px `--border`, no shadow.
- **Raised things only** — the KPI row, popovers, dialogs, drawers, the composer — get a shadow.
- **Hover raises a rung** (Fluent's `shadow4 → shadow8`) rather than translating the card.

Every real shadow in that survey is **two layers**: a near-opaque zero-offset contact ring plus a
low-alpha offset blur, with **negative spread** so the penumbra stays under the card, in a
**non-black** colour, at **4–16% alpha**. A lone `0 4px 12px rgba(0,0,0,0.1)` is the template
signature. Ours are blue-tinted, following Stripe:

```
--shadow-card   0 0 0 1px #0037700f, 0 1px 2px -1px #003b8914
--shadow-hover  0 0 0 1px #00377014, 0 4px 8px -4px #003b8914
--shadow-pop    0 0 0 1px #00377014, 0 8px 16px -6px #0037701a, 0 24px 32px -12px #003b890f
```

**Radius `0.5rem` (8px).** Corrected from my own 14px: the verified card radii across nine systems
are **0, 4, 6, 6, 6, 8, 8, 8, 12** — nothing rounds a data card past 16px, and 6px is the industry
mode. Generous rounding is the marketing-page dialect. Buttons and inputs 6px; chips fully round.

**Never:** glassmorphism, `backdrop-blur` on content, a card inside a card inside a card.

## 2. Colour

**One hue carries meaning.** The blue ramp is validated (`scripts/validate_palette.js`) and
`tests/design/oneRamp.test.mjs` fails any utility naming a step outside it.

```
--blue-50  #eff6ff   --blue-100 #dbeafe   --blue-200 #bfdbfe   --blue-400 #60a5fa
--blue-600 #1570ef   --blue-700 #175cd3   --blue-800 #1e40af   --blue-900 #0b3b8f
```

`--blue-600` is primary, at ~4.9:1 on white — in the same band as Carbon `#0f62fe` (5.00:1),
Atlassian `#1868DB` (5.20:1) and Fluent `#0f6cbd` (5.38:1). For comparison, Ant's `#1677ff` is
**4.10:1 and fails AA for text** — do not drift toward it.

**Gradient, corrected.** Carbon, Atlassian and Fluent ship **zero** gradient tokens. Grafana ships
two, brand-only, never on data. Polaris ships exactly one, and it is the model:

```
--grad-primary-sheen  linear-gradient(180deg, rgba(255,255,255,0) 64%, rgba(255,255,255,0.15) 100%)
```

A 15% white sheen over the bottom third of a **solid** primary fill. That is the entire sourced
precedent. A hue-shifting gradient on a button has no precedent in any of these systems.

**Status is reserved** — `--ok`, `--risk`, `--block` — never a series colour, and **always with an
icon and a word.** The validator fails amber↔red on CVD separation, which is correct: they are
distinguished by label, not hue.

**Charts.** Sequential = one hue light→dark. Diverging = blue ↔ warm with a neutral midpoint.
**Never a rainbow, never a dual axis, never a cycled hue.** Run
`node scripts/validate_palette.js "<hex,hex,…>"` — do not eyeball CVD.

**Radial gauges — one value, never a row of them.** The reference kit leans on them, and they are
defensible for exactly one job: a single value whose position inside a bounded range is the point
(a tenant at 73% of its spend cap). They are the wrong choice the moment you need to compare
several, show a trend, or fit more than one or two in a row — Stephen Few's objection is that
they *"use a great deal of space to say relatively little"* and *"fail spectacularly when intended
for comparison"*. For several bounded values side by side, use bullet bars: a bar one-third the
thickness of its container, a perpendicular target line, and at most three qualitative bands in
one hue at distinct intensities (darker = worse, so it survives colour-blindness).

Grafana's gauge geometry, if you build one: stroke ≈ **13% of radius**, value centred with the
unit at **70%** of the numeral, name below in secondary ink, and **4px** between concentric rings.

## 3. Type

Inter for interface, **JetBrains Mono for every numeral, id, cost, count and timestamp** via the
`Figure` primitive, so columns align and figures do not reflow as they tick.

```
display  28/32  -0.02em 600   page title — ONE per screen
metric   28/32  -0.01em 600   the hero number in a tile
title    16/22   0      600   card title
body     14/20   0      400   default
label    12/16   0      400   tile label, at --muted-fg
meta     12/16   0      400   provenance, timestamps
```

**No published enterprise system in the survey puts a KPI numeral above 32px**, and Atlassian —
the only one with a dedicated `font.metric.*` ramp — caps its largest at **28px / weight 653**.
Grafana's `BigValue` uses **weight 500**. My earlier 34px was the marketing dialect; corrected.

`.t-hero` (`clamp(2.25rem → 3.5rem)`) is **retired from product screens** — landing page only.

**Tile anatomy**, from Ant's `Statistic` and Grafana's `BigValue`:
- Label **above** the value, only **4px** apart, at 12px in `--muted-fg`.
- Value 28px. Hierarchy comes from size and ink, not from shouting.
- **Delta at ~40% of the value size** (Grafana: `max(value/2.5, 12)`), icon 3px smaller again.
- **Sparkline as a full-bleed band across the bottom half** of the tile, not squeezed beside.

## 4. Density and layout

A console, so density is a feature — but density is not the same as *text*.

- Tables for anything countable. A list of tenants is a table.
- Spacing scale `4 · 8 · 12 · 16 · 24 · 32 · 48`. Nothing between.
- Content max-width `1440px`; wide tables scroll inside their own `overflow-x:auto` container and
  **the page body never scrolls horizontally.**
- **Prose belongs in an `InfoTip`, not on the page.** If a paragraph explains a mechanism, it is a
  tooltip. If it explains a *number*, it is a `Receipt`. If it explains an absence, it is an
  `Absence` in the slot the number would occupy — one line, not three.

## 5. The receipt, still the signature

Aegis refuses to assert anything without its origin — `Source:` lines, evidence on health rows,
the rail that caught each attack, `decided_by` on a clamped cap. That discipline is unusual and
worth showing. `Receipt` and `Absence` (in `primitives/Receipt.tsx`) are the one treatment; never
re-improvise provenance per screen.

**A figure that cannot be sourced is never rendered as a number.** But it is also never three
sentences — it is a compact stated absence.

## 6. Motion

Budget **4** (was 2). Motion confirms a state change, reveals structure, or shows work happening.

- `--dur-fast 120ms` hover/focus · `--dur-base 200ms` enter/exit · spring on the flow graph.
- Card entrance stagger on a page load. Real streaming feel on the console.
- **No counting-up on a governance figure** — a spend cap that animates looks approximate.
- No scroll-jacking, no parallax, no infinite ambient loops on operator screens.
- `prefers-reduced-motion` respected on every animated element, including the 3D.

## 7. 3D — simple geometry, and never load-bearing

The brief: *"not too gimmicky, extreme simple 3d not extreme."*

**The language:** matte rounded solids — cubes, a torus, a rounded slab. One soft key light. Slow
drift. **No characters, no scenes, no clutter.**

### The look, as a verified recipe

Rendered and captured; this produces the reference exactly. Use it whether you ship it live or
render it once and export a frame:

```jsx
<Canvas shadows dpr={[1, 2]} camera={{ position: [3, 3, 5], fov: 35 }}>
  <ambientLight intensity={0.6} />
  <directionalLight position={[4, 6, 3]} intensity={2.2} castShadow shadow-mapSize={[1024,1024]} />
  <RoundedBox args={[1.4,1.4,1.4]} radius={0.18} smoothness={6} castShadow receiveShadow>
    <meshStandardMaterial color="#dfe3ec" roughness={0.85} metalness={0} />
  </RoundedBox>
  <mesh rotation={[-Math.PI/2,0,0]} position={[0,-1,0]} receiveShadow>
    <planeGeometry args={[20,20]} /><shadowMaterial opacity={0.18} />
  </mesh>
</Canvas>
```

`metalness` is **0** — a non-metal is not a dial. `roughness` **0.85**; below ~0.5 a specular
highlight appears and it reads as cheap plastic. **No HDRI** — drei's `<Environment preset>`
downloads from a CDN at runtime and its own docs say that is not for production.

### Technique per placement — measured, not assumed

| Placement | Technique | Why |
|---|---|---|
| **Landing hero** | **Pre-rendered AVIF/WebP** | 4.5 kB at 2×, versus **236 kB gzip** to ship a renderer that draws the same still frame. A matte solid on a light ground is almost all smooth gradient — the best case for a modern codec. |
| **Jobs pipeline** | **CSS 3D transforms or SVG isometric** | Six labelled stateful blocks. WebGL costs 235 kB to draw six boxes and makes the labels unselectable and invisible to assistive tech. Keep the isometric transform on **one** container — nested `preserve-3d` has real cross-engine depth-sorting bugs. |
| **Agent flow** | **React Flow (2D)** | The marquee visual. Not a 3D problem. |
| **Knowledge graph** | **See the note below** | `react-force-graph-3d` is **+305 kB gzip** over the 2D build already shipped, and the migration is **not a drop-in**. |

**Spline is ruled out**: its runtime is 544 kB gzip plus the scene file, with a documented case of
17.9 s of CPU script time. Fine as an *authoring* tool to produce the still; never as a runtime.

### The knowledge-graph decision, stated honestly

`nodeCanvasObject` and `nodePointerAreaPaint` **do not exist** in `ForceGraph3D`, and that is
where all of `KnowledgeGraph.tsx`'s visual logic lives — the arcs, the pulse halo, the mono
labels. The data contract ports; the entire paint layer is a rewrite.

The evidence is also mixed. Across the 2D-vs-3D literature, 3D roughly doubles point-reading time
and raises error rates — **but Tractinsky & Meyer (n≈242) found people preferred 2D when the goal
was deciding and 2.5D when the goal was to impress.** That is exactly this product's split. Our
graph is 17 nodes, so performance is a non-issue either way; the question is only whether that
surface is for reading or for impressing.

### Rules that hold wherever 3D appears

- **Never the only carrier of information.** Every state must also read as text, a badge or a row.
- **Never the LCP element.** A text headline or poster image must paint first.
- **`frameloop="demand"`** for anything static — a persistent render loop is a fan-spinning
  liability in a demo. Under `prefers-reduced-motion`, drop to `demand` and `invalidate()` once.
- **`aria-hidden`** on decorative canvases; R3F's `<Canvas fallback>` for the rest.
- **Feature-detect WebGL** (`isWebGL2Available()`) — ~3–4% of sessions lack it, and materially more
  in locked-down enterprise environments. The pre-rendered still doubles as that fallback.
- **Handle `webglcontextlost`**, or a backgrounded tab returns as a blank rectangle mid-demo.

## 8. Hard rules, enforced by tests

- **Light theme only.** No `dark:` anywhere under `web/src` (`tests/design/lightThemeOnly.test.mjs`).
- **One ramp** — no blue step outside the eight (`tests/design/oneRamp.test.mjs`).
- **No `description` props on cards.**
- Visible keyboard focus on every interactive element; `--blue-600` ring, 2px.
- Every route reachable from a real portal; no allowlist entry in `test_route_coverage.py`.
- Server refusals render the server's own sentence via `apiError.ts`, never "something went wrong".

## 9. Anti-slop

Do not ship: generic SaaS chrome · excess cards · glassmorphism · giant centred hero text on a
product screen · repetitive equal-thirds sections · decorative icons duplicating their label ·
pill overuse · cheap shadows · animation for its own sake · a colour that means nothing · **a
paragraph where a chart, a badge or a tooltip would do.**

## 10. Workflow

Inspect the code → read this file → `redesign-existing-projects` if the screen exists → taste
anti-slop pass → implement → check 390 / 834 / 1440 / 1920 → run `/web-interface-guidelines` →
fix findings → final review.

**Conflict order:** existing functionality > this file > taste principles > Vercel usability.
Accessibility and correctness always win.

Full direction, the reference images and the per-screen briefs live in
`docs/dev_new_docs_v2/frontend-redesign/`. Claude's own system (from `getdesign`) is kept at
`docs/design/CLAUDE-REFERENCE.md` as reference only — its identity is not Aegis's.
