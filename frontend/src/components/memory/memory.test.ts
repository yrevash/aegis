/**
 * Logic + render-smoke tests for the Memory surface (§4.3).
 *
 * Runs in a plain Node environment (no jsdom): pure `memoryText` logic is
 * asserted directly, and the chart-free presentational pieces are mounted with
 * `renderToStaticMarkup`. Nothing here imports recharts or the force-graph (the
 * vitest caveat) — those paths are covered by build/typecheck + Wave 2 QA.
 */

import { createElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { TooltipProvider } from '@/components/ui/tooltip'
import type { MemoryFactRow, MemoryWriteRow, RecallDebugItem } from '@/types/memory'

import { MiniMeter } from './MiniMeter'
import { SubjectSummary } from './SubjectSummary'
import {
  RECALL_DIMENSIONS,
  budgetPct,
  factGrowthSeries,
  factStatus,
  humanizeKey,
  humanizeValue,
  recallScores,
  recency,
  summaryHighlights,
  totalTurns,
  validFactCount,
} from './memoryText'

const render = (el: ReactElement): string => renderToStaticMarkup(el)

function fact(over: Partial<MemoryFactRow>): MemoryFactRow {
  return {
    id: 1,
    subject_id: 'cust-mreed',
    fact_type: 'preference',
    subject: 'M. Reed',
    predicate: 'prefers',
    object: 'email',
    text: 'Prefers email contact',
    confidence: 0.88,
    importance: 6,
    access_count: 3,
    valid_at: '2026-07-01T00:00:00Z',
    invalid_at: null,
    created_at: null,
    expired_at: null,
    source_turn_ids: [],
    supersedes_id: null,
    is_valid: true,
    ...over,
  }
}

function write(over: Partial<MemoryWriteRow>): MemoryWriteRow {
  return { id: 1, op: 'ADD', fact_id: 1, before: {}, after: {}, reason: null, model: null, trace_id: null, ts: null, ...over }
}

describe('recency', () => {
  it('is 1 at age 0 and decays with age', () => {
    expect(recency(0)).toBe(1)
    expect(recency(7)).toBeCloseTo(0.5, 5)
    expect(recency(-5)).toBe(1) // negative ages clamp to fresh
  })
})

describe('budgetPct', () => {
  it('clamps to 0..100 and rounds', () => {
    expect(budgetPct(256, 512)).toBe(50)
    expect(budgetPct(9999, 512)).toBe(100)
    expect(budgetPct(-5, 512)).toBe(0)
    expect(budgetPct(10, 0)).toBe(0)
  })
})

describe('fact counts + status', () => {
  it('counts only valid facts', () => {
    expect(validFactCount([fact({ id: 1 }), fact({ id: 2, is_valid: false })])).toBe(1)
  })

  it('maps validity to plain language', () => {
    expect(factStatus(fact({ is_valid: true }))).toEqual({ label: 'Current', tone: 'ok' })
    expect(factStatus(fact({ is_valid: false }))).toEqual({ label: 'Superseded', tone: 'muted' })
  })

  it('sums session turns', () => {
    expect(
      totalTurns([
        { id: 'a', subject_id: 's', persona: null, turn_count: 8, summary: null, created_at: null, last_active_at: null },
        { id: 'b', subject_id: 's', persona: null, turn_count: 5, summary: null, created_at: null, last_active_at: null },
      ]),
    ).toBe(13)
  })
})

describe('humanize + summaryHighlights', () => {
  it('humanises keys and values without inventing data', () => {
    expect(humanizeKey('open_cases')).toBe('open cases')
    expect(humanizeValue(['email_first', 'sms'])).toBe('email first, sms')
    expect(humanizeValue(1234)).toBe('1,234')
    expect(humanizeValue(null)).toBe('—')
  })

  it('picks up to N non-empty highlights, skipping blanks', () => {
    const h = summaryHighlights({ plan: 'Premium', region: 'EU', open_cases: 2, empty: '', missing: null }, 3)
    expect(h).toEqual([
      { label: 'plan', value: 'Premium' },
      { label: 'region', value: 'EU' },
      { label: 'open cases', value: '2' },
    ])
  })
})

describe('recallScores + dimensions', () => {
  it('exposes three plain dimensions', () => {
    expect(RECALL_DIMENSIONS.map((d) => d.label)).toEqual(['Match', 'Fresh', 'Weight'])
  })

  it('derives clamped 0..1 sub-scores from an item', () => {
    const item: RecallDebugItem = { key: 'f1', text: 'x', score: 0.8, importance: 6, age_days: 7, injected: true }
    const s = recallScores(item)
    expect(s.match).toBeCloseTo(0.8, 5)
    expect(s.fresh).toBeCloseTo(0.5, 5)
    expect(s.weight).toBeCloseTo(0.6, 5)
  })
})

describe('factGrowthSeries', () => {
  it('builds a chronological cumulative series (ADD grows, INVALIDATE retires)', () => {
    const series = factGrowthSeries([
      write({ id: 3, op: 'ADD', ts: '2026-07-03T00:00:00Z' }),
      write({ id: 1, op: 'ADD', ts: '2026-07-01T00:00:00Z' }),
      write({ id: 2, op: 'INVALIDATE', ts: '2026-07-02T00:00:00Z' }),
    ])
    expect(series).toEqual([1, 0, 1])
  })

  it('ignores entries without a timestamp', () => {
    expect(factGrowthSeries([write({ ts: null })])).toEqual([])
  })
})

describe('MiniMeter', () => {
  it('renders a clamped percentage width as a meter', () => {
    const html = render(createElement(MiniMeter, { value: 0.42 }))
    expect(html).toContain('width:42%')
    expect(html).toContain('role="meter"')
    expect(html).toContain('aria-valuenow="42"')
  })

  it('clamps out-of-range values', () => {
    expect(render(createElement(MiniMeter, { value: 5 }))).toContain('width:100%')
    expect(render(createElement(MiniMeter, { value: -1 }))).toContain('width:0%')
  })
})

describe('SubjectSummary', () => {
  it('leads with the subject, plain highlights and count-up stats', () => {
    const html = render(
      createElement(TooltipProvider, {
        // eslint-disable-next-line react/no-children-prop -- createElement props form
        children: createElement(SubjectSummary, {
          subjectLabel: 'M. Reed · A-771',
          highlights: [{ label: 'plan', value: 'Premium' }],
          lastSeen: '3d ago',
          stats: [
            { label: 'Facts', value: 24 },
            { label: 'Sessions', value: 6 },
            { label: 'Turns', value: 148 },
          ],
          sample: true,
        }),
      }),
    )
    expect(html).toContain('M. Reed · A-771')
    expect(html).toContain('Premium')
    expect(html).toContain('last seen 3d ago')
    expect(html).toContain('24')
    expect(html).toContain('sample')
  })

  it('shows an honest empty line when nothing is known yet', () => {
    const html = render(
      createElement(TooltipProvider, {
        // eslint-disable-next-line react/no-children-prop -- createElement props form
        children: createElement(SubjectSummary, {
          subjectLabel: 'Unknown',
          highlights: [],
          lastSeen: null,
          stats: [{ label: 'Facts', value: null }],
        }),
      }),
    )
    expect(html).toContain('No profile consolidated')
    expect(html).toContain('—')
  })
})
