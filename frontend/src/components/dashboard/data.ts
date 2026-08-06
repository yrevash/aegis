/**
 * Illustrative time series for the home dashboard charts. Deterministic and
 * self-contained so the enterprise home surface reads as populated in the demo;
 * a live backend would supply these from its metrics store.
 */

/** A point in the cost-efficiency trend. */
export interface TrendPoint {
  t: string
  cost: number
  quality: number
}

/** Hourly query throughput. */
export interface VolumePoint {
  hour: string
  queries: number
}

/** Twelve-interval cost-per-1k and quality trend (cost trending down). */
export const COST_TREND: TrendPoint[] = Array.from({ length: 12 }, (_, i) => ({
  t: `T-${11 - i}`,
  cost: Number((6.4 - i * 0.24 + Math.sin(i) * 0.15).toFixed(2)),
  quality: Number((0.88 + i * 0.006 + Math.cos(i) * 0.01).toFixed(3)),
}))

/** Query volume across a working day. */
export const QUERY_VOLUME: VolumePoint[] = [
  { hour: '08', queries: 210 },
  { hour: '10', queries: 480 },
  { hour: '12', queries: 620 },
  { hour: '14', queries: 720 },
  { hour: '16', queries: 540 },
  { hour: '18', queries: 300 },
]
