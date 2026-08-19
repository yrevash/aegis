/**
 * The export must contain what the screen is showing.
 *
 * A "Download CSV" button beside a chart is a promise that the file holds the thing
 * on screen. Drop a parameter on the way to the URL and the promise breaks silently:
 * the file parses, the numbers look plausible, and they are a different tenant's or a
 * different horizon's. That is the failure this asserts against.
 *
 * Note what is *not* asserted, because it is not this layer's job: `tenantId` is a
 * request, never an authority. The server re-resolves the scope from the caller's own
 * token and refuses a tenant the session may not read — see
 * `backend/tests/api/test_reports_export.py`.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { reportDownloadPath, ticketedUrl } from '../../src/lib/api/reports.ts'

test('the forecast export carries the parameters the panel is drawn with', () => {
  const path = reportDownloadPath('forecast', {
    tenantId: 3,
    metric: 'calls',
    horizon: 30,
    window: 'month',
  })
  assert.match(path, /^\/reports\/forecast\.csv\?/)
  assert.match(path, /tenant_id=3/)
  assert.match(path, /metric=calls/)
  assert.match(path, /horizon=30/)
  assert.match(path, /window=month/)
})

test('the platform aggregate names no tenant at all', () => {
  // `null` is the aggregate, and must not become `tenant_id=null` — which the server
  // would reject as a malformed int rather than reading as "every tenant".
  const path = reportDownloadPath('forecast', { tenantId: null, metric: 'spend' })
  assert.doesNotMatch(path, /tenant_id/)
})

test('audit filters travel, and forecast parameters never leak onto another report', () => {
  const audit = reportDownloadPath('audit', {
    since: '2026-08-01T00:00:00Z',
    actor: 'alice',
    horizon: 30,
  })
  assert.match(audit, /^\/reports\/audit\.csv\?/)
  assert.match(audit, /actor=alice/)
  assert.doesNotMatch(audit, /horizon/)
  assert.equal(reportDownloadPath('budget'), '/reports/budget.csv')
})

test('the ticket is appended without breaking an existing query string', () => {
  assert.match(ticketedUrl('/reports/budget.csv', 'abc'), /\/reports\/budget\.csv\?ticket=abc$/)
  assert.match(ticketedUrl('/reports/forecast.csv?metric=spend', 'a+b'), /&ticket=a%2Bb$/)
})
