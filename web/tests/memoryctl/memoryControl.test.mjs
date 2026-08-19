/**
 * What the memory control plane says before it destroys something.
 *
 * These are the sentences attached to the only irreversible buttons a client-facing
 * screen in this product has, so the thing worth testing is not that they render — it is
 * that they are **specific**. "Delete everything?" is not consent. A confirmation that
 * names the counts is, and a confirmation that names *zeroes* it never measured is worse
 * than either, because it reads as a measurement.
 *
 * One test per decision the copy makes, and no test of the wording itself: the phrasing
 * will change, the promises must not.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  erasureReceipt,
  erasureWarning,
  retentionClause,
  screeningNote,
  sourceLabel,
  subjectSummary,
} from '../../src/components/memoryctl/memoryControl.ts'

test('the retention clause names only the tiers that actually have rows past the horizon', () => {
  assert.equal(
    retentionClause({ messages: 12, sessions: 2, facts: 0, jobs: 0 }),
    '12 turns and 2 sessions',
    'a tier with nothing in it is absent, not listed as zero',
  )
  assert.equal(retentionClause({ messages: 1, sessions: 0, facts: 0, jobs: 0 }), '1 turn')
  assert.equal(
    retentionClause({ messages: 3, sessions: 1, facts: 4, jobs: 2 }),
    '3 turns, 1 session, 4 superseded facts and 2 queue rows',
  )
})

test('nothing past the horizon is a different sentence, not a zero', () => {
  // The caller has to be able to write "nothing has aged out yet" rather than
  // "0 turns are past the horizon", which reads as a broken counter.
  assert.equal(retentionClause({ messages: 0, sessions: 0, facts: 0, jobs: 0 }), null)
  // And "we have not looked" is not "we looked and found none".
  assert.equal(retentionClause(null), null)
})

test('the erasure warning names what goes, and never fabricates a count', () => {
  const warning = erasureWarning({
    subject: 'user:11',
    label: 'a-user',
    is_self: true,
    tenant_id: 1,
    fact_count: 3,
    session_count: 2,
    last_active: null,
  })
  assert.match(warning, /3 facts and 2 sessions/)
  assert.match(warning, /a-user/)
  assert.match(warning, /cannot be undone/)
  // A subject the screen has no counts for must not claim "0 facts" — it says what it
  // will remove in general terms instead.
  const unknown = erasureWarning({
    subject: 'user:11',
    label: 'a-user',
    is_self: true,
    tenant_id: 1,
    fact_count: 0,
    session_count: 0,
    last_active: null,
  })
  assert.match(unknown, /every stored row/)
  assert.doesNotMatch(unknown, /0 facts/)
})

test('the receipt reports every tier, including the ones that removed nothing', () => {
  // The opposite rule from the warning, and deliberately so: before the fact a zero is
  // noise, after the fact it is evidence that the tier was checked and was empty.
  const receipt = erasureReceipt({
    subject: 'user:11',
    deleted_facts: 3,
    deleted_messages: 40,
    deleted_sessions: 2,
    deleted_profiles: 0,
    deleted_writes: 7,
    deleted_jobs: 0,
  })
  assert.match(receipt, /3 facts/)
  assert.match(receipt, /40 turns/)
  assert.match(receipt, /0 profiles/)
})

test('a redaction is announced, and a clean write says nothing', () => {
  const note = screeningNote('redact', ['EMAIL_ADDRESS'])
  assert.match(note, /redacted/)
  assert.match(note, /EMAIL_ADDRESS/)
  // A redaction that happened silently is one the operator will later mistake for the
  // agent forgetting something.
  assert.equal(screeningNote('pass', []), null)
  assert.match(screeningNote('flag', []), /stored/)
})

test('a displayed value says where it came from', () => {
  assert.equal(sourceLabel('platform'), 'Aegis default')
  assert.equal(sourceLabel('tenant'), "your tenant's")
  assert.equal(sourceLabel('user'), 'your choice')
})

test('a subject with nothing stored says so rather than reading as broken', () => {
  const empty = {
    subject: 'user:11',
    label: 'a-user',
    is_self: true,
    tenant_id: 1,
    fact_count: 0,
    session_count: 0,
    last_active: null,
  }
  assert.equal(subjectSummary(empty), 'Nothing stored yet')
  assert.equal(subjectSummary({ ...empty, fact_count: 1 }), '1 fact · 0 sessions')
})
