/**
 * The claim the memory rail's digest makes: it is bounded, it is ranked by a figure the
 * store measured, and it never presents a superseded belief as something the agent holds.
 *
 * "Full memory being seen" was the owner's verdict on the console — a rail that rendered
 * every fact expanded and grew the idle page past several viewports. The bound is
 * therefore code, and the code is tested, exactly as the three-card cap is.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  DIGEST_SIZE,
  digestFacts,
  rankFacts,
  recallLine,
} from '../../src/components/console/factDigest.ts'

/** A fact row with only the fields the digest reads; the rest never enters this decision. */
function fact({ id, valid = true, access = 0, importance = 0, confidence = 1 }) {
  return {
    id,
    subject_id: 'user:5',
    fact_type: 'preference',
    subject: 'user',
    predicate: 'knows',
    object: `o${id}`,
    text: `fact ${id}`,
    confidence,
    importance,
    access_count: access,
    valid_at: null,
    invalid_at: valid ? null : '2026-01-01',
    created_at: null,
    expired_at: null,
    source_turn_ids: [],
    supersedes_id: null,
    is_valid: valid,
  }
}

test('the digest is capped however many facts the store holds', () => {
  const rows = Array.from({ length: 40 }, (_, i) => fact({ id: i + 1, access: i }))
  const digest = digestFacts(rows)
  assert.equal(digest.top.length, DIGEST_SIZE)
  assert.equal(digest.current, 40)
  assert.equal(digest.hidden, 40 - DIGEST_SIZE, 'the rest is counted, not dropped')
})

test('the few it shows are the ones the agent actually recalled most', () => {
  const rows = [
    fact({ id: 1, access: 0 }),
    fact({ id: 2, access: 9 }),
    fact({ id: 3, access: 4 }),
    fact({ id: 4, access: 0 }),
  ]
  assert.deepEqual(
    digestFacts(rows).top.map((row) => row.id),
    [2, 3, 4],
    'recall count first, then the newest of the ties',
  )
})

test('a superseded belief is counted as history and never shown as current', () => {
  const rows = [fact({ id: 1, access: 3 }), fact({ id: 2, valid: false, access: 99 })]
  const digest = digestFacts(rows)
  assert.equal(digest.current, 1)
  assert.equal(digest.superseded, 1)
  assert.deepEqual(digest.top.map((row) => row.id), [1])
})

test('an empty store owes no recall line, and a never-recalled one says so rather than estimating', () => {
  assert.equal(recallLine(digestFacts([])), null)
  assert.match(recallLine(digestFacts([fact({ id: 1 })])), /none recalled/)
  assert.match(recallLine(digestFacts([fact({ id: 1, access: 7 })])), /^7 recalls/)
})

test('ranking is total, so two renders of the same rows agree', () => {
  const rows = [fact({ id: 3 }), fact({ id: 1 }), fact({ id: 2 })]
  assert.deepEqual(rankFacts(rows).map((r) => r.id), rankFacts(rows.slice().reverse()).map((r) => r.id))
})
