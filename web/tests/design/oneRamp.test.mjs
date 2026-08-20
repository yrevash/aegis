/**
 * One hue carries meaning, and that has to be checkable.
 *
 * Two failures this catches, both of which are invisible at runtime.
 *
 * The first is a **retired alias**. `--agent` used to mean mint and `--ml` used
 * to mean purple; their values became blue while their names stayed, so for one
 * phase the console had three colour tokens whose names actively lied. They are
 * gone now — renamed to the ramp steps they had already become — but a name that
 * used to work is exactly what gets pasted back in from an older branch, and
 * Tailwind answers an unknown utility with silence, not an error. `bg-agent`
 * would simply paint nothing, and nobody would find it for a phase.
 *
 * The second is a **step off the ramp**. `globals.css` closes Tailwind's `blue`
 * namespace and reopens it with only the DESIGN.md scale, so `bg-blue-500` is
 * not an off-system hue — it is, again, silence. Naming the allowed steps here
 * turns both silences into a failing test.
 */

import assert from 'node:assert/strict'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const SRC = fileURLToPath(new URL('../../src/', import.meta.url))

/** The blue scale that exists, from DESIGN.md §2. Nothing between these. */
const RAMP = new Set(['50', '100', '200', '400', '600', '700', '800', '900'])

/** The three subject aliases that were retired, in every spelling they had. */
const RETIRED = /(?:--|\b(?:bg|text|border|ring|fill|stroke|from|via|to|outline|divide|accent|shadow|decoration|caret|placeholder)-)(agent|graph|ml)(?:-ink|-foreground)?\b/

/** Any named step on the blue scale, as a utility or as a variable. */
const BLUE_STEP = /(?:--|-)blue-(\d+)\b/g

/** Every file under `src/` that can carry a class name. */
function sources(dir = SRC, found = []) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) sources(path, found)
    else if (/\.(tsx?|css)$/.test(entry)) found.push(path)
  }
  return found
}

/** Read every source line once, as `[relative path, line number, text]`. */
function lines() {
  const files = sources()
  // A scan whose subject can silently become empty proves nothing when it passes.
  assert.ok(files.length > 100, `the source scan came back near-empty (${files.length} files)`)
  const out = []
  for (const path of files) {
    const relative = path.slice(SRC.length)
    for (const [index, text] of readFileSync(path, 'utf8').split('\n').entries()) {
      out.push([relative, index + 1, text])
    }
  }
  return out
}

test('no retired subject alias survives anywhere under src/', () => {
  const offenders = []
  for (const [path, number, text] of lines()) {
    if (RETIRED.test(text)) offenders.push(`${path}:${number}  ${text.trim()}`)
  }

  assert.deepEqual(
    offenders,
    [],
    'agent / graph / ml are no longer colours — they are steps on the blue ramp, ' +
      'and a utility naming one of them paints nothing at all',
  )
})

test('every blue step named under src/ exists on the ramp', () => {
  const offenders = []
  for (const [path, number, text] of lines()) {
    for (const match of text.matchAll(BLUE_STEP)) {
      if (!RAMP.has(match[1])) offenders.push(`${path}:${number}  blue-${match[1]}`)
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `the ramp is ${[...RAMP].join(' · ')} and globals.css closes the namespace, so a ` +
      'step outside it resolves to nothing rather than to an off-system hue',
  )
})
