/**
 * A lane's thinking must read as decisions, not as one run-on paragraph.
 *
 * What the owner saw on a real run, verbatim from their paste:
 *
 * > Let me first look up the relevant service request...Let me search for any relevant
 * > knowledge documents...Let me start by searching...
 *
 * Three separate decisions welded into one wall. The reason the console could not split
 * them back apart is in the data: the wire emits one `reasoning` event per sentence and
 * puts **no separator between them**, so a later run produced `…help answer this
 * questionLet me look into this properly.First, I need to check…` — one join with no
 * whitespace and no punctuation at all. The chunk boundary is the only break there is,
 * which is why the split reads events rather than text.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { reasoningSteps } from '../../src/components/console/laneStream.ts'

test('the run-on wall becomes one step per chunk, joins and all', () => {
  // The exact three chunks a real run streamed, including the two bad joins.
  const steps = reasoningSteps([
    'Let me start by retrieving the relevant service request records and any knowledge documents that might help answer this question',
    'Let me look into this properly.',
    "First, I need to check if there are any relevant service requests on the desk that match this customer's issue.",
  ])
  assert.equal(steps.length, 3)
  assert.equal(steps[1], 'Let me look into this properly.')
  assert.ok(steps[0].endsWith('answer this question'), 'a chunk without a full stop is still its own step')
})

test('nothing is edited — a step is the chunk it came from', () => {
  const chunks = ['First I check the ledger.', ' Then I read the policy.']
  assert.deepEqual(reasoningSteps(chunks), ['First I check the ledger.', 'Then I read the policy.'])
})

test('a newline inside a chunk is a break its author put there', () => {
  assert.deepEqual(reasoningSteps(['Plan:\n\n- read the invoice\n- compare the terms\n']), [
    'Plan:',
    '- read the invoice',
    '- compare the terms',
  ])
})

test('a token-level stream re-forms into sentences instead of one bullet per word', () => {
  const steps = reasoningSteps(['Let ', 'me ', 'check ', 'the ', 'ledger. ', 'Then ', 'I ', 'answer.'])
  assert.deepEqual(steps, ['Let me check the ledger.', 'Then I answer.'])
})

test('no chunks produce no steps rather than one empty one', () => {
  assert.deepEqual(reasoningSteps([]), [])
  assert.deepEqual(reasoningSteps(['   ', '\n']), [])
})
