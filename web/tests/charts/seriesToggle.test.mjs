/**
 * A `StackedArea` legend entry is a button, so clicking it has to do something.
 *
 * It did not: the entries carried `onMouseEnter`/`onFocus` only, so a click changed no
 * state, no DOM and no path of the chart. The obvious thing a chart legend does is toggle
 * its series; this is the state behind that, asserted without a DOM.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const { shownSeries, toggleSeries } = await import(
  '../../src/components/charts/seriesToggle.ts'
)

const CHART = fileURLToPath(new URL('../../src/components/charts/StackedArea.tsx', import.meta.url))

const SERIES = [{ key: 'a' }, { key: 'b' }, { key: 'c' }]

test('a click removes the band from the stack, and a second click restores it', () => {
  const off = toggleSeries(new Set(), 'b')
  assert.deepEqual(
    shownSeries(SERIES, off).map((s) => s.key),
    ['a', 'c'],
  )
  const on = toggleSeries(off, 'b')
  assert.deepEqual(
    shownSeries(SERIES, on).map((s) => s.key),
    ['a', 'b', 'c'],
  )
})

test('hiding every band is allowed', () => {
  // Refusing the last one would put back a click that does nothing — the very defect.
  let hidden = new Set()
  for (const s of SERIES) hidden = toggleSeries(hidden, s.key)
  assert.deepEqual(shownSeries(SERIES, hidden), [])
  assert.deepEqual(
    shownSeries(SERIES, toggleSeries(hidden, 'a')).map((s) => s.key),
    ['a'],
  )
})

test('the toggle never mutates the set it was handed', () => {
  // React would not re-render on an in-place mutation, and the legend would look dead
  // again for exactly the reason it looked dead before.
  const before = new Set(['a'])
  const after = toggleSeries(before, 'b')
  assert.deepEqual([...before], ['a'])
  assert.deepEqual([...after].sort(), ['a', 'b'])
})

test('the legend button is wired to the toggle and carries its state', () => {
  const source = readFileSync(CHART, 'utf8')
  assert.match(source, /onClick=\{\(\) => setHidden\(\(current\) => toggleSeries\(current, s\.key\)\)\}/)
  // Visible state, not a hover highlight: pressed-ness for assistive tech, and a
  // struck-through label for everyone else.
  assert.match(source, /aria-pressed=\{!off\}/)
  assert.match(source, /line-through/)
})
