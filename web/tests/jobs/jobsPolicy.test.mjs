/**
 * The Jobs screen must not offer a write the backend guard makes impossible.
 *
 * `POST /jobs/{id}/requeue` and `/cancel` load the row as `id = :id AND tenant_id =
 * :caller_tenant`, and for an untenanted caller that clause is `tenant_id IS NULL` —
 * so on a platform admin's portal every one of those buttons returned 403 *"no such row
 * under tenant None"*, and `POST /documents` returned 400 *"an upload needs an owning
 * tenant"*. `lib/portal.ts` states the doctrine that forbids exactly this.
 *
 * The predicate is asserted here rather than through a rendered tree, and the mutation
 * each test is proof against is named in the test.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const { canWriteJobs, NO_TENANT_REASON } = await import(
  '../../src/components/jobs/jobsPolicy.ts'
)

const VIEW = fileURLToPath(new URL('../../src/components/jobs/JobsView.tsx', import.meta.url))

test('an untenanted principal may not write, a tenanted one may', () => {
  // The platform admin: the whole defect. Every row it can see belongs to a tenant it
  // is not, so re-queue, cancel and upload are structurally impossible for it.
  assert.equal(canWriteJobs(null), false)
  // tenant_admin and ai_team reach this screen pinned to a tenant, and their controls
  // work — verified against the running backend as northwind.admin (200, "re-queued").
  assert.equal(canWriteJobs(1), true)
  assert.equal(canWriteJobs(2), true)
})

test('the gate is the tenant pin, never the role name', () => {
  // Hardcoding `fineRole !== 'platform_admin'` would be the tempting shortcut and would
  // be wrong twice: it would keep the buttons for any *other* untenanted principal, and
  // it would withhold them from a platform admin that was one day given a tenant.
  // Tenant 0 is a tenant, and a falsy one — the other way this reverts.
  assert.equal(canWriteJobs(0), true)
})

test('the screen renders its controls behind that predicate and says why', () => {
  // A regression here is silent: the buttons come back, the screen looks richer, and
  // nothing fails until an operator clicks one and collects a 403.
  const source = readFileSync(VIEW, 'utf8')
  assert.match(source, /canWrite \? \(/, 'the row action cell is no longer gated')
  assert.match(source, /\{canWrite \? <Th className="text-right">Action<\/Th> : null\}/)
  assert.match(source, /\{NO_TENANT_REASON\}/, 'the screen no longer states the reason')
  assert.ok(NO_TENANT_REASON.length < 40, 'the reason has grown into a paragraph')
})
