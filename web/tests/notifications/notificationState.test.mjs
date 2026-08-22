/**
 * The unread count, and the three ways it lies.
 *
 * The badge is drawn on every screen in the product, so it is the most-looked-at number
 * the console has. A count that is wrong is not a rendering defect — it is the console
 * asserting that something does or does not need attention, which is the one thing this
 * feature exists to say. These tests are the arithmetic and nothing else; the components
 * only draw what the reducer returns.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  EMPTY_NOTIFICATIONS,
  MAX_ROWS,
  badgeCount,
  bellLabel,
  notificationReducer,
  relativeAge,
  severityLabel,
  severityTone,
} from '../../src/components/notifications/notificationState.ts'

/** One notification, shaped exactly as the contract declares one. */
function row(overrides = {}) {
  return {
    id: 'n1',
    kind: 'job.succeeded',
    severity: 'info',
    title: 'Job 412 finished',
    body: '100 documents ingested.',
    entity_ref: 'job:412',
    href: '/app/platform_admin/jobs',
    created_at: '2026-08-23T10:00:00Z',
    read_at: null,
    ...overrides,
  }
}

test('the count is the server’s, not a recount of the page it sent', () => {
  // The commonest real shape: 30 rows returned, 84 unread in the feed. Recomputing
  // from `rows` would under-report by 54 and the operator would think they were caught
  // up on a backlog they cannot see.
  const state = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' }), row({ id: 'b', read_at: '2026-08-23T10:01:00Z' })],
    unread: 84,
  })
  assert.equal(state.unread, 84)
  assert.equal(state.rows.length, 2)
})

test('loaded rows come back newest first whatever order they arrived in', () => {
  const state = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    unread: 3,
    rows: [
      row({ id: 'old', created_at: '2026-08-23T08:00:00Z' }),
      row({ id: 'new', created_at: '2026-08-23T12:00:00Z' }),
      row({ id: 'mid', created_at: '2026-08-23T10:00:00Z' }),
    ],
  })
  assert.deepEqual(
    state.rows.map((r) => r.id),
    ['new', 'mid', 'old'],
  )
})

test('an arriving notification prepends and increments the count', () => {
  const loaded = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' })],
    unread: 1,
  })
  const live = notificationReducer(loaded, { type: 'arrived', row: row({ id: 'b' }) })
  assert.equal(live.unread, 2)
  assert.equal(live.rows[0].id, 'b')
  assert.deepEqual(live.arrived, ['b'])
})

test('a row that arrives already read does not move the count', () => {
  const live = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'arrived',
    row: row({ id: 'b', read_at: '2026-08-23T10:05:00Z' }),
  })
  assert.equal(live.unread, 0)
  assert.equal(live.rows.length, 1)
})

test('a replayed id after a reconnect is not counted twice', () => {
  // At-least-once is the normal guarantee for a stream like this: whatever was in
  // flight when the socket dropped is re-sent on the next one. Counting it again is
  // how a badge drifts upward all afternoon over a feed with three things in it.
  const first = notificationReducer(EMPTY_NOTIFICATIONS, { type: 'arrived', row: row({ id: 'b' }) })
  const replay = notificationReducer(first, { type: 'arrived', row: row({ id: 'b' }) })
  assert.equal(replay.unread, 1)
  assert.equal(replay.rows.length, 1)
})

test('reading is idempotent and cannot drive the count negative', () => {
  const loaded = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' })],
    unread: 1,
  })
  const once = notificationReducer(loaded, { type: 'read', id: 'a', at: 'T' })
  const twice = notificationReducer(once, { type: 'read', id: 'a', at: 'T' })
  const unknown = notificationReducer(twice, { type: 'read', id: 'nope', at: 'T' })
  assert.equal(once.unread, 0)
  assert.equal(twice.unread, 0)
  assert.equal(unknown.unread, 0)
  assert.equal(once.rows[0].read_at, 'T')
})

test('mark-all reads every row and zeroes the count, including rows off the page', () => {
  const loaded = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' }), row({ id: 'b' })],
    unread: 40,
  })
  const all = notificationReducer(loaded, { type: 'readAll', at: 'T' })
  assert.equal(all.unread, 0)
  assert.ok(all.rows.every((r) => r.read_at === 'T'))
})

test('sign-out empties the feed rather than leaving it on screen', () => {
  const loaded = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' })],
    unread: 9,
  })
  assert.deepEqual(notificationReducer(loaded, { type: 'cleared' }), EMPTY_NOTIFICATIONS)
})

test('the row list is capped, and the cap keeps the newest', () => {
  let state = EMPTY_NOTIFICATIONS
  for (let i = 0; i < MAX_ROWS + 10; i += 1) {
    state = notificationReducer(state, { type: 'arrived', row: row({ id: `n${i}` }) })
  }
  assert.equal(state.rows.length, MAX_ROWS)
  assert.equal(state.rows[0].id, `n${MAX_ROWS + 9}`)
  // The count is the feed's, so trimming the list must not trim it.
  assert.equal(state.unread, MAX_ROWS + 10)
})

test('the reducer never mutates the state it was handed', () => {
  const before = notificationReducer(EMPTY_NOTIFICATIONS, {
    type: 'loaded',
    rows: [row({ id: 'a' })],
    unread: 1,
  })
  const snapshot = JSON.stringify(before)
  notificationReducer(before, { type: 'arrived', row: row({ id: 'b' }) })
  notificationReducer(before, { type: 'read', id: 'a', at: 'T' })
  notificationReducer(before, { type: 'readAll', at: 'T' })
  assert.equal(JSON.stringify(before), snapshot)
})

test('an unrecognised severity is neutral, not an escalation', () => {
  assert.equal(severityTone('critical'), 'block')
  assert.equal(severityTone('warning'), 'risk')
  assert.equal(severityTone('info'), 'neutral')
  assert.equal(severityTone('catastrophic'), 'neutral')
  // It still gets a word, because status is never hue alone.
  assert.equal(severityLabel('catastrophic'), 'catastrophic')
  assert.equal(severityLabel('critical'), 'Critical')
})

test('the count reaches a screen reader through the label, not only the badge', () => {
  assert.equal(bellLabel(0), 'Alerts, none unread')
  assert.equal(bellLabel(1), 'Alerts, 1 unread')
  assert.equal(bellLabel(120), 'Alerts, 120 unread')
  // The badge saturates; the label does not, so the exact number is never lost.
  assert.equal(badgeCount(120), '99+')
  assert.equal(badgeCount(7), '7')
})

test('ages are compact, and a future timestamp does not become a negative age', () => {
  const now = Date.parse('2026-08-23T12:00:00Z')
  assert.equal(relativeAge('2026-08-23T11:59:50Z', now), 'now')
  assert.equal(relativeAge('2026-08-23T11:56:00Z', now), '4m')
  assert.equal(relativeAge('2026-08-23T09:00:00Z', now), '3h')
  assert.equal(relativeAge('2026-08-21T12:00:00Z', now), '2d')
  assert.equal(relativeAge('2026-08-01T12:00:00Z', now), '2026-08-01')
  assert.equal(relativeAge('2026-08-23T12:05:00Z', now), 'now')
  assert.equal(relativeAge('not a date', now), '')
})
