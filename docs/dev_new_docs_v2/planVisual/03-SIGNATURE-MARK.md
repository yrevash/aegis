# The signature mark

The owner asked for *"our own artifact or character of Aegis — not a bird or falcon, but a cute, I
don't know."* After research, the decision taken was: **an abstract signature mark, not a
creature.** This document records why, and what it should be.

---

## 1. Why not a cute character

Two independent reasons, and they point the same way.

**The research.** Mascots do aid recall in crowded markets — but *"a mascot works best as a
recognition tool… and does nothing for making a brand feel warmer when that's not the actual
problem,"* and *"embodying your brand does not mean becoming cartoonish, childish or less
serious."* Aegis's entire pitch is governance, audit and refusal, judged by a TCS enterprise jury.
Cute is the one register that argues against the product.

**The design system already ruled on it.** DESIGN.md §7:

> *"The language: matte rounded solids — cubes, a torus, a rounded slab. One soft key light. Slow
> drift. **No characters, no scenes, no clutter.**"*

That rule was written against a stated brief (*"not too gimmicky, extreme simple 3d not
extreme"*) and it still holds.

---

## 2. The thing nobody noticed: Aegis already has two identity elements

A third would be clutter. Both of these already exist and are already mounted.

### `brand/AegisMark.tsx` — the falcon (88 L)

A **single `<path>`** on `viewBox="0 0 218 136"`, filled with `currentColor`. Traced from an
owner-supplied source (`web/public/brand/falcon-source.jpg`) via threshold → Moore boundary trace →
Chaikin smoothing → Douglas-Peucker. Deliberately not square, deliberately not boxed in a tile.
Paired with the wordmark in `brand/AegisLockup.tsx`, and reused as `app/icon.svg`.

Being one path makes it **directly animatable with no new assets** — `stroke-dasharray` draw-on,
path-length reveal, opacity/scale on a spring.

### `console/AssistantBot.tsx` — the mascot that is already there (113 L + `botEyes.ts`)

A line-art robot head: antenna with a `--blue-200` bulb, side tabs, ring eyes whose **pupils track
the pointer** via `--pupil-x` / `--pupil-y` CSS custom properties written inside a `requestAnimationFrame`
loop — **zero React renders per frame**. Mounted at `ChatConsole.tsx:924`. During a run it goes
still, pupils forward.

Its docstring is explicit that this separation is deliberate:

> *"a company logo and an assistant avatar are two different things."*

---

## 3. Recommendation — build a new mark, and *delete* the bot

An earlier draft of this document recommended giving the state machine to `AssistantBot`. **That
was wrong, and the design pass overturned it with a better argument.** The recommendation is now:

**Build one new abstract mark, and delete `AssistantBot`. The console's identity-element count goes
down, not up.**

### Why not the falcon

It is the **company** mark, live in seven places — sidebar, both login headers, landing header,
landing footer, `error.tsx`, `not-found.tsx`. A brand mark that cycles through five states is not a
brand mark. Mechanically it is also the wrong shape: one ~300-point `fillRule="evenodd"` filled
path, so `stroke-dasharray` draw-on and per-segment motion are unavailable without re-authoring the
artwork, and a path-morph between five states of a falcon has no clean semantics.

### Why not the bot

1. **It is a character** — a robot head with tracking pupils, a nose and a smile. DESIGN.md §7 says
   *"No characters, no scenes, no clutter."* Keeping it means keeping a standing violation.
2. **It is behaviourally backwards.** `AssistantBot.tsx:38-43` deliberately **stops** tracking when
   a run starts — so the console's most animated element goes still at the exact moment work
   begins. That is the opposite of what this whole plan is for.
3. It is the **second** identity element in a console that already carries `AegisLockup`.

### The arithmetic

Delete `AssistantBot.tsx`, `botEyes.ts` and `tests/console/assistantBot.test.mjs`; put the new mark
in the slot at the head of the run spine, on screen at all times, idle or running.

**Net: −1 character, −1 identity element, +1 identity element that does something.** The console
ends up cleaner than it started — which is also the answer to the "too much data" feedback.

The falcon stays exactly what it is: the brand mark, on the landing page, the nav lockup and the
favicon.

---

## 4. The mark itself

**A six-segment ring around a core.** Six because that is the real length of both rail chains —
`INPUT_CHAIN.length === OUTPUT_CHAIN.length === 6` (`stageTimeline.ts:72-89`). The geometry is a
fact about the system, not decoration. The core is the model; the ring is what surrounds it.
Abstract, geometric, no face, no creature.

`viewBox="0 0 48 48"`. Six `<path>` arcs on r=18 with a 6° gap, `stroke-linecap="round"`,
`stroke-width` 2.5. One `<circle r=6>` core. ~70 lines.

## 5. The states, and the wire signal behind each

Every state is driven by a named fact already on the wire — no invented moods.

| State | Wire signal (exact) | Technique | Reduced-motion |
|---|---|---|---|
| `idle` | `state === null \|\| (!running && events.length === 0)` | draws itself in once via `stroke-dasharray`, 320 ms. **No loop** — §6 bans ambient loops on operator screens | drawn, static |
| `screening` | newest open stage satisfies `isGuardStage(stage.node)` | rotate 0→360 over 2.4 s, linear. Carries **duration, never progress** | hairline → solid, static |
| `thinking` | open stage `signal === 'agent'`, lanes ≤ 1 | core scale 1→1.12→1 keyed on `beat.seq` — **one pulse per wire event** | core solid, static |
| `fanout` | `deriveAgentPanel(state).lanes.length >= 2` | six segments spring outward into **N arcs, N = the wire's lane count** | arcs spread, no spring |
| `gated` | `state.approval !== null` | **rotation stops dead**, `--risk` hue, core morphs circle → square. Zero motion — **the stillness is the state** | identical |
| `blocked` | newest guardrail `verdict === 'block'` | `--block` hue, and **one segment vanishes** at `chain.indexOf(guardrail.layer)`. The wire names the deciding layer, so the gap is a fact | identical |
| `settled` | `!running && answer !== ''` | segments close, core fills, 200 ms | identical |

Seven states, all wire-driven, all legible without motion.

**Precedence is the whole content of `runMarkState.ts` and must be explicit:**
`blocked > gated > fanout > screening > thinking > settled > idle`.

An unrecognised `layer` must yield `null`, not segment 0 — drawing a break at the wrong rail is
worse than drawing none.

`.animate-beat` already exists at `globals.css:438-450` with **no console consumer**, and
`beatFromSignal` is already computed on every turn. The mark consumes both rather than deriving
its own signal.

---

## 5. Constraints

- **SVG and CSS only.** No WebGL — `three` is not installed, and DESIGN.md §7 measured that a
  pre-rendered still beats shipping a renderer (**4.5 kB vs 236 kB gzip**).
- **One blue ramp**, enforced by `tests/design/oneRamp.test.mjs`. Only the reserved status hues
  (`--block`, `--ok`, `--risk`) may appear, and only for the states above.
- **`prefers-reduced-motion`** on every state transition. `AssistantBot` already honours it.
- **Never the only carrier of information.** A blocked run must still say so in text — the mark is
  a second channel, not the channel.
- **No infinite ambient loop on operator screens** (§6). The idle state is pointer-reactive, which
  is not a loop; the thinking pulse exists only while a run is live.

---

## 6. What was considered and rejected

| Option | Why not |
|---|---|
| A new abstract aperture/shield mark | Correct in isolation, but it would be the **third** identity element in one console. The bot already occupies this slot. |
| Animating the falcon into the state machine | Blurs brand and assistant — a separation the codebase drew deliberately and documented. |
| A cute creature | Owner's decision, DESIGN.md §7, and the credibility risk with an enterprise jury. |
| A 3D mark | No WebGL for a mark that must render in a 24px nav slot and a console corner. |
