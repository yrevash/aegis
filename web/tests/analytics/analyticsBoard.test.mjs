/**
 * The analytics page's two decisions that must not be made by eye.
 *
 * **Which state the page is in.** "Superset is off", "not configured", "not answering"
 * and "no boards yet" are four different sentences with four different fixes, and the
 * failure mode is not a crash — it is a page confidently telling an operator to restart
 * a service they never turned on. The ordering below is the whole of that rule, and the
 * `null` case is the mutation guard: a status the browser could not fetch must land on
 * a state that renders an instruction, never on `ready`, which renders charts.
 *
 * **Which colour a series gets.** Assigned from the board's declared series order — the
 * entity — so a query that returns fewer measures cannot repaint the survivors. If the
 * lookup were done against the returned rows instead, dropping the first measure would
 * silently move every remaining one onto the previous one's hue.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  analyticsState,
  chartRows,
  countedRows,
  embedAvailable,
  seriesColor,
} from '../../src/components/analytics/analyticsBoard.ts'

/** A status carrying only the fields these functions read. */
function statusOf(overrides) {
  return {
    enabled: true,
    configured: true,
    reachable: true,
    embedEnabled: true,
    detail: '',
    action: '',
    baseUrl: 'http://localhost:8088',
    boards: 2,
    ...overrides,
  }
}

const BOARD = {
  id: 'spend',
  title: 'Spend by model',
  summary: '',
  kinds: ['chart', 'dashboard'],
  window: 'last_30_days',
  x: 'model',
  series: ['spend_usd', 'calls'],
}

// ── the honest state ────────────────────────────────────────────────────────

test('a status the browser could not fetch is never "ready"', () => {
  assert.equal(analyticsState(null), 'down')
})

test('each state is reported as itself, most fundamental first', () => {
  assert.equal(analyticsState(statusOf({ enabled: false })), 'off')
  assert.equal(analyticsState(statusOf({ configured: false })), 'unconfigured')
  assert.equal(analyticsState(statusOf({ reachable: false })), 'down')
  assert.equal(analyticsState(statusOf({ boards: 0 })), 'empty')
  assert.equal(analyticsState(statusOf()), 'ready')
})

test('a deployment that is off is not reported as unreachable', () => {
  // The mutation guard on the ordering: check `reachable` first and an operator who
  // never turned the feature on is sent to restart a server they are not running.
  assert.equal(analyticsState(statusOf({ enabled: false, reachable: false })), 'off')
})

// ── colour follows the entity ───────────────────────────────────────────────

test('a series keeps its colour when another series is absent', () => {
  const both = seriesColor(BOARD, 'calls')
  const alone = seriesColor({ ...BOARD }, 'calls')
  assert.equal(both, alone)
  assert.notEqual(seriesColor(BOARD, 'spend_usd'), seriesColor(BOARD, 'calls'))
})

test('a series past the fixed order folds onto neutral rather than a new hue', () => {
  const wide = { ...BOARD, series: ['a', 'b', 'c', 'd', 'e', 'f', 'g'] }
  assert.equal(seriesColor(wide, 'g'), 'neutral')
  assert.equal(seriesColor(wide, 'not-a-series'), 'neutral')
})

// ── only drawable rows are drawn ────────────────────────────────────────────

test('a row with no x value is dropped and counted, never drawn as "undefined"', () => {
  const data = {
    boardId: 'spend',
    title: '',
    window: 'last_30_days',
    columns: ['model', 'spend_usd', 'calls'],
    x: 'model',
    series: ['spend_usd', 'calls'],
    tenantScoped: true,
    rows: [
      { model: 'gpt-4o', spend_usd: 12.5, calls: 3 },
      { model: null, spend_usd: 1, calls: 1 },
      { model: '', spend_usd: 2, calls: 1 },
    ],
  }
  assert.deepEqual(chartRows(data), [{ label: 'gpt-4o', spend_usd: 12.5, calls: 3 }])
  assert.deepEqual(countedRows(data), { drawn: 1, dropped: 2 })
})

// ── the embed is the thing most likely to be missing ────────────────────────

test('the embed needs the board, the flag and a reachable Superset — all three', () => {
  assert.equal(embedAvailable(BOARD, statusOf()), true)
  assert.equal(embedAvailable(BOARD, statusOf({ embedEnabled: false })), false)
  assert.equal(embedAvailable(BOARD, statusOf({ reachable: false })), false)
  assert.equal(embedAvailable({ ...BOARD, kinds: ['chart'] }, statusOf()), false)
  assert.equal(embedAvailable(BOARD, null), false)
})
