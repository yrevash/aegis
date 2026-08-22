/**
 * The confirmation on the approvals queue names what it is deciding.
 *
 * Approve and Reject were single-click in a list that re-sorts itself as SLA deadlines
 * pass; approving executes a real tool action and neither decision can be taken back
 * from that screen. The claim under test is not "a dialog appears" — it is that the
 * question names **every call the decision covers**, which is the same rule the consent
 * sentence beside it already keeps. A fan-out gate that asks about one of its three
 * writes is the Phase 5 defect wearing a confirmation.
 *
 * Three cases, because there are three ways it goes wrong: the fan-out that names one,
 * the reject that has to state what it costs, and the gate whose wire named nothing.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { decisionQuestion, readApproval } from '../../src/components/approval/approvalActions.ts'

/** A gate authorising three calls, as the inbox row hands it over. */
const fanOut = readApproval({
  approval_id: 'gate-1',
  action: 'issue_supplier_credit',
  args: { amount_usd: 4200 },
  risk: 'high',
  actions: [
    { id: 'a', name: 'issue_supplier_credit', args: { amount_usd: 4200 }, risk: 'high' },
    { id: 'b', name: 'notify_account_owner', args: { channel: 'email' }, risk: 'low' },
    { id: 'c', name: 'update_request_status', args: { status: 'resolved' }, risk: 'high' },
  ],
})

test('approving names every call it would run, not just the representative', () => {
  const question = decisionQuestion('approve', fanOut, 'Tenant #1')
  for (const call of ['issue_supplier_credit', 'notify_account_owner', 'update_request_status']) {
    assert.ok(question.includes(call), `"${call}" missing from: ${question}`)
  }
  // Whose system it runs against, and that it is one-way.
  assert.ok(question.includes('Tenant #1'))
  assert.match(question, /cannot be recalled/)
})

test('rejecting names the same calls and states what it costs', () => {
  const question = decisionQuestion('reject', fanOut, 'Tenant #1')
  assert.ok(question.includes('issue_supplier_credit'))
  assert.ok(question.includes('update_request_status'))
  // The failure mode of a reject confirmation is implying it is the safe no-op.
  assert.match(question, /parked run ends/)
})

test('a gate that enumerated nothing still asks, without inventing a call', () => {
  // `action: ''` with no list is the only shape `readApproval` reads as zero calls.
  const empty = readApproval({
    approval_id: 'gate-2',
    action: '',
    args: {},
    risk: 'high',
    actions: [],
  })
  assert.equal(empty.actions.length, 0)
  const question = decisionQuestion('approve', empty, 'Aegis itself')
  assert.match(question, /this gate/)
  assert.ok(question.includes('Aegis itself'))
})
