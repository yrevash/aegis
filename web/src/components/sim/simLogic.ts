/**
 * Pure decision logic for the Access demo, kept free of React and recharts so it
 * can be unit-tested. The view maps these outcomes to ✓ / ✗ / ● / — markers;
 * here we only decide them from live state.
 */

import type { CapabilityStatus } from '@/components/shared'
import type { RunState } from '@/state/runReducer'
import type { RiskLevel, ScoredSource, ToolCall } from '@/lib/stream'

/** Comparison-cell marker: allow ✓ · deny ✗ · gate ● · none —. */
export type Mark = 'allow' | 'deny' | 'gate' | 'none'

/** Risk, ordered. `high` is the call a run is judged on. */
const RISK_RANK: Record<RiskLevel, number> = { low: 0, medium: 1, high: 2 }

/**
 * The one call a lane's outcome is about: the **highest-risk** call it proposed,
 * last one winning a tie.
 *
 * The outcome row used to read `toolCalls[0]`, which is whatever the run happened to
 * reach for first. On the seeded question that is `find_requests` — a *read* — so a
 * row headed by a write reported on a read, and it reported it as denied because the
 * old rule took `toolResults.some(r => !r.ok)`: **any** failed result anywhere in the
 * run marked **the** named call denied, even a result belonging to a different call.
 */
export function consequentialCall(state: RunState): ToolCall | null {
  let best: ToolCall | null = null
  for (const call of state.toolCalls) {
    if (best === null || RISK_RANK[call.risk] >= RISK_RANK[best.risk]) best = call
  }
  return best
}

/**
 * Action outcome for a lane, derived only from its own consequential call — never
 * fabricated, and never borrowed from another call's result.
 *
 * The label names the call itself (whatever tool the run actually proposed) rather
 * than a fixed verb, so the cell cannot describe an action the lane did not take.
 */
export function toolMark(state: RunState): { mark: Mark; label: string } {
  const call = consequentialCall(state)
  if (call === null) return { mark: 'none', label: '—' }
  // Matched by `call_id`, so the verdict shown is this call's own.
  const result = state.toolResults.find((r) => r.call_id === call.call_id)
  if (result === undefined) return { mark: 'gate', label: `${call.tool} proposed` }
  return result.ok
    ? { mark: 'allow', label: `${call.tool} executed` }
    : { mark: 'deny', label: `${call.tool} denied` }
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

/** What the two ranked lists actually have to say about the two scopes. */
export interface RankedDivergence {
  /** Sources both lanes ranked. */
  shared: number
  /** Sources only the first lane ranked. */
  onlyA: number
  /** Sources only the second lane ranked. */
  onlyB: number
  /** True when both lanes ranked exactly the same set (and ranked something). */
  same: boolean
  /** The one sentence the panel prints above the two lists. */
  note: string
}

/**
 * Compare what the two roles were allowed to rank, and say so out loud.
 *
 * The panel is the demo's centrepiece and it asserted a difference it did not show:
 * two lists of the *identical six documents at identical scores*, under a table row
 * reading "Retrieval scope — differs". The scope difference is real, but it lives
 * **upstream of the rerank**, in how many candidates each retriever was allowed to
 * consider; by the time the reranker has cut to a top-k, the two roles' windows onto a
 * shared policy corpus can and do coincide. Evidence that contradicts its own headline
 * is worse than no evidence, so the panel now states which of the two it is holding,
 * measured from the ids rather than assumed.
 *
 * @param a - Sources the first lane ranked.
 * @param b - Sources the second lane ranked.
 * @param aCandidates - Candidates the first lane's retriever considered.
 * @param bCandidates - Candidates the second lane's retriever considered.
 * @param names - The two lane labels, for the sentence.
 */
export function rankedDivergence(
  a: readonly ScoredSource[],
  b: readonly ScoredSource[],
  aCandidates: number,
  bCandidates: number,
  names: readonly [string, string],
): RankedDivergence {
  const idsA = new Set(a.map((s) => s.id))
  const idsB = new Set(b.map((s) => s.id))
  let shared = 0
  for (const id of idsA) if (idsB.has(id)) shared += 1
  const onlyA = idsA.size - shared
  const onlyB = idsB.size - shared
  const same = onlyA === 0 && onlyB === 0 && shared > 0

  if (same) {
    const scope =
      aCandidates === bCandidates
        ? `Both retrievers considered ${aCandidates} candidates on this question`
        : `The scopes differ upstream of the rerank — ${aCandidates} candidates considered for ${names[0]}, ${bCandidates} for ${names[1]}`
    return {
      shared,
      onlyA,
      onlyB,
      same,
      note: `Both roles ranked the same ${shared} sources here. ${scope}; the top-ranked policy documents fall inside both.`,
    }
  }
  if (shared === 0 && onlyA === 0 && onlyB === 0) {
    return { shared, onlyA, onlyB, same, note: 'Neither lane has ranked a source yet.' }
  }
  const parts: string[] = []
  if (onlyA > 0) parts.push(`${onlyA} only ${names[0]} could rank`)
  if (onlyB > 0) parts.push(`${onlyB} only ${names[1]} could rank`)
  if (shared > 0) parts.push(`${shared} both could`)
  return { shared, onlyA, onlyB, same, note: `${parts.join(' · ')}.` }
}
