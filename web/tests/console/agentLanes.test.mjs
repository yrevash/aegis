/**
 * The three claims the live run surface makes, and the way each one fails.
 *
 * Not one test per prop. These cover the assertions that would be *wrong on screen* if
 * the derivation broke: that a run without agent identity degrades to one supervisor
 * lane instead of inventing cards, that a fan-out's cards are allocated once and never
 * re-ordered, that a timed-out agent shows its designed terminal state rather than a
 * stuck spinner, and that a citation shows a page only when the run actually reported
 * one.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  SUPERVISOR_LANE,
  deriveActivity,
  deriveAgentPanel,
} from '../../src/components/console/agentLanes.ts'
import { readSources } from '../../src/components/console/sources.ts'

/** A RunState carrying only the fields these derivations read. */
function stateOf(events, overrides = {}) {
  return {
    phase: 'completed',
    running: false,
    events,
    steps: events.filter((e) => e.type === 'node_started'),
    nodeLedger: events.filter((e) => e.type === 'node_finished'),
    toolCalls: events.filter((e) => e.type === 'tool_call'),
    toolResults: events.filter((e) => e.type === 'tool_result'),
    retrievalScores: [],
    ...overrides,
  }
}

let seq = 0
const at = (event) => ({ run_id: 'r1', seq: (seq += 1), ...event })

test('a run with no agent identity degrades to one supervisor lane', () => {
  const state = stateOf([
    at({ type: 'node_started', node: 'plan', label: 'Plan' }),
    at({ type: 'tool_call', call_id: 'c1', tool: 'web_search', args: { q: 'aegis' }, risk: 'low' }),
    at({ type: 'tool_result', call_id: 'c1', ok: true, summary: '5 results' }),
    at({
      type: 'node_finished',
      node: 'plan',
      label: 'Plan',
      duration_ms: 120,
      model: 'gpt',
      prompt_tokens: 10,
      completion_tokens: 5,
      cost_usd: 0.002,
    }),
  ])

  const panel = deriveAgentPanel(state)

  assert.equal(panel.attributed, false, 'nothing on the wire carried an agent_id')
  assert.equal(panel.lanes.length, 1, 'one lane, not one card per graph node')
  assert.equal(panel.lanes[0].id, SUPERVISOR_LANE)
  assert.equal(panel.lanes[0].status, 'done')
  assert.equal(panel.lanes[0].tools.length, 1)
  assert.equal(panel.lanes[0].tools[0].summary, '5 results')
  assert.equal(panel.lanes[0].durationMs, 120)
})

test('an untouched run offers a single queued lane and no activity', () => {
  const state = stateOf([], { phase: 'idle', running: false })

  const panel = deriveAgentPanel(state)

  assert.equal(panel.lanes.length, 1)
  assert.equal(panel.lanes[0].status, 'queued')
  assert.equal(panel.lanes[0].detail, '', 'no invented current-action line')
  assert.equal(panel.lanes[0].durationMs, null, 'nothing measured is nothing shown')
  assert.deepEqual(deriveActivity(state), [])
})

test('a fan-out gets one card per agent, in first-sighting order, and never re-orders', () => {
  const beat = (agent_id, status, label) =>
    at({ type: 'agent_status', agent_id, role: 'research', label, status, detail: '' })

  const panel = deriveAgentPanel(
    stateOf([
      beat('a2', 'started', 'Web research'),
      beat('a1', 'started', 'Knowledge base'),
      beat('a2', 'acting', 'Web research'),
      beat('a1', 'done', 'Knowledge base'),
      beat('a2', 'done', 'Web research'),
    ]),
  )

  assert.equal(panel.attributed, true)
  assert.deepEqual(
    panel.lanes.map((l) => l.id),
    ['a2', 'a1'],
    'allocation order is first sighting, not completion order',
  )
  assert.deepEqual(
    panel.lanes.map((l) => l.label),
    ['Web research', 'Knowledge base'],
  )
})

test('an omitted agent wears the merge terminal state instead of spinning', () => {
  const panel = deriveAgentPanel(
    stateOf([
      at({
        type: 'agent_status',
        agent_id: 'a1',
        role: 'knowledge',
        label: 'Knowledge base',
        status: 'done',
        detail: '',
      }),
      at({
        type: 'agent_status',
        agent_id: 'a2',
        role: 'research',
        label: 'Web research',
        status: 'acting',
        detail: 'searching',
      }),
      at({
        type: 'synthesis',
        contributing: [{ agent_id: 'a1', role: 'knowledge', label: 'Knowledge base' }],
        omitted: [
          {
            agent_id: 'a2',
            role: 'research',
            label: 'Web research',
            status: 'timeout',
            reason: 'timed out at 45 s',
          },
        ],
        summary: 'Synthesised from 1 of 2 agents; web research timed out.',
      }),
    ]),
  )

  const dropped = panel.lanes.find((l) => l.id === 'a2')
  assert.equal(dropped.status, 'timeout', 'the beat that never came is not a spinner')
  assert.equal(dropped.omittedReason, 'timed out at 45 s')
  assert.equal(dropped.contributed, false)
  assert.equal(panel.lanes.find((l) => l.id === 'a1').contributed, true)
  assert.equal(panel.synthesis.summary, 'Synthesised from 1 of 2 agents; web research timed out.')
})

test('the activity rail carries only what no agent owns', () => {
  const items = deriveActivity(
    stateOf([
      at({
        type: 'retrieval',
        status: 'candidates',
        num_candidates: 12,
        touched_nodes: [],
        touched_edges: [],
        scored_sources: [],
      }),
      at({
        type: 'guardrail',
        stage: 'input',
        verdict: 'pass',
        reason: 'no injection found',
        layer: 'injection',
        redactions: [],
        before_masked: null,
        after: null,
      }),
      at({
        type: 'tool_call',
        agent_id: 'a1',
        call_id: 'c9',
        tool: 'web_search',
        args: {},
        risk: 'low',
      }),
    ]),
  )

  assert.deepEqual(
    items.map((i) => i.title),
    ['Recalled candidates', 'Input rail — passed'],
    "an agent's tool call belongs to its card, not the rail",
  )
})

test('a citation shows its page only when the run reported one', () => {
  const [withPage, withoutPage, unverified] = readSources([
    { id: 'c1', label: 'Transformers scale…', score: 0.91, page_no: 4, bbox: [1, 2, 3, 4] },
    { id: 'c2', label: 'Unattributed chunk', score: 0.62 },
    {
      id: 'c3',
      label: 'A quote the model made up',
      score: 0.4,
      citation_status: 'unverified',
      matched_fraction: 0.12,
    },
  ])

  assert.equal(withPage.page, 4)
  assert.equal(withPage.located, true)

  assert.equal(withoutPage.page, null, 'a missing page is never defaulted to 1')
  assert.equal(withoutPage.located, false)
  assert.equal(withoutPage.verbatim, null, 'no check run is not the same as a pass')

  assert.equal(unverified.verbatim, 'unverified')
  assert.equal(unverified.matchedFraction, 0.12)
})
