import { describe, expect, it } from 'vitest'

import { formatTokens, modelShares, modelSpendData, totalTokens } from './usage'

describe('usage roll-up helpers', () => {
  it('sums prompt + completion tokens', () => {
    expect(totalTokens({ total_prompt_tokens: 100, total_completion_tokens: 25 })).toBe(125)
  })

  it('computes per-model shares, sorted by cost descending', () => {
    const shares = modelShares([
      { model: 'a', cost_usd: 25, tokens: 10 },
      { model: 'b', cost_usd: 75, tokens: 20 },
    ])
    expect(shares.map((s) => s.model)).toEqual(['b', 'a'])
    expect(shares[0].share).toBeCloseTo(0.75)
    expect(shares[1].share).toBeCloseTo(0.25)
  })

  it('yields zero shares when total spend is zero (no divide-by-zero)', () => {
    expect(modelShares([{ model: 'a', cost_usd: 0, tokens: 0 }])[0].share).toBe(0)
  })

  it('formats large token counts compactly', () => {
    expect(formatTokens(42_800_000)).toMatch(/M/)
  })

  it('builds chart data sorted by spend with cycled colours', () => {
    const data = modelSpendData([
      { model: 'a', cost_usd: 10, tokens: 1 },
      { model: 'b', cost_usd: 40, tokens: 1 },
    ])
    expect(data.map((d) => d.model)).toEqual(['b', 'a'])
    expect(data[0].color).toBe('ml')
    expect(data[0].cost).toBe(40)
  })

  it('folds the long tail past the limit into a single "+n more" row', () => {
    const rows = Array.from({ length: 8 }, (_, i) => ({
      model: `m${i}`,
      cost_usd: 8 - i,
      tokens: 1,
    }))
    const data = modelSpendData(rows, 5)
    expect(data).toHaveLength(6)
    expect(data[5].model).toBe('+3 more')
    expect(data[5].color).toBe('neutral')
    expect(data[5].cost).toBeCloseTo(3 + 2 + 1) // tail costs 3,2,1 folded
  })
})
