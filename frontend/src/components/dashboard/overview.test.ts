/**
 * Unit tests for the pure Overview derivations. No React, no recharts, no
 * timers — so the bento's headline figures are provably a function of the real
 * `GET /metrics` payload. (Importing recharts into a `.test.ts` hangs vitest, so
 * this module and the chart components stay strictly separate.)
 */

import { describe, expect, it } from 'vitest'

import type { MetricsResponse } from '@/types/api'

import { costSavedTrend, modelMixData, pct, reductionPct, sessionSavedDelta } from './overview'

/** A minimal metrics sample with an explicit cumulative saving. */
function sample(saved: number): MetricsResponse {
  return {
    cache_hit_rate: 0.7,
    small_model_share: 0.6,
    cost_per_1k_queries_usd: 3.85,
    quality_score: 0.92,
    routing: { generation: 'gpt-4o', triage: 'mini' },
    cost_saved_usd: saved,
    baseline_cost_usd: 12.4,
  }
}

describe('costSavedTrend', () => {
  it('extracts the finite cost_saved series in order', () => {
    expect(costSavedTrend([sample(10), sample(20), sample(35)])).toEqual([10, 20, 35])
  })

  it('drops non-finite values', () => {
    expect(costSavedTrend([sample(10), sample(Number.NaN), sample(30)])).toEqual([10, 30])
  })

  it('is empty for no history', () => {
    expect(costSavedTrend([])).toEqual([])
  })
})

describe('sessionSavedDelta', () => {
  it('is the positive gain across the window', () => {
    expect(sessionSavedDelta([sample(10), sample(18), sample(31)])).toBeCloseTo(21, 10)
  })

  it('is null below two samples', () => {
    expect(sessionSavedDelta([sample(10)])).toBeNull()
    expect(sessionSavedDelta([])).toBeNull()
  })

  it('never invents momentum from a flat or falling window', () => {
    expect(sessionSavedDelta([sample(30), sample(30)])).toBeNull()
    expect(sessionSavedDelta([sample(30), sample(12)])).toBeNull()
  })
})

describe('modelMixData', () => {
  it('splits the live share into two rounded slices summing to 100', () => {
    const mix = modelMixData(0.63)
    expect(mix).toEqual([
      { name: 'Small model', value: 63, color: 'agent' },
      { name: 'Large model', value: 37, color: 'ml' },
    ])
    expect(mix[0].value + mix[1].value).toBe(100)
  })

  it('clamps out-of-range shares', () => {
    expect(modelMixData(1.4)[0].value).toBe(100)
    expect(modelMixData(-0.2)[0].value).toBe(0)
  })

  it('is empty before any metric arrives', () => {
    expect(modelMixData(null)).toEqual([])
    expect(modelMixData(Number.NaN)).toEqual([])
  })
})

describe('reductionPct', () => {
  it('is the bounded reduction versus baseline as a whole percent', () => {
    expect(reductionPct(12.4, 3.85)).toBe(69)
  })

  it('is null when an operand is missing', () => {
    expect(reductionPct(null, 3.85)).toBeNull()
    expect(reductionPct(12.4, null)).toBeNull()
  })
})

describe('pct', () => {
  it('formats a fraction as a whole-number percentage', () => {
    expect(pct(0.742)).toBe('74%')
    expect(pct(1)).toBe('100%')
  })

  it('renders an em dash for missing input', () => {
    expect(pct(null)).toBe('—')
    expect(pct(Number.NaN)).toBe('—')
  })
})
