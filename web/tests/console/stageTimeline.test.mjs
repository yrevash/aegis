/**
 * The stage spine must not invent the run's shape, and must not double-count it.
 *
 * The measured run this panel was built for spends 12.2 s in `run_team` and 12.0 s of
 * that inside two concurrent lanes. Summing every `duration_ms` in arrival order would
 * report a 41-second run that took 29, which is exactly the kind of figure that loses
 * an audience the moment somebody checks it. So the lane/graph split is asserted here,
 * along with the two other things a reader trusts this panel for: a stage in flight
 * carries no duration at all, and a finished guardrail quotes the rail's own words.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { deriveTiming, formatDuration } from '../../src/components/console/stageTimeline.ts'

let seq = 0
const at = (event) => ({ run_id: 'r1', seq: (seq += 1), ...event })

const started = (node, label, agent_id = null) =>
  at({ type: 'node_started', node, label, agent_id })

const finished = (node, label, duration_ms, extra = {}) =>
  at({
    type: 'node_finished',
    node,
    label,
    duration_ms,
    model: null,
    prompt_tokens: 0,
    completion_tokens: 0,
    cost_usd: 0,
    ...extra,
  })

const guardrail = (stage, verdict, reason) =>
  at({
    type: 'guardrail',
    stage,
    verdict,
    reason,
    layer: null,
    redactions: [],
    before_masked: null,
    after: null,
  })

/** A RunState carrying only what this derivation reads. */
const runOf = (events) => ({ events, running: false })

/** The real run, as the console measured it. */
function measuredRun() {
  seq = 0
  return runOf([
    started('guard_input', 'Input guardrail'),
    guardrail('input', 'pass', 'Input passed schema, denylist, PII and injection rails.'),
    finished('guard_input', 'Input guardrail', 3142),
    started('route', 'Route intent'),
    finished('route', 'Route intent', 10),
    started('plan_team', 'Plan the team'),
    finished('plan_team', 'Plan the team', 1444, { cost_usd: 0.0004, prompt_tokens: 300, completion_tokens: 40 }),
    started('run_team', 'Run agents concurrently'),
    started('agent:research', 'Research', 'research'),
    started('agent:knowledge', 'Knowledge', 'knowledge'),
    finished('agent:research', 'Research', 3396),
    finished('agent:knowledge', 'Knowledge', 8605),
    finished('run_team', 'Run agents concurrently', 12254),
    started('synthesize', 'Synthesise findings'),
    finished('synthesize', 'Synthesise findings', 3795),
    started('guard_output', 'Output guardrail'),
    guardrail('output', 'pass', 'Output passed schema, content-filter, content-safety, and PII rails.'),
    finished('guard_output', 'Output guardrail', 7789),
    started('stream', 'Stream answer'),
    finished('stream', 'Stream answer', 0),
  ])
}

test('a fan-out lane is never added to the run_team that contains it', () => {
  const timing = deriveTiming(measuredRun())
  // 3142 + 10 + 1444 + 12254 + 3795 + 7789 + 0 — the six top-level stages and the
  // stream, with the two lanes excluded because run_team already covers them.
  assert.equal(timing.measuredMs, 28434)
  const lanes = timing.stages.filter((s) => s.agentId !== null).map((s) => s.agentId)
  assert.deepEqual(lanes, ['research', 'knowledge'])
})

test('every duration is the wire’s, and the peak scales the bars', () => {
  const timing = deriveTiming(measuredRun())
  const guardIn = timing.stages.find((s) => s.node === 'guard_input')
  assert.equal(guardIn.durationMs, 3142)
  assert.equal(timing.peakMs, 12254)
  assert.equal(timing.measured, true)
})

