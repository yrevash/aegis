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

/** One measured point of the cost-per-1k trend, from a polled `/metrics` sample. */
export interface CostTrendPoint {
  t: string
  cost: number
}

/**
 * Build the **measured** cost-per-1k trend from the polled `/metrics` history —
 * genuine in-session data (like the Vite `useMetricsSeries`), not a fabricated
 * fixture. Newest sample is `T-0`, older ones count back. Non-finite samples are
 * dropped so a transient blip never distorts the line.
 */
export function costTrendSeries(history: readonly MetricsResponse[]): CostTrendPoint[] {
  const costs = history
    .map((m) => m.cost_per_1k_queries_usd)
    .filter((v) => Number.isFinite(v))
  return costs.map((cost, i) => ({
    t: `T-${costs.length - 1 - i}`,
    cost: Number(cost.toFixed(2)),
  }))
}

/** One measured point of query volume — calls served between two polls. */
export interface QueryVolumePoint {
  t: string
  calls: number
}

/**
 * Derive per-interval query volume from the polled `total_calls` history: the
 * difference in the cumulative call count between consecutive samples — the real
 * throughput measured over each poll window. Needs at least two samples to yield
 * a point; a negative delta (a backend restart reset the counter) is clamped to 0
 * rather than inventing a spike.
 */
export function queryVolumeSeries(history: readonly MetricsResponse[]): QueryVolumePoint[] {
  const totals = history.map((m) => m.total_calls).filter((v) => Number.isFinite(v))
  const points: QueryVolumePoint[] = []
  for (let i = 1; i < totals.length; i += 1) {
    points.push({ t: `T-${totals.length - 1 - i}`, calls: Math.max(0, totals[i] - totals[i - 1]) })
  }
  return points
}

/**
 * One slice of the model-mix donut (signal-named colour, no recharts import).
 *
 * The pair is two steps of the one blue ramp — `graph` (#1570ef) against `ml`
 * (#60a5fa). It used to be `agent` (#0b3b8f) against `ml`, which
 * `scripts/validate_palette.js` fails on the categorical lightness band: at
 * L 0.38 the deepest ramp step is darker than a categorical slot may be, and two
 * slices sitting either side of the band read as ink and tint rather than as two
 * of a kind. `graph` sits inside the band and keeps ΔE 15.2 against `ml` under
 * deuteranopia, so the pair passes on separation as well.
 */
export interface ModelMixSlice {
  name: string
  value: number
  color: 'graph' | 'ml'
}

/**
 * Split the live `small_model_share` into the two donut slices. Returns an empty
 * array before any metric arrives so the tile can show an honest awaiting state.
 */
export function modelMixData(share: number | null): ModelMixSlice[] {
  if (share == null || !Number.isFinite(share)) return []
  const small = Math.round(Math.max(0, Math.min(1, share)) * 100)
  return [
    { name: 'Small model', value: small, color: 'graph' },
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
