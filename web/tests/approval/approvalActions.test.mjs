/**
 * The one claim the approval gate makes: you authorise exactly what you were shown.
 *
 * This is the Phase 5 defect at the layer a human reads. The backend was fixed to
 * enumerate every call one approval runs (`7285bd6`); the card rendered `action`, the
 * single highest-risk representative, so three lanes proposing three writes produced a
 * dialog naming one and an Approve that ran all three. The rule under test is that the
 * rendered list and the executed list are the same list, and that the sentence beside
 * the buttons counts it correctly.
 *
 * Not one test per field. Three cases, because there are three ways this goes wrong: the
 * fan-out that hides two writes, the ordinary single-call run that must not grow
 * ceremony, and the wire that carries no list at all.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { readApproval } from '../../src/components/approval/approvalActions.ts'

/** An `approval_required` event carrying only the fields the gate reads. */
function approvalOf(overrides) {
  return {
    type: 'approval_required',
    run_id: 'r1',
    seq: 7,
    approval_id: 'ap-1',
    action: 'crm.delete_account',
    args: { account_id: 42 },
    risk: 'high',
    rationale: 'Three writes were proposed this turn; the highest-risk one deletes an account.',
    actions: [],
    ...overrides,
  }
}

test('a fan-out gate shows every call it authorises, not the representative alone', () => {
  const view = readApproval(
    approvalOf({
      actions: [
        { id: 'c1', name: 'crm.delete_account', args: { account_id: 42 }, risk: 'high' },
        { id: 'c2', name: 'billing.issue_refund', args: { amount_usd: 1200 }, risk: 'high' },
        { id: 'c3', name: 'email.send', args: { to: 'ops@acme.test' }, risk: 'medium' },
      ],
    }),
  )

  assert.deepEqual(
    view.actions.map((a) => a.name),
    ['crm.delete_account', 'billing.issue_refund', 'email.send'],
    'approving runs three calls, so the gate names three calls',
  )
  assert.equal(view.many, true)
  assert.equal(view.representativeOnly, false)
  assert.match(view.summary, /all 3 of these calls/, 'the sentence counts what will run')

  // Each call keeps its own arguments and its own risk — a medium call must not inherit
  // the gate's `high` headline, and a high one must not be softened by a medium sibling.
  assert.deepEqual(view.actions[1].args, { amount_usd: 1200 })
  assert.deepEqual(
    view.actions.map((a) => a.risk),
    ['high', 'high', 'medium'],
  )
})

test('a single-call run reads as one action, with no count and no plural', () => {
  const view = readApproval(
    approvalOf({
      actions: [{ id: 'c1', name: 'crm.delete_account', args: { account_id: 42 }, risk: 'high' }],
    }),
  )

  assert.equal(view.actions.length, 1)
  assert.equal(view.many, false, 'the common case must not grow ceremony')
  assert.equal(view.summary, 'Approving runs this one call.')
})

test('a gate whose wire carried no usable list still shows the call it named', () => {
  const empty = readApproval(approvalOf({ actions: [] }))
  assert.equal(empty.representativeOnly, true)
  assert.deepEqual(
    empty.actions.map((a) => a.name),
    ['crm.delete_account'],
    'the representative is the fallback, never a blank gate',
  )
  assert.deepEqual(empty.actions[0].args, { account_id: 42 })

  // `actions` is `list[dict]` on the wire: a member naming no tool is not a call, and a
  // list of nothing but those is the same as no list.
  const junk = readApproval(approvalOf({ actions: [{ id: 'c1' }, { args: { a: 1 } }] }))
  assert.equal(junk.representativeOnly, true)
  assert.deepEqual(
    junk.actions.map((a) => a.name),
    ['crm.delete_account'],
  )
})

/**
 * The durable inbox row shape (`ApprovalInboxRow`), carrying only what the rule reads.
 *
 * The fourth way this goes wrong, and it is the §7.1 one: the *async* queue. A gate
 * decided out-of-band from the inbox runs the same calls as one decided live in the
 * run, and it was read from a different object — so "the rendered list is the executed
 * list" held on one surface and was never checked on the other. `readApproval` takes
 * both shapes for exactly this reason; if it ever stops doing so, this fails rather
 * than the inbox quietly showing one write of three.
 */
function inboxRowOf(overrides) {
  return {
    id: 'ap-9',
    run_id: 'r9',
    tenant_id: 3,
    action: 'crm.delete_account',
    args: { account_id: 42 },
    risk: 'high',
    rationale: 'A fan-out proposed three writes.',
    status: 'pending',
    actions: [],
    ...overrides,
  }
}

/** Project an inbox row onto the gate shape, exactly as `ApprovalInbox` does. */
function asGate(row) {
  return {
    approval_id: row.id,
    action: row.action,
    args: row.args,
    risk: row.risk,
    actions: row.actions,
  }
}

test('a parked gate in the inbox authorises the same calls the live card showed', () => {
  const view = readApproval(
    asGate(
      inboxRowOf({
        actions: [
          { id: 'c1', name: 'crm.delete_account', args: { account_id: 42 }, risk: 'high' },
          { id: 'c2', name: 'billing.issue_refund', args: { amount: 900 }, risk: 'medium' },
          { id: 'c3', name: 'crm.notify_owner', args: {}, risk: 'low' },
        ],
      }),
    ),
  )

  assert.deepEqual(
    view.actions.map((a) => a.name),
    ['crm.delete_account', 'billing.issue_refund', 'crm.notify_owner'],
  )
  assert.equal(view.many, true)
  assert.equal(view.summary, 'Approving runs all 3 of these calls.')
})

test('a parked gate written before the actions column still shows the call it named', () => {
  // A row enqueued before `approvals.actions` existed reads back as an empty list.
  // Falling back to the representative is what stops that row rendering as a gate
  // with nothing in it — an empty card is the one thing a reviewer cannot act on.
  const view = readApproval(asGate(inboxRowOf({ actions: [] })))

  assert.equal(view.representativeOnly, true)
  assert.deepEqual(
    view.actions.map((a) => a.name),
    ['crm.delete_account'],
  )
  assert.equal(view.actions[0].id, 'ap-9')
  assert.equal(view.many, false)
  assert.equal(view.summary, 'Approving runs this one call.')
})
