/**
 * A refused declaration must not cost the operator the form.
 *
 * The drawer closed and blanked itself the instant `onCreate` was *called*, so a 400 —
 * `audit_peer` is not a usable tool namespace, a sentence the API takes the trouble to
 * write — discarded the endpoint, the header and the credential that had just been typed.
 * And a submit that never reached the server left the previous attempt's banner standing,
 * which reads as this attempt's verdict.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

const { afterSubmit, beforeSubmit, refuseLocally } = await import(
  '../../src/components/mcp/declareForm.ts'
)

const DRAFT = {
  serverId: 'audit_peer',
  label: 'Audit peer',
  url: 'https://peer.example/mcp',
  authHeader: 'X-API-Key',
  credential: 's3cret',
}
const EMPTY = { serverId: '', label: '', url: '', authHeader: 'Authorization', credential: '' }
const REFUSAL =
  "External MCP server id 'audit_peer' is not usable as a tool namespace."

test('a refusal keeps the drawer open with every value intact, and shows the reason', () => {
  const state = { open: true, draft: DRAFT, notice: null }
  const next = afterSubmit(state, { reason: REFUSAL }, EMPTY, 'declared')

  assert.equal(next.open, true, 'the drawer closed on a refusal')
  // Every field — the credential included. Blanking it bought no safety (the value is in
  // component state either way) and cost a retype on every typo in the id beside it.
  assert.deepEqual(next.draft, DRAFT)
  assert.deepEqual(next.notice, { kind: 'error', text: REFUSAL })
})

test('acceptance is the only thing that closes and blanks the form', () => {
  const state = { open: true, draft: DRAFT, notice: null }
  const next = afterSubmit(state, { reason: null }, EMPTY, 'acme is declared.')

  assert.equal(next.open, false)
  assert.deepEqual(next.draft, EMPTY)
  assert.deepEqual(next.notice, { kind: 'ok', text: 'acme is declared.' })
})

test('a submit that never leaves the browser clears the last verdict', () => {
  // The stale-banner half: refused, then submitted with an empty id. Without the clear,
  // the operator is looking at a verdict about a different attempt.
  const refused = afterSubmit({ open: true, draft: DRAFT, notice: null }, { reason: REFUSAL }, EMPTY, 'ok')
  const cleared = beforeSubmit(refused)
  assert.equal(cleared.notice, null)

  const local = refuseLocally(cleared, 'An id is required.')
  assert.equal(local.open, true)
  assert.deepEqual(local.draft, DRAFT)
  assert.deepEqual(local.notice, { kind: 'error', text: 'An id is required.' })
})
