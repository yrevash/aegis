/**
 * Who the live gate offers Approve to.
 *
 * The defect these cover: `ApprovalCard` drew Approve and Reject for whoever was
 * looking, and both decision endpoints are `require_admin`. A tenant's own analyst
 * parked a gate from the console, pressed Approve, and got
 * *"This action requires the admin role."* over a run that stayed parked.
 *
 * Two claims are load-bearing and only two. **The button is withheld from a principal
 * the guard refuses** — otherwise the fix did not happen. And **it is not withheld from
 * one the guard admits** — otherwise the fix broke the feature for the people the
 * feature is for, which is the failure mode of every gate-the-UI change.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  ANOTHER_TENANT,
  NOT_AN_ADMIN,
  NOT_SIGNED_IN,
  NOT_YOUR_TENANT,
  decisionRefusal,
  liveGateRefusal,
} from '../../src/components/approval/decisionPolicy.ts'

/** A session, shaped as `AuthContext` persists one. */
function session(overrides = {}) {
  return {
    role: 'admin',
    token: 't',
    username: 'northwind.admin',
    tenantId: 1,
    fineRole: 'tenant_admin',
    userId: 6,
    ...overrides,
  }
}

test('the analyst who collected the 403 is not offered the control', () => {
  // `northwind.analyst`: ai_team, pinned to tenant 1. Coarse role is not `admin`, so
  // `require_admin` refuses the decision — before any question of ownership.
  const analyst = session({ role: 'ai_team', fineRole: 'ai_team', username: 'northwind.analyst' })
  assert.equal(liveGateRefusal(analyst), NOT_AN_ADMIN)
  // And a client, on the same rule and for the same reason.
  assert.equal(liveGateRefusal(session({ role: 'client', fineRole: 'client' })), NOT_AN_ADMIN)
})

test('the roles that do have the capability keep it', () => {
  // The half that matters as much: a tenant admin decides its own tenant's gates, and
  // platform staff decide the un-tenanted gates their own runs raise. Withholding the
  // control from these is the same defect wearing the opposite sign.
  assert.equal(liveGateRefusal(session()), null)
  assert.equal(
    liveGateRefusal(session({ tenantId: null, fineRole: 'platform_admin', username: 'admin' })),
    null,
  )
})

test('ownership is the second question, and it is asked the server’s way', () => {
  const platform = session({ tenantId: null, fineRole: 'platform_admin' })
  // §7.1's headline defect: the operator of Aegis voting on a tenant's business gate.
  assert.equal(decisionRefusal(platform, 2), NOT_YOUR_TENANT)
  assert.equal(decisionRefusal(platform, null), null)
  // A tenant admin decides its own and nobody else's.
  assert.equal(decisionRefusal(session({ tenantId: 1 }), 1), null)
  assert.equal(decisionRefusal(session({ tenantId: 1 }), 2), ANOTHER_TENANT)
  // An un-tenanted *gate* is not "a gate belonging to my tenant" for a pinned admin:
  // the two nulls mean different things and are never equated.
  assert.equal(decisionRefusal(session({ tenantId: 1 }), null), ANOTHER_TENANT)
})

test('signed out is refused, never granted by default', () => {
  assert.equal(liveGateRefusal(null), NOT_SIGNED_IN)
  assert.equal(decisionRefusal(null, null), NOT_SIGNED_IN)
})
