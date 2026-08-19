/**
 * The rules the three admin write forms are not allowed to get wrong.
 *
 * These are the checks whose failure would be invisible until an operator hit it: a
 * form that lets a tenant be onboarded uncapped, a tenant admin handed a control the
 * server will 403, a budget row saved with nothing on it, or a refusal rendered as
 * "something went wrong" over the top of the sentence the backend actually sent.
 *
 * Every one of them is enforced server-side as well — that is the point. What is
 * tested here is that the browser *reflects* the same rule, because a control that
 * disagrees with the server reads as a broken console rather than as a boundary.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { serverDetail } from '../../src/lib/api/apiError.ts'
import {
  adminTier,
  budgetBody,
  canChooseTenant,
  checkBudgetDraft,
  checkTenantDraft,
  checkUserDraft,
  capRefusalWarning,
  isWellFormed,
  refusalSentence,
  scopeVerdict,
  tenantBody,
  userBody,
  writableScopes,
} from '../../src/components/admin/adminForms.ts'

/** A budget draft with every box blank, for the tests to fill one field of. */
const BLANK_BUDGET = {
  scopeType: 'user',
  scopeId: '',
  window: 'day',
  usdCap: '',
  tokenCap: '',
  rpm: '',
  tpm: '',
}

/** A user draft that passes, for the tests to break one field of. */
const GOOD_USER = {
  username: 'a.rao',
  role: 'client',
  tenantId: '',
  email: '',
  password: 'correct-horse',
}

test('an unknown fine role gets no write control, not every write control', () => {
  assert.equal(adminTier('platform_admin'), 'platform')
  assert.equal(adminTier('tenant_admin'), 'tenant')
  for (const other of ['client', 'devops', 'ai_team', null, undefined]) {
    assert.equal(adminTier(other), 'none', `${other} must not be treated as an admin`)
  }
})

test('a tenant cannot be onboarded uncapped, and zero is not a cap', () => {
  const uncapped = checkTenantDraft({ name: 'Acme', usdCap: '', window: 'day' })
  assert.ok(uncapped.usdCap, 'a missing cap must be a problem — the route answers 422')
  assert.match(uncapped.usdCap, /uncapped/i, 'and it must say why, not just that it is invalid')

  assert.ok(checkTenantDraft({ name: 'Acme', usdCap: '0', window: 'day' }).usdCap)
  assert.ok(checkTenantDraft({ name: 'Acme', usdCap: '100001', window: 'day' }).usdCap)
  assert.ok(checkTenantDraft({ name: '  ', usdCap: '500', window: 'day' }).name)

  const good = { name: '  Acme  ', usdCap: '500', window: 'month' }
  assert.ok(isWellFormed(checkTenantDraft(good)))
  assert.deepEqual(tenantBody(good), { name: 'Acme', usd_cap: 500, window: 'month' })
})

test('a tenant admin is never offered a tenant to put a user in', () => {
  assert.equal(canChooseTenant('platform'), true)
  assert.equal(canChooseTenant('tenant'), false)

  // Even if a draft carried one — a stale value, a hand-edited state — the body a
  // tenant admin sends never asserts a tenant. The isolation key is the server's.
  const meddled = { ...GOOD_USER, tenantId: '99' }
  assert.equal(userBody(meddled, 'tenant').tenant_id, null)
  assert.equal(userBody(meddled, 'platform').tenant_id, 99)

  // The platform scope is a real choice, not an empty field.
  assert.equal(userBody(GOOD_USER, 'platform').tenant_id, null)
})

test('a user is only well-formed when they could actually sign in', () => {
  assert.ok(isWellFormed(checkUserDraft(GOOD_USER, 'tenant')))
  assert.ok(checkUserDraft({ ...GOOD_USER, username: '' }, 'tenant').username)
  assert.ok(checkUserDraft({ ...GOOD_USER, username: 'a rao' }, 'tenant').username)
  assert.ok(checkUserDraft({ ...GOOD_USER, password: '' }, 'tenant').password)
  assert.ok(checkUserDraft({ ...GOOD_USER, password: 'short' }, 'tenant').password)
  assert.ok(checkUserDraft({ ...GOOD_USER, email: 'not-an-email' }, 'tenant').email)
  // A blank email is a real choice — sign-in is by username.
  assert.ok(isWellFormed(checkUserDraft({ ...GOOD_USER, email: '' }, 'tenant')))
  assert.equal(userBody({ ...GOOD_USER, email: '' }, 'tenant').email, null)
})