test('a stage still in flight reports no duration and no cost', () => {
  seq = 0
  const timing = deriveTiming(
    runOf([started('guard_input', 'Input guardrail'), started('route', 'Route intent')]),
  )
  assert.equal(timing.stages.length, 2)
  assert.equal(timing.current.node, 'route')
  for (const stage of timing.stages) {
    assert.equal(stage.running, true)
    assert.equal(stage.durationMs, null)
    assert.equal(stage.costUsd, null)
  }
  assert.equal(timing.measured, false)
  assert.equal(timing.measuredMs, 0)
})

test('a finished guardrail carries the rail’s own words', () => {
  const timing = deriveTiming(measuredRun())
  const guardOut = timing.stages.find((s) => s.node === 'guard_output')
  assert.match(guardOut.verdict, /^Output passed schema/)
  assert.equal(guardOut.blocked, false)
  assert.equal(guardOut.chain.length, 6)
})

test('a blocked input rail marks the stage that blocked it', () => {
  seq = 0
  const timing = deriveTiming(
    runOf([
      started('guard_input', 'Input guardrail'),
      guardrail('input', 'block', 'Prompt injection detected.'),
      finished('guard_input', 'Input guardrail', 3142),
    ]),
  )
  const [stage] = timing.stages
  assert.equal(stage.blocked, true)
  assert.equal(stage.verdict, 'Prompt injection detected.')
})

test('a node the frontend has never heard of still renders under its served label', () => {
  seq = 0
  const timing = deriveTiming(runOf([started('quantum_step', 'Quantum step')]))
  assert.equal(timing.stages[0].label, 'Quantum step')
  assert.equal(timing.stages[0].what, '')
  assert.equal(timing.stages[0].signal, 'neutral')
})

test('durations read as a person reads them', () => {
  assert.equal(formatDuration(10), '10 ms')
  assert.equal(formatDuration(999), '999 ms')
  assert.equal(formatDuration(3142), '3.1 s')
})

const reflection = (iteration, will_retry, reason) =>
  at({ type: 'reflection', iteration, max_iterations: 3, done: !will_retry, will_retry, reason })

test('the self-repair loop is grouped into the rounds the wire numbers', () => {
  seq = 0
  const timing = deriveTiming(
    runOf([
      started('retrieve', 'Agentic retrieval'),
      finished('retrieve', 'Agentic retrieval', 30500),
      started('plan', 'Reason & plan'),
      finished('plan', 'Reason & plan', 1500),
      started('act', 'Execute actions'),
      finished('act', 'Execute actions', 3600),
      started('reflect', 'Reflect & self-repair'),
      reflection(1, true, 'The tool returned nothing usable; replanning.'),
      finished('reflect', 'Reflect & self-repair', 1),
      started('plan', 'Reason & plan'),
      finished('plan', 'Reason & plan', 6800),
      started('act', 'Execute actions'),
      finished('act', 'Execute actions', 4300),
      started('reflect', 'Reflect & self-repair'),
      reflection(2, false, 'The goal was met.'),
      finished('reflect', 'Reflect & self-repair', 0),
      started('generate', 'Generate answer'),
      finished('generate', 'Generate answer', 2400),
    ]),
  )
  assert.equal(timing.rounds, 2)
  assert.equal(timing.roundBudget, 3)

  const rounds = timing.stages.map((s) => [s.node, s.round])
  assert.deepEqual(rounds, [
    // Retrieval is one stage that ran its own rounds internally, not a loop node.
    ['retrieve', null],
    ['plan', 1],
    ['act', 1],
    ['reflect', 1],
    ['plan', 2],
    ['act', 2],
    ['reflect', 2],
    ['generate', null],
  ])

  const firstReflect = timing.stages.find((s) => s.node === 'reflect')
  assert.equal(firstReflect.verdict, 'The tool returned nothing usable; replanning.')
})

test('a run that never loops carries no round on any stage', () => {
  seq = 0
  const timing = deriveTiming(
    runOf([started('generate', 'Generate answer'), finished('generate', 'Generate answer', 2400)]),
  )
  assert.equal(timing.rounds, 0)
  assert.equal(timing.roundBudget, null)
  assert.equal(timing.stages[0].round, null)
})
