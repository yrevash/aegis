/**
 * `patchPosture` may never claim more than it checked.
 *
 * This screen's whole subject is honesty about staleness, so the posture it reports is
 * the one figure on it that must not overstate. Two ways to have no evidence — the
 * registry was never reached, or there was nothing to reach it about — and both have to
 * land on `unverified` rather than on the reassuring answer.
 *
 * The empty case is a real defect this test was written for: with no packages the three
 * counters are all zero, so every guard fell through and an empty check reported
 * `current` — "up to date", the strongest claim the function can make, on no evidence at
 * all. `PatchCheck.tsx`'s JSDoc had claimed this module was unit-tested; nothing in the
 * repository referenced it, which is how the gap survived.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { POSTURE_LABEL, patchPosture } from '../../src/components/devops/stackDisplay.ts'

/** Nothing checked. Every counter zero, which is exactly what makes it dangerous. */
const NOTHING = { total: 0, current: 0, outdated: 0, unknown: 0 }

test('an empty check is unverified, never up to date', () => {
  assert.equal(patchPosture(NOTHING, true), 'unverified')
  assert.notEqual(POSTURE_LABEL[patchPosture(NOTHING, true)], POSTURE_LABEL.current)
})

test('offline is unverified however the counters happen to read', () => {
  // Even a summary that looks perfect cannot be trusted when nothing was dialled.
  const perfect = { total: 12, current: 12, outdated: 0, unknown: 0 }
  assert.equal(patchPosture(perfect, false), 'unverified')
})

test('only a real, complete, online answer reports up to date', () => {
  assert.equal(patchPosture({ total: 12, current: 12, outdated: 0, unknown: 0 }, true), 'current')
})

test('an unresolved package outranks a clean majority, and an outdated one outranks both', () => {
  assert.equal(patchPosture({ total: 12, current: 11, outdated: 0, unknown: 1 }, true), 'unverified')
  assert.equal(patchPosture({ total: 12, current: 10, outdated: 1, unknown: 1 }, true), 'action-needed')
})
