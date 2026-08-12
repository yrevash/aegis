import { describe, expect, it } from 'vitest'

import type { NodeFinished } from '@/types/stream'

import { buildGantt, formatCost, formatMs } from './ganttLayout'

const node = (
  name: string,
  duration_ms: number,
  cost_usd: number,
): NodeFinished => ({
  run_id: 'r1',
  seq: 0,
  type: 'node_finished',
  node: name,
  label: name,
  duration_ms,
  model: cost_usd > 0 ? 'gpt-4o' : null,
  prompt_tokens: 0,
  completion_tokens: 0,
  cost_usd,
})

describe('formatMs', () => {
  it('keeps sub-second durations in rounded ms', () => {
    expect(formatMs(612)).toBe('612ms')
    expect(formatMs(0)).toBe('0ms')
  })

  it('switches to one-decimal seconds at and above 1000ms', () => {
    expect(formatMs(1000)).toBe('1.0s')
    expect(formatMs(2340)).toBe('2.3s')
  })
})

describe('formatCost', () => {
  it('renders a positive cost to four decimals', () => {
    expect(formatCost(0.0031)).toBe('$0.0031')
  })

  it('renders a zero (non-LLM) cost as an em dash', () => {
    expect(formatCost(0)).toBe('—')
  })
})

describe('buildGantt', () => {
  it('sums total duration and cost across nodes', () => {
    const g = buildGantt([node('plan', 200, 0.001), node('retrieve', 300, 0)])
    expect(g.totalMs).toBe(500)
    expect(g.totalCost).toBeCloseTo(0.001, 6)
  })

  it('lays bars end-to-end as percentages of the run', () => {
    const g = buildGantt([node('a', 250, 0), node('b', 750, 0)])
    expect(g.bars[0]).toMatchObject({ offsetPct: 0, widthPct: 25 })
    expect(g.bars[1]).toMatchObject({ offsetPct: 25, widthPct: 75 })
  })

  it('pre-formats the ms and cost columns per bar', () => {
    const g = buildGantt([node('gen', 1200, 0.0042)])
    expect(g.bars[0].ms).toBe('1.2s')
    expect(g.bars[0].cost).toBe('$0.0042')
  })

  it('never divides by zero on a zero-duration run', () => {
    const g = buildGantt([node('noop', 0, 0)])
    expect(g.bars[0].offsetPct).toBe(0)
    expect(g.bars[0].widthPct).toBe(0)
    expect(g.totalMs).toBe(0)
  })

  it('returns empty bars for no nodes', () => {
    const g = buildGantt([])
    expect(g.bars).toEqual([])
    expect(g.totalMs).toBe(0)
    expect(g.totalCost).toBe(0)
  })
})
