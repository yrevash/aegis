---
version: 1
name: Aegis design system
description: >
  An enterprise governance console. Blue-anchored, restrained, high-density, trust-first.
  The signature is the receipt: every figure, verdict and control shows where it came from.
  Replaces the six-hue theme and the 12px SaaS radius that shipped through phase 7.
---

# Aegis design system

**Design read:** a multi-tenant AI governance console for platform operators, tenant
administrators and a technical jury — with a restrained, editorial, information-dense
language, leaning toward a single-hue blue system on cool neutrals with tabular numerics.

**Dials** (from the taste skill's trust-first / regulated row, which is what Aegis is):
`DESIGN_VARIANCE 3` · `MOTION_INTENSITY 2` · `VISUAL_DENSITY 5`.

Note: the taste skill states it is for landing pages and portfolios, **not dashboards or
data tables**. Its *anti-slop list* applies here in full; its art direction does not.
This file is the direction. The Vercel guidelines are the final quality gate.

---

## 1. The signature: the receipt

Aegis's product thesis is that **nothing is asserted without its origin** — `Source:` lines
on forecast panels, evidence on every health row, the rail that caught each red-team
attack, `decided_by` on a clamped cap, provenance on a settings value, "what this page
cannot tell you" cards where a figure is not recorded.

That discipline is unusual, and it is the thing worth making *visible*. So it is the
system's one signature element rather than a decoration:

- Every figure that has an origin carries a **provenance line** — same treatment
  everywhere: `text-xs`, `text-muted-foreground`, a hairline rule above, monospaced for
  identifiers.
- A figure that **cannot** be sourced is never rendered as a number. It is a stated
  absence, in the same slot the number would occupy.
- Spend the visual boldness here and nowhere else. Everything around it stays quiet.

## 2. Colour

**One hue carries meaning. Everything else is neutral.** The theme that shipped through
phase 7 had six decorative hues — mint for agents, purple for ML, blue, amber, rose,
green — which is the "multicolor" problem: a reader cannot tell which colours *mean*
something and which are branding.

### Blue scale — identity, magnitude, emphasis
```
--blue-50   #eff6ff    wash / selected row
--blue-100  #dbeafe    fill behind a value
--blue-200  #bfdbfe    chart step 1
--blue-400  #60a5fa    chart step 2
--blue-600  #1570ef    PRIMARY — actions, focus ring, active nav
--blue-700  #175cd3    hover / pressed
--blue-900  #0b3b8f    chart step 4, deep emphasis
```

### Neutrals — the page
```
--background #f9f9fa   --surface #ffffff    --card #ffffff
--muted      #f2f4f7   --border  #e4e7ec    --input  #d0d5dd
--foreground #101828   --muted-foreground #667085
```

### Status — reserved, never reused as a series colour
```
--ok      #12b76a     --warning #dc6803     --danger #d92d20
```
Status **always ships with an icon and a word.** Never colour alone — the palette
validator fails amber↔red on CVD separation, and that is correct: they are distinguished
by label, not hue.

### Deleted deliberately
`--agent` (mint), `--ml` (purple), and the decorative `*-foreground` set. Agent
trajectories and ML panels use the blue scale like everything else. If two things need
to be told apart, they are told apart by **position, label or weight** first, and only
then by a step on one ramp.

### Charts
Sequential = one hue, light → dark, from the blue scale. Diverging = blue ↔ warm with a
neutral grey midpoint. **Never a rainbow, never a dual axis, never a cycled hue.** Run
`scripts/validate_palette.js` — do not eyeball CVD separation.

## 3. Type

Inter for interface, JetBrains Mono for **every** numeral, identifier, run id, hash,
cost, count and timestamp. Tabular numerics are non-negotiable in a console — figures
must align down a column and must not reflow as they tick.

```
display   28/32  -0.02em  600     page title, one per screen
title     20/28  -0.01em  600     section
body      14/20   0        400    default — this is a dense product, not a landing page
label     13/16   0        500    field labels, table headers
meta      12/16   0        400    provenance, timestamps, counts
mono      13/20   0        450    all numerics and identifiers
```

One display size per screen. **A second hero-sized number on the same page is a
hierarchy failure, not emphasis.**

## 4. Shape and depth

- `--radius: 0.375rem` (6px). The 12px that shipped was commented *"the SnowUI/SaaS
  middle ground"* — that is the generic default, and it reads as templated.
- **Borders, not shadows.** A 1px `--border` separates surfaces. One shadow token exists
  for genuinely floating layers (popover, dialog) and nowhere else.
- No gradients. No glassmorphism. No card inside a card inside a card — if a region needs
  three nested surfaces, the information architecture is wrong.

## 5. Density and layout

This is a console. Density is a feature.

- Tables beat card grids for anything countable. A list of tenants is a table.
- Spacing scale: `4 · 8 · 12 · 16 · 24 · 32 · 48`. Nothing between.
- Content max-width `1440px`; tables scroll inside their own `overflow-x:auto` container
  and **the page body never scrolls horizontally**.
- Empty states are instructions, not shrugs: what this holds, why it is empty, the one
  action that fills it.

## 6. Motion

`MOTION_INTENSITY 2`. Motion confirms a state change and nothing else.

- `--dur-fast 120ms` for hover/focus, `--dur-base 200ms` for enter/exit. Nothing slower
  in the product surface.
- No scroll animation, no parallax, no infinite loops, no counting-up numbers on a
  governance figure — a spend cap that animates is a spend cap that looks approximate.
- `prefers-reduced-motion` respected on every animated element.

## 7. Hard rules, enforced by tests

- **Light theme only.** No `dark:` variants anywhere under `web/src`; enforced by
  `web/tests/design/lightThemeOnly.test.mjs`.
- **No `description` props on cards.**
- Visible keyboard focus on every interactive element — `--blue-600` ring, 2px.
- Every control reachable from a real portal; no allowlist entry in
  `backend/tests/api/test_route_coverage.py`.
- Server refusals render the server's own sentence via `apiError.ts`, never
  "something went wrong".

## 8. Anti-slop, specifically

Do not ship: generic SaaS dashboard chrome · excess cards · unnecessary gradients ·
glassmorphism · giant centred hero text · repetitive equal-thirds sections · decorative
icons that duplicate their label · pill overuse · cheap shadows · animation for its own
sake · a colour that means nothing.

## 9. Workflow

Significant UI work: inspect the code → read this file → `redesign-existing-projects` if
the screen exists → taste anti-slop pass → implement → check mobile / tablet / desktop /
large → run `/web-interface-guidelines` → fix findings → final review.

**Conflict order:** existing functionality > this file > taste principles > Vercel
usability. Accessibility and correctness always win.

Claude's own system, installed by `getdesign`, is kept at
`docs/design/CLAUDE-REFERENCE.md` as *reference only*. Its cream-and-coral identity is
not Aegis's, and its branding must not be copied.
