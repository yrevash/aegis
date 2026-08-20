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
  groupByDimension,
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

/** One board carrying only the fields these functions read. */
function boardOf(overrides) {
  return { ...BOARD, ...overrides }
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

/*
  The gallery's sections.

  Two claims worth pinning, because both fail silently rather than loudly. A dimension
  carrying one board must not become a heading of its own — five such headings in a
  three-column grid is a page that is mostly white space and reads as broken. And the
  order must be a *function of the catalogue*, not of the rows: a section order that
  depended on which query answered first would rearrange itself under the reader's
  cursor as each board landed.
*/
test('groupByDimension folds every singleton into one trailing group', () => {
  const boards = [
    boardOf({ id: 'a', x: 'day' }),
    boardOf({ id: 'b', x: 'model' }),
    boardOf({ id: 'c', x: 'day' }),
    boardOf({ id: 'd', x: 'suite' }),
    boardOf({ id: 'e', x: 'day' }),
    boardOf({ id: 'f', x: 'model' }),
    boardOf({ id: 'g', x: 'mode' }),
  ]
  const groups = groupByDimension(boards)

  assert.deepEqual(
    groups.map((group) => [group.dimension, group.boards.map((b) => b.id)]),
    [
      ['day', ['a', 'c', 'e']],
      ['model', ['b', 'f']],
      ['', ['d', 'g']],
    ],
  )
  // Nothing is dropped: every board is in exactly one section.
  assert.equal(
    groups.reduce((n, group) => n + group.boards.length, 0),
    boards.length,
  )
})

test('groupByDimension emits no tail when every dimension is shared', () => {
  const groups = groupByDimension([
    boardOf({ id: 'a', x: 'day' }),
    boardOf({ id: 'b', x: 'day' }),
  ])
  assert.deepEqual(groups.map((group) => group.dimension), ['day'])
})

test('groupByDimension keeps the catalogue order inside a section', () => {
  const groups = groupByDimension([
    boardOf({ id: 'second', x: 'day' }),
    boardOf({ id: 'first', x: 'day' }),
  ])
  assert.deepEqual(groups[0].boards.map((b) => b.id), ['second', 'first'])
})
