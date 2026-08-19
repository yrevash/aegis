/**
 * The audit filter is a **server** query, and an empty box is not a filter.
 *
 * §7.11. The console used to fetch a page and narrow it in the browser, so every control
 * answered a question about the page rather than about the trail. `auditQueryString` is
 * the seam that moved: what the operator typed becomes `GET /audit` parameters, and the
 * database does the narrowing.
 *
 * Two things would be silently wrong and are pinned here:
 *
 * - **An untouched control must emit nothing.** `actor=''` sent as a parameter asks the
 *   server for rows whose actor is the empty string, which matches nothing — a blank
 *   form would return an empty trail and look like "no events ever happened".
 * - **A typed time is wall-clock, the API is UTC.** `datetime-local` has no zone;
 *   shipping it verbatim shifts the window by the reader's offset, so "since 09:00" asks
 *   for a different morning depending on where the operator is standing.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  auditQueryString,
  emptyStateFor,
  EMPTY_AUDIT_QUERY,
  isFiltered,
  localToIso,
} from '../../src/components/audit/query.ts'

test('an untouched form asks for the whole trail, not for blank fields', () => {
  const search = auditQueryString(EMPTY_AUDIT_QUERY)
  const params = new URLSearchParams(search)

  assert.deepEqual([...params.keys()], ['limit'])
  assert.equal(params.get('limit'), '50')
  assert.equal(isFiltered(EMPTY_AUDIT_QUERY), false)
})

test('whitespace is not a filter either', () => {
  const search = auditQueryString({
    ...EMPTY_AUDIT_QUERY,
    actor: '   ',
    actionPrefix: '\t',
    text: ' ',
  })

  assert.deepEqual([...new URLSearchParams(search).keys()], ['limit'])
})

test('every control becomes the parameter the route reads', () => {
  const params = new URLSearchParams(
    auditQueryString({
      ...EMPTY_AUDIT_QUERY,
      actor: ' alice ',
      actionPrefix: 'ops.',
      model: 'gpt-4o-mini',
      outcome: 'blocked',
      text: 'transfer',
      tenantId: 7,
      limit: 200,
    }),
  )

  assert.equal(params.get('actor'), 'alice', 'the value is trimmed, not the meaning')
  assert.equal(params.get('action_prefix'), 'ops.')
  assert.equal(params.get('model'), 'gpt-4o-mini')
  assert.equal(params.get('outcome'), 'blocked')
  assert.equal(params.get('q'), 'transfer')
  assert.equal(params.get('tenant_id'), '7')
  assert.equal(params.get('limit'), '200')
})

test('a typed local time is sent as the UTC instant it names', () => {
  const local = '2026-08-19T09:00'
  const iso = localToIso(local)

  assert.equal(iso, new Date(local).toISOString())
  assert.equal(new Date(iso).getTime(), new Date(local).getTime())

  const params = new URLSearchParams(auditQueryString({ ...EMPTY_AUDIT_QUERY, since: local }))
  assert.equal(params.get('since'), iso)
})

test('an unparseable date filters nothing rather than filtering everything out', () => {
  assert.equal(localToIso('not-a-date'), null)
  assert.deepEqual(
    [...new URLSearchParams(auditQueryString({ ...EMPTY_AUDIT_QUERY, since: 'not-a-date' })).keys()],
    ['limit'],
    'a bound the browser could not parse must not be sent as one',
  )
})

test('an empty result set instructs when filtered and states the truth when not', () => {
  const unfiltered = emptyStateFor(EMPTY_AUDIT_QUERY)
  assert.match(unfiltered.title, /Nothing audited yet/)

  const filtered = emptyStateFor({ ...EMPTY_AUDIT_QUERY, actor: 'alice' })
  assert.match(filtered.title, /No events match those filters/)
  assert.match(filtered.hint, /Widen|clear/i, 'an empty state must say what to do next')
})
