/**
 * Pure derivation + formatting logic for the client Savings view.
 *
 * Side-effect free and recharts-free so it is unit-testable in a plain Node
 * environment; `SavingsView.tsx` only renders what these functions derive. The
 * point is to *show the derivation* — the headline saving reconciled against the
 * itemised breakdown, not a bare number.
 */

import type { SavingsBreakdownRow, SavingsResponse } from '@/lib/api/types'

/** Signals cycled across breakdown sources so the chart stays in the taxonomy. */
const SOURCE_SIGNALS = ['ok', 'graph', 'agent', 'ml', 'risk', 'neutral'] as const

/** A colour a breakdown source can take. */
export type SourceSignal = (typeof SOURCE_SIGNALS)[number]

/** A chart-ready breakdown row: sorted, coloured, with its share of the total. */
export interface BreakdownDatum {
  source: string
  /** Saved USD, rounded to cents. */
  saved: number
  /** Share of the itemised breakdown total, 0..1. */
  share: number
  color: SourceSignal
  explanation: string
}

/** Sum of the itemised breakdown (USD). */
export function breakdownTotal(rows: SavingsBreakdownRow[]): number {
  return rows.reduce((sum, r) => sum + r.saved_usd, 0)
}

/**
 * Breakdown rows as chart data: largest saving first, each with its share of the
 * itemised total and a cycled signal colour. A zero/negative total yields zero
 * shares (never divides by zero).
 */
export function breakdownData(rows: SavingsBreakdownRow[]): BreakdownDatum[] {
  const sorted = [...rows].sort((a, b) => b.saved_usd - a.saved_usd)
  const total = sorted.reduce((sum, r) => sum + r.saved_usd, 0)
  return sorted.map((r, i) => ({
    source: r.source,
    saved: Number(r.saved_usd.toFixed(2)),
    share: total > 0 ? r.saved_usd / total : 0,
    color: SOURCE_SIGNALS[i % SOURCE_SIGNALS.length],
    explanation: r.explanation,
  }))
}

/**
 * Fraction saved vs baseline, guarded against a zero/negative baseline and
 * clamped at 0 so a (pathological) actual > baseline never shows a negative bar.
 */
export function savedFraction(baseline: number, actual: number): number {
  if (baseline <= 0) return 0
  return Math.max(0, (baseline - actual) / baseline)
}

/** Reconciliation of the headline saving against the itemised breakdown. */
export interface Reconciliation {
  /** The headline `saved_usd`. */
  headline: number
  /** Sum of the breakdown rows. */
  breakdown: number
  /** headline − breakdown (0 when they agree). */
  deltaUsd: number
  /** Whether the breakdown explains the headline within `toleranceUsd`. */
  reconciles: boolean
}

/**
 * Does the itemised breakdown add up to the headline saving? Proving this is the
 * whole "savings proper — show the derivation" requirement: the number is only
 * trustworthy when its parts sum to it.
 */
export function reconcile(saved: SavingsResponse, toleranceUsd = 1): Reconciliation {
  const breakdown = breakdownTotal(saved.breakdown)
  const deltaUsd = saved.saved_usd - breakdown
  return {
    headline: saved.saved_usd,
    breakdown,
    deltaUsd,
    reconciles: Math.abs(deltaUsd) <= toleranceUsd,
  }
}
