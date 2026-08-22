/**
 * Hiding the evidence is the redesign; losing it is a regression (DESIGN.md §4).
 *
 * The devops surfaces just moved three kinds of prose one layer down: the probe
 * evidence under each dependency row, the reasoning under each stated absence, and
 * the sentence trailing a receipt's identifier. Every one of those is now behind a
 * disclosure, and a disclosure has a failure mode that the page it came from did
 * not: **it can be emptied without anything looking wrong**. A tooltip that lost its
 * content, a trigger that stopped naming its subject, an origin split that dropped
 * the half it was supposed to relocate — all three render a screen that is quieter,
 * tidier, and no longer sourced. Nothing else in this repo would notice.
 *
 * So: the claim each relocation makes, and the way each one fails silently.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

import { splitOrigin } from '../../src/components/ops-overview/readiness.ts'

const read = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8')

test('splitting a receipt relocates the sentence and never drops it', () => {
  // The real payload of `GET /platform/caches`: an identifier, then a clause about
  // the mechanism. The identifier is the receipt and stays on the face; the clause
  // is prose and goes to the tooltip — but it has to come back out of the split.
  const { origin, note } = splitOrigin('aegis.core.cache_stats — counters incremented inside each cache')
  assert.equal(origin, 'aegis.core.cache_stats')
  assert.equal(note, 'counters incremented inside each cache')

  // The failure mode is a silent truncation: an origin with no spaced dash must come
  // back whole, and a dash *inside* an identifier is not a place to cut a receipt.
  assert.deepEqual(splitOrigin('in_process_rolling_window'), {
    origin: 'in_process_rolling_window',
    note: null,
  })
  assert.deepEqual(splitOrigin('job_runs.status-history'), {
    origin: 'job_runs.status-history',
    note: null,
  })
})

test('every relocated claim is still rendered, one layer down', () => {
  const mark = read('../../src/components/ops-overview/AbsenceMark.tsx')
  // An absence that keeps only its figure is a blank with a glyph on it: the reason
  // it is not recorded and what would have to change are the honesty, and both have
  // to survive the move into the tip.
  assert.match(mark, /\{why\}/, 'AbsenceMark stopped rendering `why`')
  assert.match(mark, /\{needed\}/, 'AbsenceMark stopped rendering `needed`')

  const rows = read('../../src/components/health/PipelineHealthView.tsx')
  // The dependency table dropped its "What answered" column. The probe behind each
  // verdict moved into the row's disclosure — a health page whose verdicts cannot be
  // checked is a health page asking to be trusted.
  assert.match(rows, /\{row\.evidence\}/, 'the dependency row stopped rendering its evidence')
  assert.match(rows, /\{row\.detail\}/, "the dependency row stopped rendering the server's detail")
})

test('a disclosure trigger names what it belongs to', () => {
  // §4: evidence off the face stays reachable "behind a trigger whose `aria-label`
  // names what it belongs to". `InfoTip` defaults that label to "More information",
  // so an omitted `label` is invisible — the page looks identical and a screen-reader
  // user gets eight identical buttons where eight components used to be named.
  for (const file of [
    '../../src/components/ops-overview/AbsenceMark.tsx',
    '../../src/components/ops-overview/OpsBlocks.tsx',
    '../../src/components/ops-overview/ComponentBoard.tsx',
    '../../src/components/health/PipelineHealthView.tsx',
  ]) {
    const source = read(file)
    const triggers = source.match(/<InfoTip\b[^>]*/g) ?? []
    assert.ok(triggers.length > 0, `${file} renders no InfoTip — has the disclosure gone?`)
    for (const trigger of triggers) {
      assert.match(trigger, /\slabel=/, `an InfoTip in ${file} has no label: ${trigger.trim()}`)
    }
  }
})
