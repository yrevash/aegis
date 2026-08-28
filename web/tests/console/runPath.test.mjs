/**
 * The four claims the run scaffold makes, and the way each one goes wrong.
 *
 * The strip is on screen for the whole run now, so every cell of it is a claim about
 * work the platform did. These cover the ones that would be *wrong on screen*: that a
 * beat states the routed width only once the router has named one, that a blocked rail
 * ends the path rather than letting the rest quietly read as done, that a fan-out lane is
 * never added to the `run_team` whose duration already contains it, and that the deciding
 * rail is the one the wire named — with no mark at all when the wire named one this chain
 * does not contain.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { beatStates } from '../../src/components/console/runPath.ts'

/** A RunState carrying only the fields this derivation reads. */
function stateOf(events, overrides = {}) {
  return {
    phase: 'completed',
    running: false,
    events,
    steps: events.filter((e) => e.type === 'node_started'),
    nodeLedger: events.filter((e) => e.type === 'node_finished'),
    guardrails: events.filter((e) => e.type === 'guardrail'),
    routing: events.find((e) => e.type === 'routing') ?? null,
    ...overrides,
  }
}

let seq = 0
const at = (event) => ({ run_id: 'r1', seq: (seq += 1), ...event })

/** A `node_finished` with the fields the timing reduction reads. */
const finished = (node, durationMs) =>
  at({
    type: 'node_finished',
    node,
    label: node,
    duration_ms: durationMs,
    model: 'gpt',
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
  })

/** What the strip actually paints, per beat. */
const shape = (beats) => beats.map((b) => [b.id, b.status, b.durationMs, b.caption])

test('an empty console gets four pending beats and not one measured figure', () => {
  assert.deepEqual(shape(beatStates(null)), [
    ['input', 'pending', null, ''],
    ['route', 'pending', null, ''],
    ['work', 'pending', null, ''],
    ['output', 'pending', null, ''],
  ])
})

test('a beat runs while its stage is open and settles on the wire’s own duration', () => {
  const state = stateOf(
    [
      at({ type: 'node_started', node: 'guard_input', label: 'Guard input' }),
      finished('guard_input', 3142),
      at({ type: 'node_started', node: 'route', label: 'Route' }),
    ],
    { running: true },
  )

  assert.deepEqual(shape(beatStates(state)), [
    ['input', 'passed', 3142, ''],
    ['route', 'running', null, ''],
    ['work', 'pending', null, ''],
    ['output', 'pending', null, ''],
  ])
})

test('the routed width appears on the middle beat only after the routing event', () => {
  const before = [
    at({ type: 'node_started', node: 'guard_input', label: 'Guard input' }),
    finished('guard_input', 3142),
    at({ type: 'node_started', node: 'route', label: 'Route' }),
    finished('route', 10),
  ]

  assert.equal(
    beatStates(stateOf(before, { running: true }))[2].caption,
    '',
    'the router has not sized the turn yet, so the strip must claim no width',
  )

  const after = [
    ...before,
    at({
      type: 'routing',
      role: 'research',
      reason: 'the question spans two corpora',
      used_llm: false,
      depth: 'team',
      fanout: 4,
      decided_by: 'auto',
    }),
    at({ type: 'node_started', node: 'run_team', label: 'Run team' }),
  ]

  assert.deepEqual(shape(beatStates(stateOf(after, { running: true }))), [
    ['input', 'passed', 3142, ''],
    ['route', 'passed', 10, 'research'],
    ['work', 'running', null, 'Team of 4'],
    ['output', 'pending', null, ''],
  ])
})

test('a blocked input rail ends the path, and nothing after it ever reads as passed', () => {
  const state = stateOf([
    at({ type: 'node_started', node: 'guard_input', label: 'Guard input' }),
    at({
      type: 'guardrail',
      stage: 'input',
      verdict: 'block',
      reason: 'an instruction addressed to the model was planted in the question',
      layer: 'injection',
      redactions: [],
    }),
    finished('guard_input', 6),
  ])

  const beats = beatStates(state)

  assert.deepEqual(shape(beats), [
    ['input', 'blocked', 6, 'prompt injection'],
    ['route', 'pending', null, ''],
    ['work', 'pending', null, ''],
    ['output', 'pending', null, ''],
  ])
  assert.equal(beats[0].railIndex, 3, 'the wire named the injection rail, and it is fourth')
  assert.equal(beats[0].verdict, 'block', 'the raw wire verdict, never collapsed to a boolean')
})

test('a fan-out lane is not added to the run_team whose duration already covers it', () => {
  const state = stateOf([
    at({ type: 'node_started', node: 'run_team', label: 'Run team' }),
    at({ type: 'node_started', node: 'agent:research', label: 'Research' }),
    finished('agent:research', 3396),
    finished('run_team', 12254),
  ])

  assert.equal(
    beatStates(state)[2].durationMs,
    12254,
    'adding the lane would claim a 15.6 s middle for a 12.2 s one',
  )
})

test('a rail this chain does not contain is marked nowhere rather than at the wrong rail', () => {
  const exfiltration = stateOf([
    at({ type: 'node_started', node: 'guard_output', label: 'Guard output' }),
    at({
      type: 'guardrail',
      stage: 'output',
      verdict: 'redact',
      reason: 'a key-shaped string was masked',
      layer: 'exfiltration',
      redactions: [],
    }),
    finished('guard_output', 7789),
  ])

  const beat = beatStates(exfiltration)[3]
  assert.equal(beat.railIndex, null, 'check_output’s six-layer chain has no exfiltration cell')
  assert.equal(beat.caption, '', 'and nothing is named, rather than the wrong rail being named')
  assert.equal(beat.verdict, 'redact', 'the verdict is still reported — only its position is not')

  const grounding = stateOf([
    at({ type: 'node_started', node: 'guard_output', label: 'Guard output' }),
    at({
      type: 'guardrail',
      stage: 'output',
      verdict: 'flag',
      reason: 'one claim had no source behind it',
      layer: 'grounding',
      redactions: [],
    }),
    finished('guard_output', 7789),
  ])

  assert.deepEqual(
    [beatStates(grounding)[3].railIndex, beatStates(grounding)[3].caption],
    [4, 'grounding'],
    'a rail the chain does contain is named, in its own position',
  )
})
