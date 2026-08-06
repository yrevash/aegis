import { describe, expect, it } from 'vitest'

import type { SavingsResponse } from '@/types/api'

import { breakdownData, breakdownTotal, reconcile, savedFraction } from './savingsCalc'

const breakdown = [
  { source: 'Small-model routing', saved_usd: 6_120, explanation: 'a' },
  { source: 'Semantic cache hits', saved_usd: 1_740, explanation: 'b' },
  { source: 'Prompt / context trimming', saved_usd: 710, explanation: 'c' },
]

describe('breakdownTotal', () => {
  it('sums the itemised savings', () => {
    expect(breakdownTotal(breakdown)).toBe(8_570)
  })

  it('is zero for no rows', () => {
    expect(breakdownTotal([])).toBe(0)
  })
})

describe('breakdownData', () => {
  it('sorts largest-saving first with shares that sum to 1', () => {
    const data = breakdownData(breakdown)
    expect(data.map((d) => d.source)).toEqual([
      'Small-model routing',
      'Semantic cache hits',
      'Prompt / context trimming',
    ])
    expect(data.reduce((s, d) => s + d.share, 0)).toBeCloseTo(1)
    expect(data[0].share).toBeCloseTo(6_120 / 8_570)
  })

  it('cycles signal colours, leading with ok (green = money saved)', () => {
    const data = breakdownData(breakdown)
    expect(data[0].color).toBe('ok')
    expect(data[1].color).toBe('graph')
  })

  it('yields zero shares when nothing was saved (no divide-by-zero)', () => {
    const data = breakdownData([{ source: 'x', saved_usd: 0, explanation: '' }])
    expect(data[0].share).toBe(0)
  })
})

describe('savedFraction', () => {
  it('computes the fraction saved vs baseline', () => {
    expect(savedFraction(12_480, 3_910)).toBeCloseTo(8_570 / 12_480)
  })

  it('guards a zero baseline and clamps a negative saving to 0', () => {
    expect(savedFraction(0, 10)).toBe(0)
    expect(savedFraction(100, 150)).toBe(0)
  })
})

describe('reconcile', () => {
  const base: SavingsResponse = {
    generated_at: '2026-01-01T00:00:00Z',
    baseline_cost_usd: 12_480,
    actual_cost_usd: 3_910,
    saved_usd: 8_570,
    saved_pct: 8_570 / 12_480,
    note: 'n',
    breakdown,
  }

  it('reconciles when the breakdown sums to the headline saving', () => {
    const r = reconcile(base)
    expect(r.headline).toBe(8_570)
    expect(r.breakdown).toBe(8_570)
    expect(r.deltaUsd).toBe(0)
    expect(r.reconciles).toBe(true)
  })

  it('flags a mismatch beyond tolerance and reports the delta', () => {
    const r = reconcile({ ...base, saved_usd: 9_000 })
    expect(r.deltaUsd).toBe(430)
    expect(r.reconciles).toBe(false)
  })
})
