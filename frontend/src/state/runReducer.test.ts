/**
 * Unit tests for the pure {@link runReducer} — the glass-box state projection.
 *
 * Each new backend event is exercised in isolation and in a small pipeline so a
 * regression in the reducer surfaces here, deterministically, with no React and
 * no timers.
 */

import { describe, expect, it } from 'vitest'

import type { StreamEvent, StreamEventBody } from '@/types/stream'

import { initialRunState, runReducer, type RunState } from './runReducer'

/** Reduce a list of events from the empty state. */
function reduceAll(events: StreamEvent[]): RunState {
  return events.reduce(runReducer, initialRunState)
}

/** Stamp envelope fields on a body so tests read as the wire shape. */
let seqCounter = 0
function ev(body: StreamEventBody): StreamEvent {
  seqCounter += 1
  return { ...body, run_id: 'run_test', seq: seqCounter } as StreamEvent
}

describe('runReducer — glass-box events', () => {
  it('accumulates the node_finished ledger in order', () => {
    const state = reduceAll([
      ev({ type: 'node_finished', node: 'plan', label: 'Planning', duration_ms: 540, model: 'gpt-4o-mini', prompt_tokens: 680, completion_tokens: 172, cost_usd: 0.0004 }),
      ev({ type: 'node_finished', node: 'generate', label: 'Drafting', duration_ms: 900, model: 'gpt-4o', prompt_tokens: 1840, completion_tokens: 96, cost_usd: 0.0038 }),
    ])
    expect(state.nodeLedger).toHaveLength(2)
    expect(state.nodeLedger.map((n) => n.node)).toEqual(['plan', 'generate'])
    expect(state.nodeLedger[1].cost_usd).toBeCloseTo(0.0038)
  })

  it('accumulates reasoning chunks into one string', () => {
    const state = reduceAll([
      ev({ type: 'reasoning', text: 'First. ' }),
      ev({ type: 'reasoning', text: 'Second.' }),
    ])
    expect(state.reasoning).toBe('First. Second.')
  })

  it('projects ML gate fields onto the mlGate slice', () => {
    const state = reduceAll([
      ev({
        type: 'ml_explanation',
        prediction: 0.82,
        conformal_interval: [0.64, 0.93],
        conformal_confidence: 0.9,
        interval_width: 0.29,
        prediction_set_size: 2,
        shap_attribution: [],
        gated: true,
        band: 'defer',
        gate_reason: 'too uncertain',
        min_confidence: 0.85,
        max_rel_width: 0.2,
      }),
    ])
    expect(state.ml?.prediction).toBe(0.82)
    expect(state.mlGate).toEqual({
      gated: true,
      band: 'defer',
      gateReason: 'too uncertain',
      minConfidence: 0.85,
      maxRelWidth: 0.2,
    })
  })

  it('captures reranked scored_sources and preserves them across later steps', () => {
    const scored = [
      { id: 'a', label: 'A', score: 0.9 },
      { id: 'b', label: 'B', score: 0.7 },
    ]
    const state = reduceAll([
      ev({ type: 'retrieval', status: 'started', num_candidates: 0, touched_nodes: [], touched_edges: [], scored_sources: [] }),
      ev({ type: 'retrieval', status: 'reranked', num_candidates: 5, touched_nodes: [], touched_edges: [], scored_sources: scored }),
      // A trailing 'done' with no scores must not wipe the ranking.
      ev({ type: 'retrieval', status: 'done', num_candidates: 5, touched_nodes: [], touched_edges: [], scored_sources: [] }),
    ])
    expect(state.retrievalScores).toEqual(scored)
    expect(state.candidates).toBe(5)
  })

  it('stores guardrail redaction entries with layer and masked fields', () => {
    const state = reduceAll([
      ev({
        type: 'guardrail',
        stage: 'input',
        verdict: 'redact',
        layer: 'pii',
        reason: 'masked PII',
        redactions: [{ kind: 'ssn' }, { kind: 'email' }],
        before_masked: 'SSN ***-**-6789',
        after: 'SSN [REDACTED_SSN]',
      }),
    ])
    expect(state.guardrails).toHaveLength(1)
    const g = state.guardrails[0]
    expect(g.verdict).toBe('redact')
    expect(g.layer).toBe('pii')
    expect(g.redactions.map((r) => r.kind)).toEqual(['ssn', 'email'])
    expect(g.after).toContain('[REDACTED_SSN]')
  })

  it('updates lastSignal on every event, keyed by seq and hue', () => {
    seqCounter = 0
    const events = [
      ev({ type: 'run_started', trace_id: 't' }),
      ev({ type: 'retrieval', status: 'started', num_candidates: 0, touched_nodes: [], touched_edges: [], scored_sources: [] }),
      ev({ type: 'reasoning', text: 'thinking' }),
      ev({ type: 'guardrail', stage: 'input', verdict: 'pass', layer: null, reason: 'ok', redactions: [], before_masked: null, after: null }),
    ]
    // After run_started (neutral)
    let s = runReducer(initialRunState, events[0])
    expect(s.lastSignal).toEqual({ signal: 'neutral', seq: 1 })
    // retrieval → graph
    s = runReducer(s, events[1])
    expect(s.lastSignal).toEqual({ signal: 'graph', seq: 2 })
    // reasoning → agent (additive event the design map defaults to neutral)
    s = runReducer(s, events[2])
    expect(s.lastSignal).toEqual({ signal: 'agent', seq: 3 })
    // guardrail → block
    s = runReducer(s, events[3])
    expect(s.lastSignal).toEqual({ signal: 'block', seq: 4 })
  })
})

