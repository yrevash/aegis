/**
 * Unit tests for the pure orchestration flow-map resolver.
 *
 * The flow-map is driven off the reduced {@link RunState}, so these tests feed
 * scripted events through the real {@link runReducer} and assert which node is
 * live and which conditional branch (gated vs direct) the map lit — no React,
 * no timers.
 */

import { describe, expect, it } from 'vitest'

import { initialRunState, runReducer, type RunState } from '@/state/runReducer'
import type { StreamEvent, StreamEventBody } from '@/types/stream'

import {
  FLOW_EDGES,
  FLOW_NODES,
  isEdgeActive,
  resolveFlow,
  type FlowEdge,
} from './orchestration'

let seqCounter = 0
function ev(body: StreamEventBody): StreamEvent {
  seqCounter += 1
  return { ...body, run_id: 'run_test', seq: seqCounter } as StreamEvent
}
function reduceAll(events: StreamEvent[]): RunState {
  return events.reduce(runReducer, initialRunState)
}
const findEdge = (from: string, to: string): FlowEdge =>
  FLOW_EDGES.find((e) => e.from === from && e.to === to)!

describe('resolveFlow — idle', () => {
  it('marks every node idle with no active node or branch on empty state', () => {
    const flow = resolveFlow(initialRunState)
    expect(flow.activeId).toBeNull()
    expect(flow.branch).toBeNull()
    for (const node of FLOW_NODES) expect(flow.status[node.id]).toBe('idle')
  })
})

describe('resolveFlow — active-node resolution', () => {
  it('lights the started-but-unfinished node as the frontier', () => {
    const state = reduceAll([
      ev({ type: 'node_started', node: 'guard_input', label: 'Screening' }),
      ev({ type: 'node_finished', node: 'guard_input', label: 'Screening', duration_ms: 70, model: null, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 }),
      ev({ type: 'node_started', node: 'retrieve', label: 'Searching' }),
    ])
    const flow = resolveFlow(state)
    expect(flow.status.guard_input).toBe('done')
    expect(flow.status.retrieve).toBe('active')
    expect(flow.activeId).toBe('retrieve')
  })

  it('maps the backend `ml` node onto the `ml` flow node', () => {
    const state = reduceAll([ev({ type: 'node_started', node: 'ml', label: 'Scoring' })])
    expect(resolveFlow(state).status.ml).toBe('active')
    expect(resolveFlow(state).activeId).toBe('ml')
  })

  it('still accepts the legacy `score` node id for the `ml` flow node', () => {
    const state = reduceAll([ev({ type: 'node_started', node: 'score', label: 'Scoring' })])
    expect(resolveFlow(state).status.ml).toBe('active')
    expect(resolveFlow(state).activeId).toBe('ml')
  })

  it('treats the awaiting-approval phase as the live approval node', () => {
    const state = reduceAll([
      ev({ type: 'approval_required', approval_id: 'a1', action: 'Refund', args: {}, risk: 'high', rationale: 'too uncertain' }),
    ])
    const flow = resolveFlow(state)
    expect(flow.status.approval).toBe('active')
    expect(flow.activeId).toBe('approval')
    expect(flow.branch).toBe('gated')
  })
})

describe('resolveFlow — conditional branch', () => {
  it('reports the gated branch once the run has paused at the gate', () => {
    const state = reduceAll([
      ev({ type: 'node_started', node: 'act', label: 'Acting' }),
      ev({ type: 'approval_required', approval_id: 'a1', action: 'Refund', args: {}, risk: 'high', rationale: 'r' }),
    ])
    expect(resolveFlow(state).branch).toBe('gated')
  })

  it('reports the direct branch when the run reaches act without gating', () => {
    const state = reduceAll([
      ev({ type: 'node_started', node: 'plan', label: 'Planning' }),
      ev({ type: 'node_started', node: 'act', label: 'Acting' }),
    ])
    const flow = resolveFlow(state)
    expect(flow.branch).toBe('direct')
    // A clean direct path (no failed tool) reads as autonomous, not denied.
    expect(flow.denied).toBe(false)
    // The gated conditional edges must not light on the direct path.
    expect(isEdgeActive(findEdge('ml', 'approval'), flow)).toBe(false)
  })

  it('marks a policy-denied action on the direct path as denied, not autonomous', () => {
    const state = reduceAll([
      ev({ type: 'node_started', node: 'plan', label: 'Planning' }),
      ev({ type: 'node_started', node: 'act', label: 'Acting' }),
      ev({ type: 'tool_call', call_id: 'c1', tool: 'issue_refund', args: {}, risk: 'high' }),
      ev({ type: 'tool_result', call_id: 'c1', ok: false, summary: 'Denied: not in allowlist.' }),
    ])
    const flow = resolveFlow(state)
    expect(flow.branch).toBe('direct')
    expect(flow.denied).toBe(true)
  })

  it('lights the stream node while answer tokens arrive, done when finished', () => {
    const streaming = reduceAll([ev({ type: 'token', text: 'Hello ' })])
    expect(resolveFlow(streaming).status.stream).toBe('active')
    const finished = reduceAll([
      ev({ type: 'token', text: 'Hello ' }),
      ev({ type: 'run_finished', status: 'completed', prompt_tokens: 1, completion_tokens: 1, cost_usd: 0, cache_hit: false }),
    ])
    expect(resolveFlow(finished).status.stream).toBe('done')
  })
})

describe('isEdgeActive', () => {
  it('lights a spine edge once its source is done and target reached', () => {
    const state = reduceAll([
      ev({ type: 'node_started', node: 'guard_input', label: 'g' }),
      ev({ type: 'node_finished', node: 'guard_input', label: 'g', duration_ms: 1, model: null, prompt_tokens: 0, completion_tokens: 0, cost_usd: 0 }),
      ev({ type: 'node_started', node: 'retrieve', label: 'r' }),
    ])
    const flow = resolveFlow(state)
    expect(isEdgeActive(findEdge('guard_input', 'retrieve'), flow)).toBe(true)
  })
})
