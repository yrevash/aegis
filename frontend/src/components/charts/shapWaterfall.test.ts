import { describe, expect, it } from 'vitest'

import type { ShapFeature } from '@/types/stream'

import { buildWaterfall, waterfallPercent } from './shapWaterfall'

const feat = (feature: string, value: number, contribution: number): ShapFeature => ({
  feature,
  value,
  contribution,
})

describe('buildWaterfall', () => {
  it('reconstructs the prediction as base + Σ contribution', () => {
    const wf = buildWaterfall(14, [
      feat('reopened', 1, 3.1),
      feat('priority', 2, -4),
      feat('queue_depth', 12, 1.6),
    ])
    // 14 + 3.1 - 4 + 1.6 = 14.7
    expect(wf.reconstructed).toBeCloseTo(14.7, 5)
  })

  it('orders drivers by descending absolute contribution', () => {
    const wf = buildWaterfall(0, [
      feat('small', 1, 0.5),
      feat('big', 1, -5),
      feat('mid', 1, 2),
    ])
    expect(wf.steps.map((s) => s.feature)).toEqual(['big', 'mid', 'small'])
  })

  it('chains each step from the previous cumulative total', () => {
    const wf = buildWaterfall(10, [feat('a', 1, 4), feat('b', 1, -3)])
    // sorted: a(4) then b(-3)
    expect(wf.steps[0]).toMatchObject({ start: 10, end: 14, raises: true })
    expect(wf.steps[1]).toMatchObject({ start: 14, end: 11, raises: false })
  })

  it('flags raises vs lowers by contribution sign', () => {
    const wf = buildWaterfall(0, [feat('up', 1, 2), feat('down', 1, -2)])
    const up = wf.steps.find((s) => s.feature === 'up')
    const down = wf.steps.find((s) => s.feature === 'down')
    expect(up?.raises).toBe(true)
    expect(down?.raises).toBe(false)
  })

  it('spans the domain across every cumulative point, past the endpoints', () => {
    // sorted by |contribution|: up(+10) then down(−4): 5 → 15 → 11.
    // The peak (15) overshoots the final prediction (11), so the domain
    // must cover 5..15, not just base..reconstructed.
    const wf = buildWaterfall(5, [feat('up', 1, 10), feat('down', 1, -4)])
    expect(wf.reconstructed).toBe(11)
    expect(wf.domain[0]).toBe(5)
    expect(wf.domain[1]).toBe(15)
  })

  it('handles an empty feature set (prediction == base)', () => {
    const wf = buildWaterfall(7, [])
    expect(wf.steps).toEqual([])
    expect(wf.reconstructed).toBe(7)
    expect(wf.domain).toEqual([7, 7])
  })
})

describe('waterfallPercent', () => {
  it('maps the domain bounds to 0 and 100', () => {
    expect(waterfallPercent(3, [3, 15])).toBe(0)
    expect(waterfallPercent(15, [3, 15])).toBe(100)
  })

  it('places a mid value proportionally', () => {
    expect(waterfallPercent(9, [3, 15])).toBeCloseTo(50, 5)
  })

  it('clamps values outside the domain', () => {
    expect(waterfallPercent(-1, [0, 10])).toBe(0)
    expect(waterfallPercent(99, [0, 10])).toBe(100)
  })

  it('never divides by zero on a degenerate domain', () => {
    expect(waterfallPercent(7, [7, 7])).toBe(0)
  })
})
