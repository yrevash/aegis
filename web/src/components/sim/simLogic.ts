/**
 * Pure decision logic for the Access demo, kept free of React and recharts so it
 * can be unit-tested. The view maps these outcomes to ✓ / ✗ / ● / — markers;
 * here we only decide them from live state.
 */

import type { CapabilityStatus } from '@/components/shared'
import type { RunState } from '@/state/runReducer'

/** Comparison-cell marker: allow ✓ · deny ✗ · gate ● · none —. */
export type Mark = 'allow' | 'deny' | 'gate' | 'none'

/**
 * Refund-action outcome for a lane, derived only from its tool results/calls —
 * never fabricated. Denied results win, then executed, then a bare proposal.
 */
export function toolMark(state: RunState): { mark: Mark; label: string } {
  if (state.toolResults.some((r) => !r.ok)) return { mark: 'deny', label: 'Refund denied' }
  if (state.toolResults.some((r) => r.ok)) return { mark: 'allow', label: 'Refund executed' }
  if (state.toolCalls.length > 0) return { mark: 'gate', label: 'Refund proposed' }
  return { mark: 'none', label: '—' }
}

/** Whether both lanes have started and neither is still running. */
export function isSettled(ops: RunState, cli: RunState): boolean {
  const started = ops.events.length > 0 || cli.events.length > 0
  return started && !ops.running && !cli.running
}

/** Live status of the human-gate control across both lanes. */
export function gateStatus(ops: RunState): CapabilityStatus {
  if (ops.approval) return 'pending'
  if (ops.awaitedApproval) return 'live'
  return 'idle'
}
