/**
 * A red-team report has to be readable as evidence, and two ways it stops being one.
 *
 * **A 100% block rate is not a trophy.** It is either a rail that blocks everything —
 * which the benign controls prove — or a battery that ran nothing. Both are findings,
 * and a screen that renders a green tick for all three cases has quietly turned the
 * most suspicious number in the report into the most reassuring one.
 *
 * **An offline block rate is a claim about signatures, not about the product.** The
 * battery deliberately keeps probes no deterministic signature can catch; offline they
 * leak. Reporting that run as "Aegis blocks 82% of attacks" is the sentence the whole
 * harness exists not to say.
 *
 * The comparison is the third: a rate on its own is unreadable without the last one.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  compareRuns,
  headline,
  points,
  splitLeaks,
  verdictNote,
} from '../../src/components/redteam/redteamReport.ts'

/** A stored run carrying only the fields these functions read. */
function runOf(overrides) {
  return {
    runId: 'rt-1',
    tenantId: null,
    suite: 'owasp-full',
    mode: 'offline',
    startedAt: '2026-08-19T10:00:00Z',
    durationMs: 40,
    initiatedBy: 'devops',
    attacksTotal: 28,
    attacksBlocked: 23,
    attacksUnchecked: 0,
    blockRate: 23 / 28,
    controlsTotal: 8,
    falsePositives: 0,
    falsePositiveRate: 0,
    minBlockRate: 0.75,
    maxFalsePositiveRate: 0,
    passed: true,
    estimatedCostUsd: 0,
    ...overrides,
  }
}

test('a perfect block rate is reported as over-blocking when benign controls were blocked', () => {
  const note = verdictNote(
    runOf({ attacksBlocked: 28, blockRate: 1, falsePositives: 3, falsePositiveRate: 3 / 8 }),
  )
  assert.match(note, /over-blocking/)
})

test('a perfect block rate over an empty control set says nothing could have failed', () => {
  const note = verdictNote(runOf({ blockRate: 1, controlsTotal: 0, falsePositiveRate: 0 }))
  assert.match(note, /nothing here that could have failed/)
})

test('a battery that ran no attacks reports that, not a rate', () => {
  const note = verdictNote(runOf({ attacksTotal: 0, blockRate: 0, passed: true }))
  assert.match(note, /No attacks ran/)
})

test('a perfect run with passing controls is the only one allowed to sound clean', () => {
  const note = verdictNote(
    runOf({ attacksBlocked: 28, blockRate: 1, falsePositives: 0, falsePositiveRate: 0 }),
  )
  assert.match(note, /all 8 benign controls passed/)
  assert.doesNotMatch(note, /over-blocking/)
})

test('an offline run claims its signatures, a live run claims the stack', () => {
  const offline = headline(runOf({ mode: 'offline' }))
  const live = headline(runOf({ mode: 'live' }))
  assert.match(offline, /deterministic signatures blocked 23 of 28/i)
  assert.match(live, /rail stack blocked 23 of 28/i)
  // Neither is allowed to become a claim about the product as a whole.
  assert.doesNotMatch(offline, /Aegis/)
})

test('the first run of a battery compares to nothing rather than to zero', () => {
  assert.equal(compareRuns(runOf({}), null), null)
})

test('a comparison names the direction that is better for each measure', () => {
  const previous = runOf({ runId: 'rt-0', blockRate: 0.9, falsePositiveRate: 0.1, attacksBlocked: 25 })
  const current = runOf({ blockRate: 0.8, falsePositiveRate: 0, attacksBlocked: 23 })
  const comparison = compareRuns(current, previous)

  // The block rate fell: worse.
  assert.equal(comparison.blockRate.improved, false)
  // The false-positive rate fell too — and for that measure, falling is better.
  assert.equal(comparison.falsePositiveRate.improved, true)
  // Two more attacks got through than last time.
  assert.equal(comparison.attacksLeakedDelta, 2)
  assert.equal(comparison.previousRunId, 'rt-0')
})

test('leaks are split into the ones with no signature by design and the real misses', () => {
  const { expected, unexpected } = splitLeaks([
    { id: 'jb-04', needsLlm: true },
    { id: 'inj-01', needsLlm: false },
  ])
  assert.deepEqual(
    expected.map((p) => p.id),
    ['jb-04'],
  )
  assert.deepEqual(
    unexpected.map((p) => p.id),
    ['inj-01'],
  )
})

test('a delta of zero reads as no change, not as +0', () => {
  assert.equal(points(0), 'no change')
  assert.equal(points(0.04), '+4 pts')
  assert.equal(points(-0.04), '−4 pts')
})

test('a run with a probe nothing examined refuses to call itself coverage', () => {
  // The measured case: a live owasp-full run scored 28/28 and PASSED with one probe
  // refused by an unavailable classifier. The screen must not repeat that sentence.
  const note = verdictNote(
    runOf({ attacksBlocked: 27, attacksUnchecked: 1, blockRate: 27 / 28, passed: true }),
  )
  assert.match(note, /refused without being examined/)
  assert.doesNotMatch(note, /benign controls passed/)
})

test('the headline says how many probes went unexamined rather than hiding them', () => {
  const text = headline(
    runOf({ mode: 'live', attacksBlocked: 27, attacksTotal: 28, attacksUnchecked: 1 }),
  )
  assert.match(text, /blocked 27 of 28/)
  assert.match(text, /1 more were refused without being examined/)
})
