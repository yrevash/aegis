/**
 * The recall trace must not answer a question nobody asked.
 *
 * `RecallDebugPanel`'s docstring says it refuses to seed a query — *"a placeholder
 * question would put words in the operator's mouth and trace a recall nobody asked
 * for"* — and three lines below it the submit handler read
 * `setQuery(draft.trim() || 'request status')`. Submitting an empty box traced "request
 * status", and the ranked rows underneath were the agent's honest answer to words the
 * person had never typed, presented as if they had.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { recallQuery } from '../../src/components/memory/memoryText.ts'

test('an empty recall box traces nothing, rather than a question nobody typed', () => {
  assert.equal(recallQuery(''), null)
  assert.equal(recallQuery('   \n\t '), null, 'whitespace is an empty box')

  assert.equal(recallQuery('  what did I say about refunds?  '), 'what did I say about refunds?')
})
