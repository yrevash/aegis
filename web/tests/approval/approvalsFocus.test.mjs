/**
 * What `?approval=` opens on the Approvals screen.
 *
 * The load-bearing claim is the *decided* one. The alert fires the moment a gate opens
 * and gates close, so by the time somebody clicks, the common case is that the gate is
 * already history — and the inbox's default cut is *Waiting, last 7 days*, which does
 * not contain it. Showing an empty "nothing is waiting on you" queue in that situation
 * is the console asserting something it was never told, about the one screen where the
 * difference between approved and rejected is the whole product.
 *
 * The other claim: an empty answer only means "no such gate" once the query is at its
 * widest. Until then it means the filters hid it.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  approvalFocusNote,
  resolveApprovalFocus,
} from '../../src/components/approval/approvalsFocus.ts'

/** One inbox row, shaped as `GET /approvals` sends one. */
function gate(overrides = {}) {
  return {
    id: 'a1',
    run_id: 'r1',
    tenant_id: 1,
    action: 'deactivate_account',
    args: {},
    risk: 'HIGH',
    rationale: null,
    status: 'pending',
    persona: null,
    sla_deadline: null,
    created_at: '2026-08-23T10:00:00Z',
    ml_snapshot: {},
    actions: [],
    requested_by: 4,
    decided_at: null,
    decided_by: null,
    decidable: true,
    blocked_reason: null,
    ...overrides,
  }
}

test('no parameter opens nothing and says nothing', () => {
  const focus = resolveApprovalFocus([gate()], null, true)
  assert.equal(focus.kind, 'none')
  assert.equal(approvalFocusNote(focus, false), null)
})

test('an open gate resolves to the card with the decision on it', () => {
  const focus = resolveApprovalFocus([gate({ id: 'a2' }), gate({ id: 'a1' })], 'a1', true)
  assert.equal(focus.kind, 'waiting')
  assert.equal(focus.row.id, 'a1')
  assert.match(approvalFocusNote(focus, false), /deactivate_account/)
})

test('a gate already decided says which way it went — it is not an empty inbox', () => {
  const rows = [gate({ status: 'rejected', decided_by: 'northwind.admin', decidable: false })]
  const focus = resolveApprovalFocus(rows, 'a1', true)
  assert.equal(focus.kind, 'decided')
  const note = approvalFocusNote(focus, true)
  assert.match(note, /rejected/)
  assert.match(note, /northwind\.admin/)
  assert.match(note, /Nothing is waiting on you/)
})

test('an SLA-expired gate is not attributed to a person who never decided it', () => {
  const focus = resolveApprovalFocus([gate({ status: 'expired' })], 'a1', true)
  const note = approvalFocusNote(focus, true)
  assert.match(note, /expired/)
  assert.doesNotMatch(note, / by /)
})

test('a miss on a narrow query means "keep looking", never "no such gate"', () => {
  // This is the difference between widening the filters and lying to the reader.
  const focus = resolveApprovalFocus([], 'a1', false)
  assert.equal(focus.kind, 'searching')
  assert.doesNotMatch(approvalFocusNote(focus, false), /no such gate/i)
})

test('a miss on the widest query says so, and does not pick a reason', () => {
  const focus = resolveApprovalFocus([gate({ id: 'other' })], 'a1', true)
  assert.equal(focus.kind, 'missing')
  const note = approvalFocusNote(focus, true)
  assert.match(note, /a1/)
  assert.match(note, /every status/)
  // "Another tenant's" and "never existed" are one answer on purpose — the server
  // narrows this list, so the browser genuinely cannot tell them apart, and claiming
  // either would be an invention.
  assert.match(note, /another tenant/)
  assert.match(note, /no such gate/)
})
