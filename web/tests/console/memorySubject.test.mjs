/**
 * Who the memory rail reads for, and when it must read for nobody.
 *
 * `/memory/*` is keyed on a subject string and a non-admin may read only its own, so this
 * one function decides between "a person sees their own memory" and "a person sees none".
 * Both failures are silent: a wrong subject is a 403 the rail renders as an empty panel,
 * and a subject invented for a principal that has no `users` row would be a request for
 * somebody else's record.
 *
 * One test, because there is one rule with one shape: a real positive integer id, or null.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { memorySubjectOf } from '../../src/components/console/memorySubject.ts'

test('memory is scoped to a real user id, and to nothing at all without one', () => {
  assert.equal(memorySubjectOf({ userId: 7 }), 'user:7', 'the backend keys on `user:<id>`')
  assert.equal(memorySubjectOf({ userId: 1 }), 'user:1')

  // Signed out, and the back-compat demo principals that have no `users` row behind them.
  assert.equal(memorySubjectOf(null), null)
  assert.equal(memorySubjectOf({ userId: null }), null)

  // A session stored before `userId` existed rehydrates with junk in the field; none of
  // these is an id, and each would otherwise become a subject string that reads as one.
  assert.equal(memorySubjectOf({ userId: 0 }), null, 'zero is not a user')
  assert.equal(memorySubjectOf({ userId: -3 }), null)
  assert.equal(memorySubjectOf({ userId: 2.5 }), null, 'a fraction is not a row id')
  assert.equal(memorySubjectOf({ userId: Number.NaN }), null)
})
