import { describe, expect, it } from 'vitest'

import type { RiskEntry } from '@/types/api'

import {
  buildMatrix,
  exposureBand,
  exposureScore,
  RESIDUAL_META,
  residualCounts,
  residualSignal,
  worstFirst,
} from './riskMatrix'

const scale = { likelihood: [1, 2, 3, 4, 5], impact: [1, 2, 3, 4, 5] }

function risk(partial: Partial<RiskEntry> & Pick<RiskEntry, 'id' | 'likelihood' | 'impact'>): RiskEntry {
  return {
    title: partial.id,
    category: 'cat',
    mitigation: 'm',
    control_ref: 'ctrl',
    residual: 'low',
    ...partial,
  }
}

describe('residualSignal', () => {
  it('maps residual bands to green / amber / red signals', () => {
    expect(residualSignal('low')).toBe('ok')
    expect(residualSignal('medium')).toBe('risk')
    expect(residualSignal('high')).toBe('block')
  })

  it('orders residual severity low < medium < high', () => {
    expect(RESIDUAL_META.low.rank).toBeLessThan(RESIDUAL_META.medium.rank)
    expect(RESIDUAL_META.medium.rank).toBeLessThan(RESIDUAL_META.high.rank)
  })
})

describe('exposureBand', () => {
  it('is low in the calm corner, high in the worst corner', () => {
    expect(exposureBand(1, 1)).toBe('low')
    expect(exposureBand(5, 5)).toBe('high')
  })

  it('crosses low→medium at product 7 and medium→high at product 15', () => {
    expect(exposureBand(2, 3)).toBe('low') // 6
    expect(exposureBand(4, 2)).toBe('medium') // 8
    expect(exposureBand(3, 4)).toBe('medium') // 12
    expect(exposureBand(3, 5)).toBe('high') // 15
  })
})

describe('buildMatrix', () => {
  const risks = [
    risk({ id: 'A', likelihood: 5, impact: 5, residual: 'high' }),
    risk({ id: 'B', likelihood: 1, impact: 1, residual: 'low' }),
    risk({ id: 'C', likelihood: 3, impact: 3, residual: 'medium' }),
  ]

  it('is a 5×5 grid over the scale', () => {
    const m = buildMatrix(scale, risks)
    expect(m.rows).toHaveLength(5)
    expect(m.rows.every((row) => row.length === 5)).toBe(true)
    expect(m.likelihoods).toEqual([1, 2, 3, 4, 5])
    expect(m.impacts).toEqual([1, 2, 3, 4, 5])
  })

  it('renders rows impact-descending so the worst corner is top-right', () => {
    const m = buildMatrix(scale, risks)
    const topRight = m.rows[0][m.rows[0].length - 1]
    expect(topRight.likelihood).toBe(5)
    expect(topRight.impact).toBe(5)
    expect(topRight.worstCorner).toBe(true)
    expect(topRight.risks.map((r) => r.id)).toEqual(['A'])
  })

  it('plots each risk into exactly one cell', () => {
    const m = buildMatrix(scale, risks)
    const placed = m.rows.flat().flatMap((c) => c.risks.map((r) => r.id))
    expect(placed.sort()).toEqual(['A', 'B', 'C'])
    // bottom-left cell holds the calm risk B (likelihood 1, impact 1)
    const bottomLeft = m.rows[m.rows.length - 1][0]
    expect(bottomLeft.risks.map((r) => r.id)).toEqual(['B'])
  })

  it('collects off-scale risks in `unplaced` rather than dropping them', () => {
    const m = buildMatrix(scale, [...risks, risk({ id: 'X', likelihood: 9, impact: 2 })])
    expect(m.unplaced.map((r) => r.id)).toEqual(['X'])
    const placed = m.rows.flat().flatMap((c) => c.risks.map((r) => r.id))
    expect(placed).not.toContain('X')
  })
})

describe('worstFirst', () => {
  it('orders by exposure desc, then residual severity, then id', () => {
    const ranked = worstFirst([
      risk({ id: 'low-exp', likelihood: 1, impact: 2, residual: 'high' }),
      risk({ id: 'hi-exp', likelihood: 5, impact: 4, residual: 'low' }),
      risk({ id: 'tie-b', likelihood: 3, impact: 3, residual: 'high' }),
      risk({ id: 'tie-a', likelihood: 3, impact: 3, residual: 'high' }),
    ])
    expect(ranked.map((r) => r.id)).toEqual(['hi-exp', 'tie-a', 'tie-b', 'low-exp'])
  })
})

describe('exposureScore + residualCounts', () => {
  it('scores exposure as likelihood × impact', () => {
    expect(exposureScore({ likelihood: 4, impact: 3 })).toBe(12)
  })

  it('counts risks per residual band', () => {
    expect(
      residualCounts([
        risk({ id: 'a', likelihood: 1, impact: 1, residual: 'low' }),
        risk({ id: 'b', likelihood: 1, impact: 1, residual: 'low' }),
        risk({ id: 'c', likelihood: 1, impact: 1, residual: 'medium' }),
      ]),
    ).toEqual({ low: 2, medium: 1, high: 0 })
  })
})
