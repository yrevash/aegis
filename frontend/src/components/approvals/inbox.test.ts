import { describe, expect, it } from 'vitest'

import type { ApprovalRow } from '@/types/api'

import { applyDecision, pendingRows, statusForDecision } from './inbox'

/** A minimal approval row for the decision-flow tests. */
function row(id: number, status = 'pending'): ApprovalRow {
  return {
    id,
    run_id: `run_${id}`,
    action: 'do a thing',
    args: {},
    risk: 'high',
    rationale: 'because',
    status,
    persona: null,
    sla_deadline: null,
    created_at: new Date(0).toISOString(),
    ml_snapshot: null,
  }
}

describe('inbox decision flow', () => {
  it('maps a decision to its terminal status', () => {
    expect(statusForDecision('approve')).toBe('approved')
    expect(statusForDecision('reject')).toBe('rejected')
  })

  it('applies a decision to only the matching row, immutably', () => {
    const rows = [row(1), row(2)]
    const next = applyDecision(rows, 1, 'approve')
    expect(next[0].status).toBe('approved')
    expect(next[1].status).toBe('pending')
    // The source array is not mutated.
    expect(rows[0].status).toBe('pending')
  })

  it('filters to still-pending rows', () => {
    const rows = [row(1, 'pending'), row(2, 'approved'), row(3, 'pending')]
    expect(pendingRows(rows).map((r) => r.id)).toEqual([1, 3])
  })
})
