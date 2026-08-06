/**
 * Unit tests for the rerank sort + normalise logic behind the scoreboard.
 */

import { describe, expect, it } from 'vitest'

import { rankSources, type RankInput } from './rerank'

const sources: RankInput[] = [
  { id: 'b', label: 'Doc B', score: 0.4 },
  { id: 'a', label: 'Doc A', score: 0.9 },
  { id: 'c', label: 'Doc C', score: 0.6 },
]

describe('rankSources', () => {
  it('sorts by descending score and assigns 1-based ranks', () => {
    const ranked = rankSources(sources)
    expect(ranked.map((s) => s.id)).toEqual(['a', 'c', 'b'])
    expect(ranked.map((s) => s.rank)).toEqual([1, 2, 3])
  })

  it('normalises each bar fraction to the top score', () => {
    const ranked = rankSources(sources)
    expect(ranked[0].relative).toBeCloseTo(1, 5) // top score → full bar
    expect(ranked[1].relative).toBeCloseTo(0.6 / 0.9, 5)
    expect(ranked[2].relative).toBeCloseTo(0.4 / 0.9, 5)
  })

  it('caps to topK when provided', () => {
    const ranked = rankSources(sources, 2)
    expect(ranked).toHaveLength(2)
    expect(ranked.map((s) => s.id)).toEqual(['a', 'c'])
  })

  it('returns an empty list for no sources', () => {
    expect(rankSources([])).toEqual([])
  })

  it('yields zero fractions when the top score is 0 (no negative-width bars)', () => {
    const ranked = rankSources([
      { id: 'x', label: 'X', score: 0 },
      { id: 'y', label: 'Y', score: 0 },
    ])
    expect(ranked.every((s) => s.relative === 0)).toBe(true)
  })

  it('floors non-finite and negative scores to 0', () => {
    const ranked = rankSources([
      { id: 'good', label: 'Good', score: 0.8 },
      { id: 'nan', label: 'NaN', score: Number.NaN },
      { id: 'neg', label: 'Neg', score: -0.5 },
    ])
    expect(ranked[0].id).toBe('good')
    expect(ranked.find((s) => s.id === 'nan')?.score).toBe(0)
    expect(ranked.find((s) => s.id === 'neg')?.score).toBe(0)
    expect(ranked.every((s) => s.relative >= 0)).toBe(true)
  })

  it('is a pure sort — does not mutate the input array', () => {
    const input = [...sources]
    rankSources(input)
    expect(input.map((s) => s.id)).toEqual(['b', 'a', 'c'])
  })
})
