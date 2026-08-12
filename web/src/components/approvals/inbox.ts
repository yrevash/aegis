/**
 * Pure helpers for the approvals inbox decision flow. Kept side-effect free so
 * the optimistic update and filtering are unit-testable without the network or
 * React.
 */

import type { ApprovalDecision, ApprovalRow } from '@/lib/api/types'

/** Terminal status a decision transitions a row to. */
export function statusForDecision(decision: ApprovalDecision): 'approved' | 'rejected' {
  return decision === 'approve' ? 'approved' : 'rejected'
}

/**
 * Optimistically apply a decision to the row set, returning a new array with the
 * matching row's status updated. Non-matching rows are returned unchanged.
 */
export function applyDecision(
  rows: ApprovalRow[],
  id: number,
  decision: ApprovalDecision,
): ApprovalRow[] {
  const status = statusForDecision(decision)
  return rows.map((r) => (r.id === id ? { ...r, status } : r))
}

/** The still-pending rows (what the inbox shows). */
export function pendingRows(rows: ApprovalRow[]): ApprovalRow[] {
  return rows.filter((r) => r.status === 'pending')
}
