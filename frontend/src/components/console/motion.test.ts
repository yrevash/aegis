/**
 * Unit tests for the active-beat helpers — the choreography's event→hue map.
 *
 * Covers projecting `state.lastSignal` into a renderable {@link Beat}, the
 * per-chip predicate, and the end-to-end event→beat pipeline through the real
 * {@link runReducer} so the trace/flow/graph/trust pulses agree on the hue.
 */

import { describe, expect, it } from 'vitest'

import { SIGNALS } from '@/config/signals'
import { initialRunState, runReducer } from '@/state/runReducer'
import type { StreamEvent, StreamEventBody } from '@/types/stream'

import { beatFromSignal, isBeatSignal } from './motion'

let seqCounter = 0
function ev(body: StreamEventBody): StreamEvent {
  seqCounter += 1
  return { ...body, run_id: 'run_test', seq: seqCounter } as StreamEvent
}

describe('beatFromSignal', () => {
  it('returns null before any event', () => {
    expect(beatFromSignal(null)).toBeNull()
  })

  it('projects the signal, seq and its ink hex', () => {
    const beat = beatFromSignal({ signal: 'ml', seq: 7 })
    expect(beat).toEqual({ signal: 'ml', seq: 7, hex: SIGNALS.ml.hex })
  })
})

describe('isBeatSignal', () => {
  it('matches only the beat that owns the hue', () => {
    const beat = beatFromSignal({ signal: 'graph', seq: 3 })
    expect(isBeatSignal(beat, 'graph')).toBe(true)
    expect(isBeatSignal(beat, 'agent')).toBe(false)
    expect(isBeatSignal(null, 'graph')).toBe(false)
  })
})

describe('event → active-beat pipeline', () => {
  const cases: Array<[StreamEventBody, string]> = [
    [{ type: 'retrieval', status: 'started', num_candidates: 0, touched_nodes: [], touched_edges: [], scored_sources: [] }, 'graph'],
    [{ type: 'ml_explanation', prediction: 0.5, conformal_interval: null, conformal_confidence: null, interval_width: null, prediction_set_size: null, shap_attribution: [], gated: null, band: null, gate_reason: null, min_confidence: null, max_rel_width: null }, 'ml'],
    [{ type: 'approval_required', approval_id: 'a', action: 'x', args: {}, risk: 'high', rationale: 'r' }, 'risk'],
    [{ type: 'guardrail', stage: 'input', verdict: 'block', reason: 'r', layer: null, redactions: [], before_masked: null, after: null }, 'block'],
    [{ type: 'reasoning', text: 'thinking' }, 'agent'],
  ]

  it('drives lastSignal and its beat hue from the newest event', () => {
    for (const [body, expected] of cases) {
      const state = runReducer(initialRunState, ev(body))
      const beat = beatFromSignal(state.lastSignal)
      expect(beat?.signal).toBe(expected)
      expect(beat?.hex).toBe(SIGNALS[beat!.signal].hex)
    }
  })

  it('advances the beat seq on every event so a repeated hue still re-fires', () => {
    let state = runReducer(initialRunState, ev({ type: 'reasoning', text: 'a' }))
    const first = beatFromSignal(state.lastSignal)!
    state = runReducer(state, ev({ type: 'reasoning', text: 'b' }))
    const second = beatFromSignal(state.lastSignal)!
    expect(first.signal).toBe(second.signal)
    expect(second.seq).toBeGreaterThan(first.seq)
  })
})