test('a tenant admin cannot set their own tenant’s cap, and is told whose it is', () => {
  // §7.16 row 2 — `writable_by: platform` at tenant scope.
  const refused = scopeVerdict('tenant', 'tenant')
  assert.equal(refused.writable, false)
  assert.match(refused.reason, /Aegis sets your tenant/i)

  assert.equal(scopeVerdict('user', 'tenant').writable, true, 'their users are theirs')
  assert.equal(scopeVerdict('tenant', 'platform').writable, true)
  assert.equal(scopeVerdict('user', 'none').writable, false)

  assert.deepEqual(writableScopes('tenant'), ['user'])
  assert.deepEqual(writableScopes('platform'), ['tenant', 'user'])

  // And the check refuses the draft too, so the button cannot post it.
  const draft = { ...BLANK_BUDGET, scopeType: 'tenant', scopeId: '1', usdCap: '999' }
  assert.ok(checkBudgetDraft(draft, 'tenant').scopeType)
  assert.ok(isWellFormed(checkBudgetDraft(draft, 'platform')))
})

test('a budget with no cap on it is not a budget, and a blank box is null not zero', () => {
  const empty = checkBudgetDraft({ ...BLANK_BUDGET, scopeId: '7' }, 'tenant')
  assert.ok(empty.usdCap, 'a row with nothing capped would read as uncapped on every dimension')

  assert.ok(checkBudgetDraft({ ...BLANK_BUDGET, usdCap: '5' }, 'tenant').scopeId, 'no target, no cap')
  assert.ok(checkBudgetDraft({ ...BLANK_BUDGET, scopeId: '7', rpm: '0' }, 'tenant').rpm)

  const body = budgetBody({ ...BLANK_BUDGET, scopeId: '7', usdCap: '12.5', rpm: '60' })
  assert.deepEqual(body, {
    scope_type: 'user',
    scope_id: 7,
    window: 'day',
    usd_cap: 12.5,
    token_cap: null,
    rpm: 60,
    tpm: null,
  })
})

test('a sub-cap above the tenant cap is warned about before the server refuses it', () => {
  // `effective_limits` clamps a user cap inward, so the row saves and never binds.
  assert.match(capRefusalWarning(900, 500), /the server will refuse it/)
  assert.equal(capRefusalWarning(400, 500), null)
  assert.equal(capRefusalWarning(500, 500), null)
  assert.equal(capRefusalWarning(900, null), null, 'nothing known to clamp against, nothing to say')
  assert.equal(capRefusalWarning(null, 500), null)
})

test('the server’s own reason survives the trip, whatever shape it arrived in', () => {
  // The refusal that is the isolation story showing its work.
  assert.equal(
    serverDetail({ detail: 'A tenant-admin may only create users in its own tenant.' }),
    'A tenant-admin may only create users in its own tenant.',
  )
  // A bare phrase still leaves as a sentence.
  assert.equal(serverDetail({ detail: "Tenant 'Acme' already exists" }), "Tenant 'Acme' already exists.")
  // Pydantic's 422, field-first so it reads as an instruction.
  assert.equal(
    serverDetail({ detail: [{ loc: ['body', 'usd_cap'], msg: 'Input should be greater than 0' }] }),
    'usd_cap: Input should be greater than 0.',
  )
  // Nothing usable falls through, so the status table stays the floor.
  for (const junk of [null, undefined, 'plain text', {}, { detail: [] }, { detail: '  ' }]) {
    assert.equal(serverDetail(junk), null)
  }

  // And whatever reaches the form, it never says "something went wrong".
  assert.equal(refusalSentence(new Error('A tenant-admin may only set budgets for its own tenant.')),
    'A tenant-admin may only set budgets for its own tenant.')
  assert.match(refusalSentence('not an error'), /gave no reason/)
})
