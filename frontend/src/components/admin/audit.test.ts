import { describe, expect, it } from 'vitest'

import type { AuditLogRow } from '@/types/api'

import { actorsOf, auditCounts, eventsPerHour, filterRows, resultOf } from './audit'

const row = (over: Partial<AuditLogRow>): AuditLogRow => ({
  id: 1,
  ts: '2026-08-04T12:00:00Z',
  action: 'answer',
  actor: 'alice',
  model: '4o',
  trace_id: 'a1b2',
  approved_by: null,
  ...over,
})

describe('audit derivations', () => {
  it('classifies blocked vs completed from the action', () => {
    expect(resultOf('guardrail.injection')).toBe('blocked')
    expect(resultOf('tool.refund denied')).toBe('blocked')
    expect(resultOf('answer')).toBe('completed')
  })

  it('tallies total, blocked and approved counts', () => {
    const counts = auditCounts([
      row({ id: 1, action: 'answer' }),
      row({ id: 2, action: 'guardrail.pii' }),
      row({ id: 3, action: 'refund', approved_by: 'bob' }),
    ])
    expect(counts).toEqual({ total: 3, blocked: 1, approved: 1 })
  })

  it('buckets events into contiguous clock-hours ending at the newest row', () => {
    const buckets = eventsPerHour(
      [
        row({ id: 1, ts: '2026-08-04T10:15:00Z' }),
        row({ id: 2, ts: '2026-08-04T10:45:00Z' }),
        row({ id: 3, ts: '2026-08-04T12:05:00Z' }),
      ],
      3,
    )
    expect(buckets).toHaveLength(3)
    expect(buckets.reduce((s, b) => s + b.count, 0)).toBe(3)
    // newest row (12:xx) lands in the last bucket; the two 10:xx rows in the first.
    expect(buckets[0].count).toBe(2)
    expect(buckets[2].count).toBe(1)
  })

  it('lists distinct actors sorted, skipping blanks', () => {
    expect(
      actorsOf([row({ actor: 'bob' }), row({ actor: 'alice' }), row({ actor: null }), row({ actor: 'bob' })]),
    ).toEqual(['alice', 'bob'])
  })

  it('filters rows by result and actor', () => {
    const rows = [
      row({ id: 1, action: 'answer', actor: 'alice' }),
      row({ id: 2, action: 'guardrail.pii', actor: 'system' }),
      row({ id: 3, action: 'answer', actor: 'system' }),
    ]
    expect(filterRows(rows, { result: 'blocked', actor: null }).map((r) => r.id)).toEqual([2])
    expect(filterRows(rows, { result: null, actor: 'system' }).map((r) => r.id)).toEqual([2, 3])
    expect(filterRows(rows, { result: 'completed', actor: 'system' }).map((r) => r.id)).toEqual([3])
  })
})
