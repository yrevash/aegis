/**
 * A nav gloss is one line, and one line has to be checkable.
 *
 * `Section.tooltip` used to be rendered as `title={item.tooltip}` on every row of
 * `PortalNav` — a native browser tooltip. Native tooltips clip, time out, and take
 * no keyboard focus, so nothing in the product ever complained as these grew: 34
 * of them reached a **28.5-word mean**, and `compliance` reached **129 words**
 * inside a `title=` attribute. Text that long in a hover is not dense, it is
 * invisible, and it fired under the pointer every time it crossed the rail.
 *
 * The `title=` is gone. What is left is the catalogue's plain-language gloss, and
 * the failure mode of a gloss is that it silently becomes an essay again — the
 * next person with something worth saying about a section has exactly one field in
 * front of them. So the cap is asserted off the catalogue itself: 12 words. The
 * explanation goes on the destination screen's `PageHeader` `InfoTip` (under its
 * own 40-word ceiling, `tipLength.test.mjs`), and anything longer than that goes
 * to `docs/`.
 *
 * The count is a plain whitespace split — the same measure the audit used to get
 * 28.5, so the two numbers stay comparable. It means `—` and `·` cost a word each,
 * which is correct: they are read.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { SECTIONS } from '../../src/lib/portal.ts'

/** The audit's measure: whitespace-separated tokens, punctuation included. */
const words = (text) => text.trim().split(/\s+/).filter(Boolean)

const MAX_WORDS = 12

test('every section gloss is 12 words or fewer', () => {
  const entries = Object.entries(SECTIONS)
  // A scan whose subject can silently empty out proves nothing when it passes:
  // an import that resolved to `{}` would report a clean sweep of nothing.
  assert.ok(entries.length > 20, `the catalogue scan came back near-empty (${entries.length} sections)`)

  const offenders = []
  for (const [id, section] of entries) {
    const count = words(section.tooltip).length
    if (count > MAX_WORDS) {
      offenders.push(`${id}: ${count} words — ${section.tooltip}`)
    }
  }

  assert.deepEqual(
    offenders,
    [],
    `a nav gloss over ${MAX_WORDS} words is an essay in a place nobody reads — ` +
      "move it to that screen's PageHeader InfoTip, or to docs/",
  )
})

test('every section still has a gloss to read', () => {
  // The other way this file passes vacuously: a cap is trivially met by an empty
  // string, and an empty gloss is a section with no plain-language name at all.
  for (const [id, section] of Object.entries(SECTIONS)) {
    assert.equal(typeof section.tooltip, 'string', `${id} has no tooltip`)
    assert.ok(words(section.tooltip).length >= 3, `${id}'s gloss says nothing: "${section.tooltip}"`)
  }
})

test('the nav rail renders no native browser tooltip', () => {
  // The cap above is only half the fix. Capping the text while leaving `title=`
  // on the row keeps 34 hovers that clip, time out and cannot be focused — and
  // the next person to re-add one would pass every assertion in this file.
  const source = readFileSync(fileURLToPath(new URL('../../src/components/layout/PortalNav.tsx', import.meta.url)), 'utf8')
  const rendering = source.slice(source.indexOf('export function PortalNav'))
  assert.doesNotMatch(
    rendering,
    /\btitle=/,
    'PortalNav grew a native `title=` tooltip again — the gloss belongs on the ' +
      "destination screen's PageHeader, not in a hover the keyboard cannot reach",
  )
})
