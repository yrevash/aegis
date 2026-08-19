/**
 * The database console's pure helpers.
 *
 * Two of these carry real weight and are the reason this file exists rather than a
 * rendered-tree test: `coverage` is what stops a reader treating a bounded answer as a
 * complete one, and `emptyMessage` is what stops "no rows" being read as "the filter is
 * broken" — or, far worse, the other way round.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

const { cell, coverage, defaultOrder, emptyMessage, estimate, grouped, isAbsent, nextCursor } =
  await import('../../src/components/db/dbView.ts')

const base = {
  label: 'Browse usage_ledger',
  columns: ['id', 'tenant_id', 'cost_usd'],
  rows: [
    [1, 7, 0.01],
    [2, 7, 0.02],
  ],
  rowCount: 2,
  truncated: false,
  truncationReason: '',
  durationMs: 4,
  approxBytes: 40,
  planCost: 12,
  planSummary: 'Index Scan on usage_ledger',
  scope: 'tenant 7',
  tenantFiltered: true,
  sql: 'SELECT ...',
  exactCount: null,
  queryId: 'abc',
}

test('a value renders as itself, and a NULL renders as absence', () => {
  assert.equal(cell('alpha'), 'alpha')
  assert.equal(cell(0), '0')
  assert.equal(cell(false), 'false')
  assert.equal(cell({ a: 1 }), '{"a":1}')
  assert.equal(cell(null), '—')
  assert.ok(isAbsent(null) && isAbsent(undefined) && !isAbsent(0) && !isAbsent(''))
})

test('a complete result says so, and names the scope that produced it', () => {
  const text = coverage(base)
  assert.match(text, /2 rows, complete\./)
  assert.match(text, /Scoped to tenant 7/)
})

test('a truncated result leads with the server’s own sentence about what was cut', () => {
  const text = coverage({
    ...base,
    truncated: true,
    truncationReason: 'Showing the first 100 rows; more matched.',
  })
  assert.ok(text.startsWith('Showing the first 100 rows; more matched.'))
  assert.match(text, /Scoped to tenant 7/)
})

test('a table with no tenant column says that, rather than claiming a filter', () => {
  const text = coverage({ ...base, tenantFiltered: false, scope: 'every tenant (platform-wide read)' })
  assert.match(text, /carries no tenant column/)
  assert.doesNotMatch(text, /Scoped to/)
})

test('an exact count is reported beside the page, never instead of it', () => {
  assert.match(coverage({ ...base, exactCount: 4210 }), /4,210 rows match in total\./)
})

test('an empty scoped result blames the scope, because the scope is why', () => {
  const text = emptyMessage({ ...base, rows: [], rowCount: 0 })
  assert.match(text, /for tenant 7/)
  assert.match(text, /Widen the scope/)
})

test('an empty unscoped result states the table is really empty', () => {
  const text = emptyMessage({ ...base, rows: [], rowCount: 0, tenantFiltered: false })
  assert.match(text, /real state, not a failed read/)
})

test('the keyset cursor is the last row’s ordering value, and only when more matched', () => {
  assert.equal(nextCursor(base, 'id'), null, 'a complete page has no next page')
  assert.equal(nextCursor({ ...base, truncated: true }, 'id'), '2')
  assert.equal(nextCursor({ ...base, truncated: true }, 'not_a_column'), null)
  assert.equal(nextCursor({ ...base, truncated: true, rows: [] }, 'id'), null)
})

test('a table is paged on its primary key, falling back to its first column', () => {
  assert.equal(defaultOrder({ primaryKey: ['id'], columns: [{ name: 'id' }] }), 'id')
  assert.equal(defaultOrder({ primaryKey: [], columns: [{ name: 'ts' }] }), 'ts')
  assert.equal(defaultOrder({ primaryKey: [], columns: [] }), '')
})

test('a row estimate is labelled as one', () => {
  assert.equal(estimate(12345), '~12,345 rows')
  assert.equal(estimate(0), 'no rows estimated')
})

test('tables split into the ones the tenant selector changes and the ones it does not', () => {
  const { scoped, platform } = grouped([
    { name: 'usage_ledger', tenantScoped: true },
    { name: 'tenants', tenantScoped: false },
  ])
  assert.deepEqual(scoped.map((t) => t.name), ['usage_ledger'])
  assert.deepEqual(platform.map((t) => t.name), ['tenants'])
})
