/**
 * The signature mark is a second channel for facts the wire named — and precedence is
 * the whole of it.
 *
 * A single run satisfies several of these at once: a fanned-out team run that stops at
 * the injection rail is `fanout` *and* `blocked`, and a mark that showed the wrong one
 * would be telling a room the run is working while the panel beside it says it was
 * refused. So the order is asserted directly rather than inferred from a rendered SVG.
 *
 * The second claim here is the one that is easy to get wrong quietly: an unrecognised
 * `layer` must leave the ring whole. Falling back to segment 0 would draw a break at
 * `schema` for a verdict that came from somewhere else entirely — a fact the mark would
 * be inventing, in the one place its geometry carries meaning.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  brokenSegmentOf,
  markStateOf,
  segmentsOf,
  SPIN_SECONDS,
} from '../../src/components/console/runMarkState.ts'

/** A RunState carrying only the fields the mark reads. */
function stateOf(overrides = {}) {
  return {
    phase: 'idle',
    running: false,
    events: [],
    guardrails: [],
    approval: null,
    routing: null,
    answer: '',
    ...overrides,
  }
}

let seq = 0
const at = (event) => ({ run_id: 'r1', seq: (seq += 1), ...event })

/** One open stage, which is what `screening` and `thinking` are told apart by. */
const open = (node) => [at({ type: 'node_started', node, label: node })]

const guardrail = (verdict, layer, stage = 'input') =>
  at({ type: 'guardrail', stage, verdict, reason: 'because', layer, redactions: [] })

const team = at({
  type: 'routing',
  role: 'research',
  reason: 'two corpora',
  used_llm: false,
  depth: 'team',
  fanout: 4,
  decided_by: 'auto',
})

test('each of the seven states is reached by the fact that names it', () => {
  assert.deepEqual(
    [
      markStateOf(null),
      markStateOf(stateOf()),
      markStateOf(stateOf({ running: true, events: open('guard_input') })),
      markStateOf(stateOf({ running: true, events: open('plan') })),
      markStateOf(stateOf({ running: true, events: [team], routing: team })),
      markStateOf(stateOf({ running: true, approval: at({ type: 'approval_required' }) })),
      markStateOf(stateOf({ guardrails: [guardrail('block', 'injection')] })),
      markStateOf(stateOf({ answer: 'the answer' })),
    ],
    ['idle', 'idle', 'screening', 'thinking', 'fanout', 'gated', 'blocked', 'settled'],
  )
})

test('a block wins over every other state the same run satisfies', () => {
  const everything = stateOf({
    running: true,
    events: [...open('guard_input'), team],
    routing: team,
    approval: at({ type: 'approval_required' }),
    guardrails: [guardrail('block', 'injection')],
    answer: 'the answer',
  })

  assert.equal(markStateOf(everything), 'blocked')
  // And the rest of the order, one demotion at a time.
  assert.equal(markStateOf({ ...everything, guardrails: [] }), 'gated')
  assert.equal(markStateOf({ ...everything, guardrails: [], approval: null }), 'fanout')
  assert.equal(
    markStateOf({ ...everything, guardrails: [], approval: null, routing: null }),
    'screening',
  )
})

test('the live states stop the moment the run does', () => {
  // `lastSignal` updates on `run_finished` like any other event, so a mark that read
  // `fanout` off `routing` alone would keep spinning after the run had ended.
  const finishedTeam = stateOf({ running: false, routing: team, answer: 'the answer' })

  assert.equal(markStateOf(finishedTeam), 'settled')
})

test('an unrecognised rail leaves the ring whole rather than breaking segment zero', () => {
  const unknown = stateOf({ guardrails: [guardrail('block', 'telepathy')] })
  const unnamed = stateOf({ guardrails: [guardrail('block', null)] })
  const named = stateOf({ guardrails: [guardrail('block', 'pii')] })
  const passed = stateOf({ guardrails: [guardrail('pass', 'pii')] })

  assert.deepEqual(
    [
      brokenSegmentOf(null),
      brokenSegmentOf(unknown),
      brokenSegmentOf(unnamed),
      brokenSegmentOf(named),
      brokenSegmentOf(passed),
    ],
    [null, null, null, 2, null],
    'only a block whose layer sits in its own chain may break the ring',
  )
})

test('the ring splits into the wire’s own lane count, and only on a fan-out', () => {
  const running = stateOf({ running: true, events: [team], routing: team })

  assert.equal(segmentsOf(running), 4, 'four arcs for the four lanes the router sized')
  assert.equal(segmentsOf(stateOf()), 6, 'six otherwise — the length of both rail chains')
  assert.equal(
    segmentsOf(stateOf({ running: true, events: open('plan') })),
    6,
    'a single-lane run never re-splits the ring',
  )
})


/**
 * The defect this guards is one that shipped.
 *
 * `screening` turned and every other live state was left to a per-event pulse, on the
 * assumption that events keep arriving while a run is open. Agentic retrieval emits
 * nothing between its open and its close, so a 60-second window drew one pulse and then
 * held perfectly still — a spinner-shaped hole in the mark that exists to fill it.
 *
 * A state that means "work is open" must therefore carry motion of its own, and this
 * fails if a future state is added to the live set without any.
 */
test('every state that means work is open carries motion of its own', () => {
  const open = ['screening', 'thinking', 'fanout']
  for (const state of open) {
    const seconds = SPIN_SECONDS[state]
    assert.equal(
      typeof seconds,
      'number',
      `${state} means a stage is open and would render motionless between wire events`,
    )
    assert.ok(seconds > 0 && seconds < 30, `${state} turns at an unreadable rate`)
  }
})

test('a state with nothing open does not turn', () => {
  // Motion here would be decoration: there is no open stage whose duration it could carry.
  for (const state of ['idle', 'settled', 'blocked', 'gated']) {
    assert.equal(SPIN_SECONDS[state], undefined, `${state} turns while nothing is running`)
  }
})
