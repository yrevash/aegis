/**
 * What `?document=` opens on the Jobs screen — and, the part that matters, what it says
 * when it cannot open the thing it was sent to.
 *
 * A deep link that lands on a list not containing its own target is the original defect
 * with a query string on it. The two ways that happens are the two tests with the most
 * to say here: the row exists but the active filter hides it, and there is no row at
 * all. Neither may end in an unexplained list.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { focusNote, resolveJobFocus } from '../../src/components/jobs/jobsFocus.ts'

/** One `job_runs` row, shaped as `GET /jobs` sends one. */
function job(overrides = {}) {
  return {
    id: 7,
    job_type: 'ingest',
    status: 'succeeded',
    completed_stage: 'index',
    workflow_id: 'wf-7',
    document_id: 25,
    cost_usd: 0,
    error: null,
    cancelled_by: null,
    created_at: '2026-08-23T10:00:00Z',
    started_at: null,
    finished_at: null,
    ...overrides,
  }
}

const everything = () => true

test('no parameter opens nothing and says nothing', () => {
  const focus = resolveJobFocus([job()], null, everything)
  assert.equal(focus.kind, 'none')
  assert.equal(focusNote(focus, false), null)
})

test('the run for that document is what opens — not the list', () => {
  const rows = [job({ id: 9, document_id: 40 }), job({ id: 7, document_id: 25 })]
  const focus = resolveJobFocus(rows, 25, everything)
  assert.equal(focus.kind, 'row')
  assert.equal(focus.row.id, 7)
  assert.equal(focus.hiddenByFilter, false)
  assert.match(focusNote(focus, false), /job #7/)
})

test('a re-queued document opens its latest run, not its first', () => {
  // `GET /jobs` is newest-first, and a re-queue is a second `job_runs` row for the same
  // document. The alert that sent the reader here is about the latest one.
  const rows = [job({ id: 12, document_id: 25 }), job({ id: 7, document_id: 25 })]
  assert.equal(resolveJobFocus(rows, 25, everything).row.id, 12)
})

test('a row the active filter hides is reported as hidden, not as absent', () => {
  // The failure mode: "Failed" is selected, the alert is about a succeeded ingest, and
  // the screen shows a list the target is not in. `hiddenByFilter` is what makes the
  // screen widen and *say* it widened.
  const rows = [job({ id: 7, document_id: 25, status: 'succeeded' })]
  const focus = resolveJobFocus(rows, 25, (row) => row.status === 'failed')
  assert.equal(focus.kind, 'row')
  assert.equal(focus.hiddenByFilter, true)
  assert.match(focusNote(focus, true), /Showing all jobs/)
})

test('a document with no run here falls back to the document, and says why', () => {
  const focus = resolveJobFocus([job({ document_id: 40 })], 900, everything)
  assert.equal(focus.kind, 'document')
  assert.equal(focus.documentId, 900)
  const note = focusNote(focus, false)
  assert.match(note, /900/)
  assert.match(note, /No run in this queue/)
  // It must not claim the document is gone — the screen cannot know that yet, and the
  // ingest record it opens is what actually answers the question.
  assert.doesNotMatch(note, /deleted|does not exist/i)
})

test('an empty queue still resolves to the document rather than to silence', () => {
  const focus = resolveJobFocus([], 25, everything)
  assert.equal(focus.kind, 'document')
  assert.ok(focusNote(focus, false))
})
