/**
 * The Answer tab must not accuse a person of a decision they never made.
 *
 * `finishedStatus === 'blocked'` is the terminal status for a guardrail block, a budget
 * refusal *and* a rejected human gate, and the tab keyed one sentence on it: "Rejected
 * at the human gate — no answer generated." An audit watched it say that about a run
 * the input rail stopped because the injection classifier was unreachable — while the
 * true reason was on the event, verbatim, and rendering correctly one tab over.
 *
 * Answer is the default tab, so this is the sentence most people read and the only one
 * many will. These cover the four ways a run ends with nothing to show, and the one
 * where it is still coming.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { answerAbsence } from '../../src/components/console/answerAbsence.ts'

let seq = 0
const at = (event) => ({ run_id: 'r1', seq: (seq += 1), ...event })

const guardrail = (overrides) =>
  at({
    type: 'guardrail',
    stage: 'input',
    verdict: 'block',
    reason: '',
    layer: null,
    redactions: [],
    before_masked: null,
    after: null,
    ...overrides,
  })

/** A RunState carrying only what this derivation reads. */
function runOf(events, overrides = {}) {
  return {
    phase: 'completed',
    running: false,
    answer: '',
    error: null,
    finishedStatus: null,
    awaitedApproval: false,
    events,
    ...overrides,
  }
}

test('a rail block names the rail and quotes the run, not the human gate', () => {
  // Audit B's run, exactly: the input rail stopped it because the classifier was down.
  const absence = answerAbsence(
    runOf(
      [
        guardrail({
          layer: 'injection',
          reason: 'The injection classifier was unreachable, so the question was refused.',
        }),
      ],
      { phase: 'blocked', finishedStatus: 'blocked' },
    ),
  )

  assert.equal(absence.stopped, true)
  assert.doesNotMatch(
    absence.headline,
    /human gate|rejected/i,
    'nobody rejected this run — the input rail stopped it',
  )
  assert.match(absence.headline, /input rail/)
  assert.match(absence.headline, /injection/, 'the layer that decided is worth naming')
  assert.equal(
    absence.detail,
    'The injection classifier was unreachable, so the question was refused.',
    "the run's own reason, verbatim — the same string Trace shows",
  )
})

test('a budget refusal is a budget refusal, even on a run that paused for a person', () => {
  const absence = answerAbsence(
    runOf(
      [
        at({ type: 'approval_required', approval_id: 'a1', action: 'refund', args: {}, risk: 'high', rationale: '', actions: [] }),
        at({
          type: 'budget_exceeded',
          scope: 'tenant',
          scope_id: 1,
          limit_type: 'usd',
          limit: 5,
          used: 5.4,
          message: 'This tenant is over its daily spend cap of $5.00.',
        }),
      ],
      { phase: 'blocked', finishedStatus: 'blocked', awaitedApproval: true },
    ),
  )

  assert.match(absence.headline, /spend cap/)
  assert.doesNotMatch(absence.headline, /human gate/i)
  assert.equal(absence.detail, 'This tenant is over its daily spend cap of $5.00.')
})

test('a run that only ever paused for a person still reads as the human gate', () => {
  const absence = answerAbsence(
    runOf(
      [
        at({ type: 'approval_required', approval_id: 'a1', action: 'refund', args: {}, risk: 'high', rationale: '', actions: [] }),
        guardrail({ verdict: 'pass', reason: 'nothing found' }),
      ],
      { phase: 'blocked', finishedStatus: 'blocked', awaitedApproval: true },
    ),
  )

  assert.match(absence.headline, /human gate/)
  assert.equal(absence.stopped, true)
})

test('a backend that went away stops promising a stream that will never come', () => {
  const absence = answerAbsence(
    runOf([at({ type: 'run_started', trace_id: 't1' })], {
      phase: 'error',
      error: 'The backend stopped answering. Check it is still running, then send the question again.',
    }),
  )

  assert.equal(absence.stopped, true)
  assert.doesNotMatch(
    absence.headline,
    /streams here/,
    'a dead run must not advertise an answer that cannot arrive',
  )
  assert.match(absence.detail, /stopped answering/)
})

test('a run still in flight promises the stream, and says nothing else', () => {
  const absence = answerAbsence(
    runOf([at({ type: 'run_started', trace_id: 't1' })], {
      phase: 'streaming',
      running: true,
    }),
  )

  assert.equal(absence.stopped, false)
  assert.match(absence.headline, /streams here/)
  assert.equal(absence.detail, '')
})
