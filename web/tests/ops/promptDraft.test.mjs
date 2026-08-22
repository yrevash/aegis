/**
 * The two rules that make the prompt editor safe to touch.
 *
 * The defect they exist for: `GET /llmops/prompts` answers `activePrompt: null` for a
 * scope with no promoted version, the editor folded that into `''`, and the box that
 * says "Task prompt" rendered blank while a prompt was live. What an operator then typed
 * did not *edit* the prompt — it became the whole of it. A tenant's operating prompt was
 * replaced by one sentence that way.
 *
 * Not one test per branch. Two claims, and the failure mode of each: the seed must reach
 * an untouched box and never reach a typed-in one, and a body with nothing in it must be
 * refused rather than confirmed.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { draftRefusal, mayReseed } from '../../src/components/ops/promptDraft.ts'

test('the live prompt reaches an untouched box, and moves with it', () => {
  // First load: nothing has been seeded and nothing typed.
  assert.equal(mayReseed('', null), true)
  // An activate or a rollback changed the live text under a box nobody has touched:
  // it still holds exactly the last seed, so the new live text may replace it.
  assert.equal(mayReseed('the prompt that was live', 'the prompt that was live'), true)
})

test('a reload never overwrites what the operator typed', () => {
  // The failure mode: a background reload landing mid-edit and discarding the draft.
  assert.equal(mayReseed('half a rewritten prompt', 'the prompt that was live'), false)
  // Typing into a box that was seeded from nothing (the shipped-prompt scope) is still
  // typing — this is exactly the scope where the accident happened.
  assert.equal(mayReseed('AUDIT MARKER: always cite the request id.', null), false)
})

test('a body with nothing in it is refused, and says why', () => {
  // The server's own rule is `min_length=1`, so a single space is a valid version as far
  // as the API is concerned. Promoting it would leave the tenant on the floor alone.
  for (const blank of ['', ' ', '\n\n', '\t  \n']) {
    const refusal = draftRefusal(blank)
    assert.notEqual(refusal, null, `expected a refusal for ${JSON.stringify(blank)}`)
    assert.match(refusal, /needs a body/)
  }
  assert.equal(draftRefusal('You are the Operations Lead.'), null)
})
