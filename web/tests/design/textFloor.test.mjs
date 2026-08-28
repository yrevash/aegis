/**
 * No functional text below 11px — and being on the size ramp is not a defence.
 *
 * The failure this closes is a token argument beating a legibility one. A size gets
 * added to the ramp, every reviewer checks the value against the ramp, it passes, and
 * the product ships furniture nobody over forty can read. Aegis had three of these
 * (`PipelineIso.tsx` stage labels at 10px and 9px) and `.eyebrow` sitting at 0.68rem —
 * **10.88px, under the floor by 0.12px** — which is precisely the kind of number that
 * survives review forever because it looks like it was chosen.
 *
 * So this test ignores the design system on purpose. It does not ask whether a size is
 * on the ramp; it asks whether a human can read it.
 *
 * **The SVG exemption is deliberately not inherited.** The rule this threshold comes
 * from exempts `svg` and `[aria-hidden]`, written for decorative vector art. Aegis's
 * smallest text lives *inside* SVGs and is real content — `PipelineIso.tsx:466` is the
 * stage name on a pipeline diagram, which is the label a reader is there to read. An
 * exemption for ornament is wrong for a diagram.
 *
 * Two things are checked, because the rule can be broken from either side:
 *   1. arbitrary utilities — `text-[10px]`, `text-[0.6rem]` — anywhere under `src/`
 *   2. the token ramp itself, in `globals.css`, so a sub-floor `.t-*` or `--font-size-*`
 *      cannot re-introduce it globally in one line
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))
const GLOBALS = fileURLToPath(new URL('../../src/app/globals.css', import.meta.url))

/** The floor, in px. Functional text is anything a reader is meant to read. */
const FLOOR_PX = 11

/** Every file under `src/` that can carry a class name. */
function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (/\.(tsx?|css)$/.test(entry)) found.push(path)
  }
  return found
}

/** `10px` / `0.68rem` / `.875em` → px, assuming the 16px root this app sets. */
function toPx(value, unit) {
  const n = Number.parseFloat(value)
  if (!Number.isFinite(n)) return null
  if (unit === 'px') return n
  if (unit === 'rem' || unit === 'em') return n * 16
  return null
}

test('no arbitrary text utility under src/ resolves below the floor', () => {
  const files = sources()
  // Anti-vacuity: a scan that walks nothing passes everything.
  assert.ok(files.length > 50, `expected to scan >50 files, saw ${files.length}`)

  let utilitiesSeen = 0
  const offenders = []
  for (const file of files) {
    const text = readFileSync(file, 'utf8')
    for (const match of text.matchAll(/text-\[(\d*\.?\d+)(px|rem|em)\]/g)) {
      utilitiesSeen += 1
      const px = toPx(match[1], match[2])
      if (px !== null && px < FLOOR_PX) {
        const line = text.slice(0, match.index).split('\n').length
        offenders.push(`${file.replace(SRC, 'src/')}:${line} → ${match[0]} = ${px}px`)
      }
    }
  }

  // Second anti-vacuity guard: if the arbitrary-value syntax ever changes, the regex
  // would quietly match nothing and this test would pass without reading anything.
  assert.ok(
    utilitiesSeen > 0,
    'the scan found no text-[…] utilities at all — the pattern no longer matches the source',
  )
  assert.deepEqual(
    offenders,
    [],
    `functional text below ${FLOOR_PX}px:\n  ${offenders.join('\n  ')}`,
  )
})

test('the token ramp itself does not dip below the floor', () => {
  const css = readFileSync(GLOBALS, 'utf8')
  let declarationsSeen = 0
  const offenders = []

  // Every font-size declaration in the sheet, AND every entry in Tailwind v4's named
  // type scale. The second half is the one an audit caught missing: this project has no
  // tailwind.config.js, so the named scale (`text-xs`, `text-sm`, …) is defined by
  // `--text-*` custom properties inside `@theme`. A scan that reads only `font-size:`
  // declarations leaves `--text-xs: 0.5rem` — one line, 206 call sites — completely
  // unguarded, which is exactly the "one line cannot reintroduce this globally" claim
  // the test was written to support.
  for (const match of css.matchAll(/--text-[a-z0-9-]+:\s*(\d*\.?\d+)(px|rem|em)\s*;/g)) {
    declarationsSeen += 1
    const px = toPx(match[1], match[2])
    if (px !== null && px < FLOOR_PX) {
      const line = css.slice(0, match.index).split('\n').length
      offenders.push(`globals.css:${line} → ${match[0].trim()} = ${px}px`)
    }
  }

  for (const match of css.matchAll(/font-size:\s*(\d*\.?\d+)(px|rem|em)\s*;/g)) {
    declarationsSeen += 1
    const px = toPx(match[1], match[2])
    if (px !== null && px < FLOOR_PX) {
      const line = css.slice(0, match.index).split('\n').length
      offenders.push(`globals.css:${line} → ${match[1]}${match[2]} = ${px}px`)
    }
  }

  assert.ok(
    declarationsSeen > 5,
    `expected several font-size declarations in globals.css, saw ${declarationsSeen}`,
  )
  assert.deepEqual(
    offenders,
    [],
    `a type token resolves below ${FLOOR_PX}px:\n  ${offenders.join('\n  ')}`,
  )
})
