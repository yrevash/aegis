/**
 * "Grounded ✓" must not appear on a run that retrieved nothing.
 *
 * The chip lit whenever a provenance record existed — and one exists on every run,
 * including a run that retrieved zero passages. So the trust strip said `Grounded ✓`
 * while the Sources panel on the same turn said "This run retrieved nothing, so the
 * answer is not grounded in a document". Two surfaces, one run, opposite claims.
 *
 * These are the three states that must stay apart: no answer, an answer with nothing
 * behind it, and an answer the grounding rail actually cleared.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { isGrounded } from '../../src/components/console/trustChecks.ts'
import { initialRunState } from '../../src/state/runReducer.ts'

/** A provenance record — emitted on every run, grounded or not. */
const PROVENANCE = { type: 'provenance', run_id: 'r1', seq: 9, origins: [], cache_hit: false }

const run = (over) => ({ ...initialRunState, ...over })

const rail = (verdict, layer) => ({
  type: 'guardrail',
  run_id: 'r1',
  seq: 5,
  stage: 'output',
  verdict,
  layer,
  reason: '',
  redactions: [],
  before_masked: null,
  after: null,
})

test('an answer with zero retrieval is not grounded, provenance or no provenance', () => {
  const state = run({ answer: 'The supplier is REVO.', provenance: PROVENANCE })
  assert.equal(isGrounded(state), false)
})

test('the grounding rail is the authority when it ran', () => {
  const answered = { answer: 'The supplier is REVO.', provenance: PROVENANCE }

  // A FLAG is what the backend now reports for an answer with no passages behind it.
  assert.equal(isGrounded(run({ ...answered, guardrails: [rail('flag', 'grounding')] })), false)

  // And it outranks retrieval having returned something: the rail read the passages and
  // judged the answer unsupported by them.
  assert.equal(
    isGrounded(
      run({ ...answered, retrievalScores: [{ id: 's1', score: 0.9 }], guardrails: [rail('flag', 'grounding')] }),
    ),
    false,
  )

  assert.equal(isGrounded(run({ ...answered, guardrails: [rail('pass', 'grounding')] })), true)
})

test('with no grounding rail, retrieval having returned something is the fallback', () => {
  const answered = { answer: 'The supplier is REVO.', provenance: PROVENANCE }
  assert.equal(isGrounded(run({ ...answered, retrievalScores: [{ id: 's1', score: 0.9 }] })), true)

  // A rail from some other layer says nothing about grounding either way.
  assert.equal(
    isGrounded(run({ ...answered, guardrails: [rail('pass', 'pii')] })),
    false,
    'a PII rail passing is not a grounding claim',
  )
})

test('a run with no answer is never grounded — there is nothing to ground', () => {
  const blocked = run({
    answer: '',
    provenance: PROVENANCE,
    retrievalScores: [{ id: 's1', score: 0.9 }],
    guardrails: [rail('pass', 'grounding')],
  })
  assert.equal(isGrounded(blocked), false)
})
