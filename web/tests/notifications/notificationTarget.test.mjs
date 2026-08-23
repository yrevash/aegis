/**
 * Where an alert sends the person who clicked it.
 *
 * This is the arithmetic behind the whole feature, and it has exactly one failure mode
 * that matters: rendering a link to a section the reader's portal does not mount. The
 * console's route guard does not error on that — it redirects the session home, with no
 * message — so the defect is *invisible in the browser* and reads as a dead button. It
 * is what shipped, for four portals out of five, for the entire life of the bell.
 *
 * The second claim tested here is that nothing already in the `notifications` table
 * became unreadable: rows written before the contract changed carry an absolute
 * `/app/tenant_admin/…` path, and they still have to resolve — for whoever is looking.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveNotificationTarget } from '../../src/lib/notificationTarget.ts'

test('a portal-relative target resolves against the viewer’s own portal', () => {
  // The same stored row, read by three different principals. One href, three paths.
  for (const portal of ['tenant_admin', 'platform_admin', 'ai_team']) {
    const target = resolveNotificationTarget('jobs?document=25', portal)
    assert.equal(target.kind, 'link')
    assert.equal(target.href, `/app/${portal}/jobs?document=25`)
    assert.equal(target.section, 'jobs')
  }
})

test('the entity survives the resolution — this is the point of the feature', () => {
  const target = resolveNotificationTarget('approvals?approval=abc123', 'client')
  assert.equal(target.kind, 'link')
  assert.equal(target.href, '/app/client/approvals?approval=abc123')
})

test('a section the viewer’s portal does not mount is NOT rendered as a link', () => {
  // The regression that matters. `client` has no Jobs section, so the old absolute
  // `/app/tenant_admin/jobs` bounced that reader to their dashboard in silence.
  const target = resolveNotificationTarget('jobs?document=25', 'client')
  assert.equal(target.kind, 'elsewhere')
  assert.equal(target.section, 'jobs')
  assert.ok(target.label.length > 0, 'the row must be able to name the screen it means')
  assert.equal(target.href, undefined, 'an unreachable section must carry no href')
})

test('devops gets no link to a tenant’s approvals, and platform staff do', () => {
  assert.equal(resolveNotificationTarget('approvals?approval=x', 'devops').kind, 'elsewhere')
  assert.equal(resolveNotificationTarget('approvals?approval=x', 'platform_admin').kind, 'link')
})

test('rows written before the contract changed still resolve, for the reader', () => {
  // Legacy absolute paths are in the table right now. The stale portal segment is
  // dropped rather than obeyed — obeying it is the bug.
  const target = resolveNotificationTarget('/app/tenant_admin/jobs', 'platform_admin')
  assert.equal(target.kind, 'link')
  assert.equal(target.href, '/app/platform_admin/jobs')
  assert.equal(resolveNotificationTarget('/app/tenant_admin/jobs', 'client').kind, 'elsewhere')
})

test('nothing is linked before the session has hydrated', () => {
  // A path built on a guessed portal is exactly the redirect being fixed.
  assert.equal(resolveNotificationTarget('jobs?document=25', null).kind, 'elsewhere')
})

test('an absent, empty or off-site target is nowhere, not a guess', () => {
  for (const href of [null, undefined, '', '   ', 'https://example.com/app/x/jobs', '//evil.test']) {
    assert.equal(
      resolveNotificationTarget(href, 'tenant_admin').kind,
      'none',
      `${JSON.stringify(href)} must not become a link`,
    )
  }
})

test('a slug no portal mounts is nowhere — not "on another portal"', () => {
  // Saying "Wombats is not on your portal" about a section that does not exist would
  // be the console inventing a screen.
  assert.equal(resolveNotificationTarget('wombats?id=1', 'tenant_admin').kind, 'none')
  assert.equal(resolveNotificationTarget('/app/tenant_admin', 'tenant_admin').kind, 'none')
})
