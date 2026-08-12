/**
 * Pure derivations for the Overview surface. Kept recharts-free and
 * side-effect-free so every headline figure is provably a function of the real
 * `GET /metrics` payload. The React tiles only format + animate these values.
 *
 * The money math itself lives in `roi.ts`; this module adds the small
 * Overview-specific shaping (session trend, session delta, model-mix slices,
 * reduction percentage) the bento needs.
 */

import type { MetricsResponse } from '@/lib/api/types'

import { costReductionRatio } from './roi'

/** The measured `cost_saved_usd` history, oldest → newest, finite only. */
export function costSavedTrend(history: readonly MetricsResponse[]): number[] {
  return history.map((m) => m.cost_saved_usd).filter((v) => Number.isFinite(v))
}

/**
 * The gain in cumulative savings across the polled session window (latest minus
 * the first sample). Only a *positive*, real increase is surfaced as a delta —
 * a flat or decreasing window returns null so the hero never invents momentum.
 */
export function sessionSavedDelta(history: readonly MetricsResponse[]): number | null {
  const t = costSavedTrend(history)
  if (t.length < 2) return null
  const gain = t[t.length - 1] - t[0]
  return gain > 0 ? gain : null
}

/** One slice of the model-mix donut (signal-named colour, no recharts import). */
export interface ModelMixSlice {
  name: string
  value: number
  color: 'agent' | 'ml'
}

/**
 * Split the live `small_model_share` into the two donut slices. Returns an empty
 * array before any metric arrives so the tile can show an honest awaiting state.
 */
export function modelMixData(share: number | null): ModelMixSlice[] {
  if (share == null || !Number.isFinite(share)) return []
  const small = Math.round(Math.max(0, Math.min(1, share)) * 100)
  return [
    { name: 'Small model', value: small, color: 'agent' },
    { name: 'Large model', value: 100 - small, color: 'ml' },
  ]
}

/** Reduction versus the frontier baseline as a whole-number percentage, or null. */
export function reductionPct(
  baseline: number | null,
  costPer1k: number | null,
): number | null {
  if (baseline == null || costPer1k == null) return null
  return Math.round(costReductionRatio(baseline, costPer1k) * 100)
}

/** Format a 0..1 fraction as a whole-number percentage string ("74%"). */
export function pct(fraction: number | null): string {
  if (fraction == null || !Number.isFinite(fraction)) return '—'
  return `${Math.round(fraction * 100)}%`
}
