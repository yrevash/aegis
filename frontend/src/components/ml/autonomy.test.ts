import { describe, expect, it } from 'vitest'

import type { MLGate } from '@/state/runReducer'
import type { Abstained } from '@/types/stream'

import { autonomyBand } from './autonomy'

const gate = (gated: boolean | null, band: MLGate['band'] = null): MLGate => ({
  gated,
  band,
  gateReason: 'reason',
  minConfidence: 0.85,
  maxRelWidth: 0.2,
})

const abstain: Abstained = {
  type: 'abstained',
  run_id: 'r',
  seq: 1,
  band: 'abstain',
  reason: 'degenerate interval',
  prediction: 0.5,
  conformal_confidence: 0.9,
}

describe('autonomyBand', () => {
  it('is autonomous when the conformal gate cleared', () => {
    expect(autonomyBand(gate(false), null).band).toBe('autonomous')
  })

  it('defers when the gate held', () => {
    expect(autonomyBand(gate(true), null).band).toBe('defer')
  })

  it('defers when queued to the inbox even if the gate cleared', () => {
    expect(autonomyBand(gate(false), null, true).band).toBe('defer')
  })

  it('abstains with highest precedence over defer/autonomous', () => {
    expect(autonomyBand(gate(false), abstain, true).band).toBe('abstain')
  })

  it('has no band before any verdict', () => {
    expect(autonomyBand(null, null).band).toBeNull()
  })

  it('honours the backend explicit band over the legacy gate', () => {
    // Gate says "cleared" (would be autonomous) but the explicit band says defer.
    expect(autonomyBand(gate(false, 'defer'), null).band).toBe('defer')
    // Gate says "held" (would be defer) but the explicit band says autonomous.
    expect(autonomyBand(gate(true, 'autonomous'), null).band).toBe('autonomous')
  })

  it('still lets an abstain event override an explicit non-abstain band', () => {
    expect(autonomyBand(gate(false, 'autonomous'), abstain).band).toBe('abstain')
  })
})
