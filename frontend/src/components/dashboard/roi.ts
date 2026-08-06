/**
 * ROI math — the pure, deterministic core behind the live "$ saved" panel.
 *
 * Every figure here is derived from the *real* `GET /metrics` fields
 * (`cost_per_1k_queries_usd`, `baseline_cost_usd`, `cost_saved_usd`). Where a
 * projection needs a business input the backend cannot know — a labour rate, a
 * handling time, a monthly query volume — that input lives in
 * {@link SAMPLE_ASSUMPTIONS} and is surfaced in the UI as a clearly-labelled
 * "sample assumption", never dressed up as a measured number.
 *
 * Keeping this a side-effect-free module means the arithmetic is unit-testable
 * in isolation (see `roi.test.ts`); the React panel only formats and animates.
 */

/** One thousand queries — the unit the backend reports cost in. */
export const PER_THOUSAND = 1_000
/** One million queries — the "cost at scale" projection bucket. */
export const PER_MILLION = 1_000_000

/**
 * Business inputs the telemetry cannot measure. These are illustrative and are
 * always rendered behind a "sample assumptions" note — swap per engagement.
 */
export interface RoiAssumptions {
  /** Illustrative queries handled per month (drives the $/month projection). */
  monthlyVolume: number
  /** Illustrative minutes a human takes to resolve one case unaided. */
  manualMinutes: number
  /** Illustrative fully-loaded labour rate, USD per hour. */
  laborRateUsdPerHour: number
  /** Illustrative agent wall-clock per case, seconds (for the time contrast). */
  agentSeconds: number
}

/** Default illustrative assumptions. Domain-neutral, obviously round numbers. */
export const SAMPLE_ASSUMPTIONS: RoiAssumptions = {
  monthlyVolume: 250_000,
  manualMinutes: 25,
  laborRateUsdPerHour: 45,
  agentSeconds: 8,
}

// ── Measured derivations (real metric fields only) ──────────────────────────

/** Real: unit cost of a single query, from the measured cost per 1k. */
export function costPerQueryUsd(costPer1kUsd: number): number {
  return costPer1kUsd / PER_THOUSAND
}

/** Real: cost of `queries` at the measured blended rate. */
export function costAtVolumeUsd(costPer1kUsd: number, queries: number): number {
  return costPer1kUsd * (queries / PER_THOUSAND)
}

/**
 * Real: measured saving per 1k queries versus the frontier-model baseline —
 * both operands come straight from `GET /metrics`.
 */
export function savedPer1kUsd(baselineCostUsd: number, costPer1kUsd: number): number {
  return baselineCostUsd - costPer1kUsd
}

/** Real: projected saving across `queries`, scaled from the measured per-1k gap. */
export function savedAtVolumeUsd(
  baselineCostUsd: number,
  costPer1kUsd: number,
  queries: number,
): number {
  return savedPer1kUsd(baselineCostUsd, costPer1kUsd) * (queries / PER_THOUSAND)
}

/**
 * Real: cost reduction versus baseline as a fraction in `[0, 1]`. Guards a
 * zero/negative baseline so the meter never divides by zero or overshoots.
 */
export function costReductionRatio(baselineCostUsd: number, costPer1kUsd: number): number {
  if (baselineCostUsd <= 0) return 0
  const ratio = savedPer1kUsd(baselineCostUsd, costPer1kUsd) / baselineCostUsd
  return Math.max(0, Math.min(1, ratio))
}

// ── Manual-vs-agent (mixed: real unit cost, sample labour inputs) ───────────

/** Sample: labour cost of resolving one case manually = minutes/60 × rate. */
export function manualCostPerCaseUsd(a: RoiAssumptions): number {
  return (a.manualMinutes / 60) * a.laborRateUsdPerHour
}

/**
 * Mixed: how many times cheaper the agent is per case — sample manual labour
 * cost over the *real* agent unit cost. Guards a zero unit cost.
 */
export function manualVsAgentMultiple(costPer1kUsd: number, a: RoiAssumptions): number {
  const agent = costPerQueryUsd(costPer1kUsd)
  if (agent <= 0) return 0
  return manualCostPerCaseUsd(a) / agent
}

/**
 * Mixed: net saving per case — sample manual labour cost minus the real agent
 * unit cost.
 */
export function savedPerCaseUsd(costPer1kUsd: number, a: RoiAssumptions): number {
  return manualCostPerCaseUsd(a) - costPerQueryUsd(costPer1kUsd)
}

// ── Formatting (pure) ───────────────────────────────────────────────────────

/** Format a USD amount with fixed fraction digits (default 2). */
export function formatUsd(value: number, fractionDigits = 2): string {
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  })
}

/** Format a large USD amount compactly (e.g. `$1.2M`, `$3.9K`). */
export function formatUsdCompact(value: number): string {
  return value.toLocaleString('en-US', {
    style: 'currency',
    currency: 'USD',
    notation: 'compact',
    maximumFractionDigits: 1,
  })
}

/** Format an integer count compactly (e.g. `250K`, `1M`). */
export function formatCountCompact(value: number): string {
  return value.toLocaleString('en-US', { notation: 'compact', maximumFractionDigits: 1 })
}
