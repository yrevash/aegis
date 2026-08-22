/**
 * The health strip is a picture of `/readyz`, and a picture is easy to lie with.
 *
 * The devops board used to spell every component's verdict out in prose, which is
 * unreadable but hard to falsify — the words were the data. It leads with a bar now,
 * and a bar has two failure modes that prose does not: it can paint a segment for a
 * state nothing is actually in, and it can paint a state it does not understand in
 * the colour of a state it does. Both would be read as fact at a glance, by exactly
 * the operator who no longer has the sentences to check it against.
 *
 * So: two claims, and the failure mode of each. Not one test per verdict.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { verdictSplit } from '../../src/components/ops-overview/readiness.ts'

/** A component as `/readyz` sends it — only the fields the strip reads matter. */
function component(key, status) {
  return {
    key,
    name: key,
    category: 'store',
    status,
    detail: null,
    evidence: null,
    measured_at: '2026-08-22T16:18:26+00:00',
    required: true,
  }
}

test('the strip only draws verdicts that actually occurred, at their real share', () => {
  const split = verdictSplit([
    component('postgres', 'up'),
    component('redis', 'up'),
    component('neo4j', 'down'),
    component('qdrant', 'up'),
  ])

  // The failure mode: a zero-width `degraded` segment and a red key on a legend for a
  // platform with nothing degraded in it. Three states are absent and none is drawn.
  assert.deepEqual(
    split.map((s) => s.status),
    ['up', 'down'],
  )
  assert.deepEqual(
    split.map((s) => s.count),
    [3, 1],
  )
  // Real counts over the real denominator — the widths are the data, not a rounding.
  assert.equal(split.reduce((sum, s) => sum + s.share, 0), 1)
  assert.equal(split[0].share, 0.75)

  // Nothing probed is nothing drawn, rather than a full-width bar of an invented state.
  assert.deepEqual(verdictSplit([]), [])
})

test('a verdict this build has never heard of is never painted as healthy', () => {
  // A newer backend can ship a status word this client does not know. Folding it into
  // `up` would turn an unread state into a green segment — the one direction the strip
  // must never fail in.
  const split = verdictSplit([component('postgres', 'up'), component('mystery', 'quiescing')])

  assert.deepEqual(
    split.map((s) => s.status),
    ['up', 'unknown'],
  )
  assert.equal(split.find((s) => s.status === 'up').count, 1)
})
