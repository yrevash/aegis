/**
 * The landing page's chain of custody must stay tied to the running system.
 *
 * The chain is the one artefact on the public page that a jury reads as a
 * description of the product, and it is written by hand — so its two failure
 * modes are the ones worth a test, and only those two.
 *
 * **It must not print a figure.** Every stage label, rail id and `decided_by`
 * value in `chainOfCustody.ts` is lifted from the backend, and none of it is a number a
 * run measured. A count, a latency or a cost pasted into that file would be the
 * exact thing this product refuses — a plausible figure on a page arguing that
 * plausible figures are never rendered — and it would be invisible in review
 * because it would look completely at home.
 *
 * **It must not loop.** The chain plays itself in once on load. `nextIndex`
 * returning to zero would turn the hero into an infinite ambient animation, which
 * DESIGN.md §6 rules out by name, and nothing on screen would look broken.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { CHAIN, FINAL_INDEX, nextIndex } from '../../src/components/landing/chainOfCustody.ts'

test('the chain advances once and stops rather than wrapping', () => {
  const visited = []
  let index = 0
  visited.push(index)

  for (let guard = 0; guard < 100; guard += 1) {
    const next = nextIndex(index)
    if (next === null) break
    index = next
    visited.push(index)
  }

  assert.equal(nextIndex(FINAL_INDEX), null, 'the last link must be terminal')
  assert.deepEqual(
    visited,
    CHAIN.map((_, i) => i),
    'the chain visits every link once, in order, and does not return to the first',
  )
})

test('no link carries a measured figure', () => {
  // Anything that reads as a quantity: a bare number, a percentage, a duration, a
  // cost. The allowlist is the handful of structural integers that are *not*
  // measurements — the `01`-style ordinals are generated, so these are the only
  // literals in the file, and each one is a declared ceiling or a fixed count.
  const DECLARED = new Set([
    'max_parallel_agents: 4', // AgentConfig's ceiling, not a run's width
    'Approving runs all 3 of these calls. Rejecting ends the run without running any of them.', // the product's own string, verbatim
  ])

  const QUANTITY = /\b\d[\d,.]*\s*(?:%|ms|s\b|k\b|USD|\$)|\b\d{2,}\b/

  const offenders = []
  for (const link of CHAIN) {
    const strings = [link.label, link.node, link.note, ...link.record]
    if (link.quote != null) strings.push(link.quote)
    for (const lane of link.lanes ?? []) strings.push(lane.label, lane.remit)

    for (const text of strings) {
      if (DECLARED.has(text)) continue
      if (QUANTITY.test(text)) offenders.push(`${link.id}: ${text}`)
    }
  }

  assert.deepEqual(
    offenders,
    [],
    'the chain describes the shape of a run, never a run’s measurements — a ' +
      'figure here would be invented, because no run produced it',
  )
})
