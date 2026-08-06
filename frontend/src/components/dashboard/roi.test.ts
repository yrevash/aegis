/**
 * Unit tests for the pure ROI math. Deterministic — no timers, no React, no
 * randomness — so the money figures on the dashboard are provably derived from
 * the real `GET /metrics` fields (and the sample assumptions are the only
 * non-measured inputs).
 */

import { describe, expect, it } from 'vitest'

import {
  PER_MILLION,
  PER_THOUSAND,
  SAMPLE_ASSUMPTIONS,
  costAtVolumeUsd,
  costPerQueryUsd,
  costReductionRatio,
  formatCountCompact,
  formatUsd,
  formatUsdCompact,
  manualCostPerCaseUsd,
  manualVsAgentMultiple,
  savedAtVolumeUsd,
  savedPer1kUsd,
  savedPerCaseUsd,
  type RoiAssumptions,
} from './roi'

// A stable, mock-shaped metrics slice (mirrors `mockMetrics()` at wobble 0).
const COST_PER_1K = 3.85
const BASELINE = 12.4

describe('measured derivations (real metric fields)', () => {
  it('costPerQueryUsd divides cost-per-1k by 1000', () => {
    expect(costPerQueryUsd(COST_PER_1K)).toBeCloseTo(0.00385, 10)
    expect(costPerQueryUsd(0)).toBe(0)
  })

  it('costAtVolumeUsd scales linearly with volume', () => {
    expect(costAtVolumeUsd(COST_PER_1K, PER_THOUSAND)).toBeCloseTo(3.85, 10)
    expect(costAtVolumeUsd(COST_PER_1K, PER_MILLION)).toBeCloseTo(3850, 10)
    expect(costAtVolumeUsd(COST_PER_1K, 0)).toBe(0)
  })

  it('savedPer1kUsd is the baseline-minus-blended gap', () => {
    expect(savedPer1kUsd(BASELINE, COST_PER_1K)).toBeCloseTo(8.55, 10)
  })

  it('savedAtVolumeUsd projects the measured gap across volume', () => {
    expect(savedAtVolumeUsd(BASELINE, COST_PER_1K, PER_MILLION)).toBeCloseTo(8550, 10)
    expect(savedAtVolumeUsd(BASELINE, COST_PER_1K, SAMPLE_ASSUMPTIONS.monthlyVolume)).toBeCloseTo(
      8.55 * (SAMPLE_ASSUMPTIONS.monthlyVolume / PER_THOUSAND),
      10,
    )
  })

  it('costReductionRatio is a bounded 0..1 fraction', () => {
    expect(costReductionRatio(BASELINE, COST_PER_1K)).toBeCloseTo(8.55 / 12.4, 10)
    // Never divides by zero or overshoots.
    expect(costReductionRatio(0, COST_PER_1K)).toBe(0)
    expect(costReductionRatio(BASELINE, 0)).toBe(1)
    // A blended cost above baseline (no saving) clamps to 0, never negative.
    expect(costReductionRatio(BASELINE, BASELINE + 5)).toBe(0)
  })
})

describe('manual-vs-agent (real unit cost, sample labour inputs)', () => {
  const a: RoiAssumptions = { monthlyVolume: 100_000, manualMinutes: 30, laborRateUsdPerHour: 60, agentSeconds: 10 }

  it('manualCostPerCaseUsd is minutes/60 × rate', () => {
    // 30 min at $60/hr = exactly $30.
    expect(manualCostPerCaseUsd(a)).toBeCloseTo(30, 10)
    expect(manualCostPerCaseUsd(SAMPLE_ASSUMPTIONS)).toBeCloseTo((25 / 60) * 45, 10)
  })

  it('savedPerCaseUsd nets the sample labour cost against the real unit cost', () => {
    expect(savedPerCaseUsd(COST_PER_1K, a)).toBeCloseTo(30 - 0.00385, 10)
  })

  it('manualVsAgentMultiple divides sample labour by the real unit cost', () => {
    expect(manualVsAgentMultiple(COST_PER_1K, a)).toBeCloseTo(30 / 0.00385, 6)
    // Guards a zero unit cost.
    expect(manualVsAgentMultiple(0, a)).toBe(0)
  })
})

describe('formatting', () => {
  it('formatUsd fixes fraction digits', () => {
    expect(formatUsd(3.85)).toBe('$3.85')
    expect(formatUsd(8.55, 0)).toBe('$9')
    expect(formatUsd(0.00385, 4)).toBe('$0.0039')
  })

  it('formatUsdCompact abbreviates large amounts', () => {
    expect(formatUsdCompact(3850)).toBe('$3.9K')
    expect(formatUsdCompact(1_200_000)).toBe('$1.2M')
  })

  it('formatCountCompact abbreviates counts', () => {
    expect(formatCountCompact(250_000)).toBe('250K')
    expect(formatCountCompact(PER_MILLION)).toBe('1M')
  })
})