describe('runReducer — new SSE events', () => {
  it('stores retrieval provenance without changing phase', () => {
    const state = reduceAll([
      ev({ type: 'run_started', trace_id: 't' }),
      ev({
        type: 'provenance',
        origins: ['vector', 'graph', 'bm25'],
        fusion: 'rrf',
        cache_hit: false,
        cache_kind: null,
        original_query: null,
        cached_at: null,
      }),
    ])
    expect(state.provenance?.fusion).toBe('rrf')
    expect(state.provenance?.origins).toEqual(['vector', 'graph', 'bm25'])
    expect(state.phase).toBe('streaming')
  })

  it('records an approval_queued enqueue and marks the run awaiting approval', () => {
    const state = reduceAll([
      ev({
        type: 'approval_queued',
        approval_id: 'appr_1',
        action: 'Issue refund',
        args: { amount: 100 },
        risk: 'high',
        rationale: 'deferred',
        sla_deadline: '2026-01-01T00:00:00Z',
        assignee_tier: 'tenant_admin',
      }),
    ])
    expect(state.approvalQueued?.approval_id).toBe('appr_1')
    expect(state.approvalQueued?.assignee_tier).toBe('tenant_admin')
    expect(state.awaitedApproval).toBe(true)
    expect(state.phase).toBe('awaiting_approval')
  })

  it('enters the abstained terminal phase and keeps it through run_finished', () => {
    const state = reduceAll([
      ev({ type: 'run_started', trace_id: 't' }),
      ev({
        type: 'abstained',
        band: 'abstain',
        reason: 'degenerate interval',
        prediction: 0.5,
        conformal_confidence: 0.9,
      }),
      ev({ type: 'run_finished', status: 'blocked', prompt_tokens: 1, completion_tokens: 0, cost_usd: 0, cache_hit: false }),
    ])
    expect(state.abstained?.reason).toBe('degenerate interval')
    // The abstain phase survives the terminal run_finished envelope.
    expect(state.phase).toBe('abstained')
    expect(state.running).toBe(false)
  })

  it('captures a budget_exceeded breach and blocks the run', () => {
    const state = reduceAll([
      ev({ type: 'run_started', trace_id: 't' }),
      ev({
        type: 'budget_exceeded',
        scope: 'tenant',
        scope_id: 2,
        limit_type: 'usd',
        limit: 1200,
        used: 1200.42,
        message: 'cap reached',
      }),
    ])
    expect(state.budgetExceeded?.scope_id).toBe(2)
    expect(state.budgetExceeded?.limit_type).toBe('usd')
    expect(state.phase).toBe('blocked')
  })

  it('resets the new event slices on a fresh run_started', () => {
    const first = reduceAll([
      ev({ type: 'run_started', trace_id: 't1' }),
      ev({
        type: 'budget_exceeded',
        scope: 'tenant',
        scope_id: 2,
        limit_type: 'usd',
        limit: 1,
        used: 2,
        message: 'x',
      }),
    ])
    const second = runReducer(first, ev({ type: 'run_started', trace_id: 't2' }))
    expect(second.budgetExceeded).toBeNull()
    expect(second.provenance).toBeNull()
    expect(second.abstained).toBeNull()
    expect(second.approvalQueued).toBeNull()
  })
})

describe('runReducer — pipeline invariants', () => {
  it('keeps events, steps, and ledger consistent through a mixed run', () => {
    seqCounter = 0
    const state = reduceAll([
      ev({ type: 'run_started', trace_id: 't' }),
      ev({ type: 'node_started', node: 'plan', label: 'Planning' }),
      ev({ type: 'reasoning', text: 'a ' }),
      ev({ type: 'reasoning', text: 'b' }),
      ev({ type: 'node_finished', node: 'plan', label: 'Planning', duration_ms: 10, model: 'gpt-4o-mini', prompt_tokens: 1, completion_tokens: 1, cost_usd: 0.0001 }),
      ev({ type: 'run_finished', status: 'completed', prompt_tokens: 1, completion_tokens: 1, cost_usd: 0.0001, cache_hit: false }),
    ])
    expect(state.events).toHaveLength(6)
    expect(state.steps).toHaveLength(1)
    expect(state.nodeLedger).toHaveLength(1)
    expect(state.reasoning).toBe('a b')
    expect(state.phase).toBe('completed')
    expect(state.running).toBe(false)
    expect(state.usage?.cost_usd).toBeCloseTo(0.0001)
  })
})
