/**
 * The audit screen's analytics.
 *
 * The load-bearing claim on that screen is that **every figure describes the rows the
 * server actually returned, and says so** — `GET /audit` is filtered and paged
 * server-side, so a header that quietly read as "the whole trail" would be the one lie
 * the surface exists to prevent. These exercise the three places that could tell it:
 *
 * - the trend's axis comes from the data's own window, not from the clock, so a filter
 *   that matched last Tuesday does not draw an empty "last 12 hours" and read as idle;
 * - a family is the segment before the *first* separator, because the vocabulary is
 *   namespaced with both a dot and a colon at once;
 * - a percentage never rounds up to 100 from below, because "100% refused" when one
 *   request got through is a false statement about a governance control.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

const {
  actionFamily,
  auditPulse,
  auditTrend,
  familyPrefix,
  percent,
  tallyBy,
  windowSentence,
} = await import('../../src/components/audit/insights.ts')

/** One audit row, with only the fields these read spelled out. */
function row(over = {}) {
  return {
    id: 1,
    ts: '2026-08-20T10:00:00Z',
    action: 'query.start',
    actor: 'admin',
    model: null,
    trace_id: null,
    approved_by: null,
    outcome: 'completed',
    ...over,
  }
}

test('a family is the segment before whichever separator comes first', () => {
  assert.equal(actionFamily('guardrail.input'), 'guardrail')
  assert.equal(actionFamily('tool:search_docs'), 'tool')
  assert.equal(actionFamily('report.export'), 'report')
  // No separator at all is its own family, not an empty string.
  assert.equal(actionFamily('login'), 'login')
})

test('the prefix that re-selects a family keeps its separator', () => {
  // `guardrail` alone would also match a hypothetical `guardrails_v2`; the server
  // matches `action_prefix` literally, so a lens that widened would be worse than none.
  assert.equal(familyPrefix('guardrail.input'), 'guardrail.')
  assert.equal(familyPrefix('tool:send_email'), 'tool:')
  assert.equal(familyPrefix('login'), 'login')
})

test('the pulse counts the rows in hand and nothing beyond them', () => {
  const rows = [
    row({ id: 1, action: 'query.start', actor: 'admin', trace_id: 't1', model: 'gpt-4o' }),
    row({ id: 2, action: 'guardrail.input', actor: 'admin', outcome: 'blocked' }),
    row({ id: 3, action: 'approval.decision', actor: 'reviewer', approved_by: 'yash' }),
    row({ id: 4, action: 'documents.upload', actor: '', trace_id: '' }),
  ]
  const pulse = auditPulse(rows)
  assert.equal(pulse.total, 4)
  assert.equal(pulse.blocked, 1)
  assert.equal(pulse.completed, 3)
  assert.equal(pulse.approved, 1)
  assert.equal(pulse.traced, 1)
  // An empty actor is not a principal, and an empty trace id is not a trace.
  assert.equal(pulse.actors, 2)
  assert.equal(pulse.actions, 4)
  assert.equal(pulse.models, 1)
  assert.equal(pulse.refusalRate, 0.25)
})

test('an empty set produces zeroes rather than a division by zero', () => {
  const pulse = auditPulse([])
  assert.equal(pulse.total, 0)
  assert.equal(pulse.refusalRate, 0)
  assert.equal(pulse.traceRate, 0)
})

test('a tally carries each group’s refusals and sorts by volume', () => {
  const rows = [
    row({ action: 'query.start' }),
    row({ action: 'query.start' }),
    row({ action: 'guardrail.input', outcome: 'blocked' }),
    row({ action: null }),
  ]
  const tally = tallyBy(rows, (entry) => entry.action)
  assert.deepEqual(tally, [
    { name: 'query.start', total: 2, blocked: 0 },
    { name: 'guardrail.input', total: 1, blocked: 1 },
  ])
})

test('the trend’s axis is the window the rows themselves span', () => {
  const rows = [
    row({ id: 1, ts: '2026-08-20T10:00:00Z' }),
    row({ id: 2, ts: '2026-08-20T10:30:00Z', outcome: 'blocked' }),
    row({ id: 3, ts: '2026-08-20T11:00:00Z' }),
  ]
  const trend = auditTrend(rows, 4)
  assert.equal(trend.buckets.length, 4)
  assert.equal(trend.from, Date.parse('2026-08-20T10:00:00Z'))
  assert.equal(trend.to, Date.parse('2026-08-20T11:00:00Z'))
  // Every row lands in exactly one bucket — the newest one included, rather than
  // falling off the end of a half-open range.
  assert.equal(
    trend.buckets.reduce((n, bucket) => n + bucket.total, 0),
    3,
  )
  assert.equal(
    trend.buckets.reduce((n, bucket) => n + bucket.blocked, 0),
    1,
  )
  // A quiet slice stays in the axis as a gap rather than being closed up.
  assert.ok(trend.buckets.some((bucket) => bucket.total === 0))
})

test('no rows means no window, said as words rather than drawn as an idle chart', () => {
  const trend = auditTrend([], 8)
  assert.deepEqual(trend.buckets, [])
  assert.equal(trend.from, null)
  assert.equal(trend.to, null)
  assert.equal(windowSentence(trend), 'no rows in this view')
})

test('a percentage never rounds up to 100 from below, or down to 0 from above', () => {
  assert.equal(percent(0), '0%')
  assert.equal(percent(1), '100%')
  assert.equal(percent(0.999), '>99%')
  assert.equal(percent(0.0001), '<1%')
  assert.equal(percent(0.25), '25%')
  assert.equal(percent(Number.NaN), '0%')
})
