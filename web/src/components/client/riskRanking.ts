/**
 * Pure ranking + scoring logic for the client Risk Map.
 *
 * Side-effect free and chart-library-free so it is unit-testable in a plain Node
 * environment; `RiskMap.tsx` and `RiskLadder.tsx` only render what these
 * functions derive.
 *
 * The view is a **ranked ladder**, not a heat-map grid. With a handful of risks
 * a 5×5 matrix is 80% empty space and forces the reader to cross-reference a
 * pill against a card. A sorted list answers the page's actual question —
 * "which risk should worry me, and what did the control buy?" — in one pass:
 *
 *   • ordering + bar length  → inherent exposure (likelihood × impact), *before*
 *   • colour                 → residual band, *after* the mitigating control
 *
 * One colour, one meaning: residual. Length is the only other channel.
 */

import type { Signal } from '@/config/signals'
import type { RiskEntry } from '@/lib/api/types'

/** Residual risk band after the mitigating control. */
export type Residual = RiskEntry['residual']

/** The three signals a residual band maps onto (green / amber / red). */
export type ResidualSignal = Extract<Signal, 'ok' | 'risk' | 'block'>

interface ResidualMeta {
  /** Human label for the band. */
  label: string
  /** Trust-taxonomy signal that colours the band. */
  signal: ResidualSignal
  /** Ordering rank — higher is worse (used for worst-first sorts). */
  rank: number
}

/**
 * Residual band → its signal colour + label + severity rank. `low` is healthy
 * (green), `medium` awaits attention (amber), `high` is the danger band (red).
 */
export const RESIDUAL_META: Record<Residual, ResidualMeta> = {
  low: { label: 'Low', signal: 'ok', rank: 0 },
  medium: { label: 'Medium', signal: 'risk', rank: 1 },
  high: { label: 'High', signal: 'block', rank: 2 },
}

/** Residual bands worst-first — the order the ladder and the tally read in. */
export const RESIDUAL_ORDER: readonly Residual[] = ['high', 'medium', 'low']

/** Resolve a residual band to the signal hue that colours its bar. */
export function residualSignal(residual: Residual): ResidualSignal {
  return RESIDUAL_META[residual].signal
}

/** Inherent exposure before mitigation: likelihood × impact. */
export function exposureScore(risk: Pick<RiskEntry, 'likelihood' | 'impact'>): number {
  return risk.likelihood * risk.impact
}

/** The scale the backend publishes with the map (`likelihood` × `impact` bands). */
export interface RiskScale {
  likelihood: number[]
  impact: number[]
}

/**
 * Worst exposure the published scale allows (max likelihood × max impact) — the
 * denominator every bar is drawn against, so bar lengths are comparable and the
 * "of 25" in the key is derived from the response rather than hard-coded.
 * Falls back to the largest observed exposure when a scale arrives empty.
 */
export function maxExposure(scale: RiskScale, risks: RiskEntry[] = []): number {
  const maxL = Math.max(0, ...scale.likelihood)
  const maxI = Math.max(0, ...scale.impact)
  const fromScale = maxL * maxI
  const fromRisks = Math.max(0, ...risks.map(exposureScore))
  return Math.max(fromScale, fromRisks, 1)
}

/** One rung of the ladder: a risk with its derived exposure and bar length. */
export interface RankedRisk {
  risk: RiskEntry
  /** Inherent exposure, likelihood × impact. */
  exposure: number
  /** Exposure as a percentage of the scale's worst cell — the bar length, 0..100. */
  pct: number
  /** 1-based position in the ranking. */
  rank: number
}

/**
 * Rank the risks the way a reader needs them: **worst residual first** (what is
 * still carried after the control), then highest inherent exposure, then id for
 * a stable order. Bars therefore descend within each residual band, and the
 * colour boundaries in the list line up with the residual tally above it.
 */
export function rankRisks(scale: RiskScale, risks: RiskEntry[]): RankedRisk[] {
  const denominator = maxExposure(scale, risks)
  return [...risks]
    .sort(
      (a, b) =>
        RESIDUAL_META[b.residual].rank - RESIDUAL_META[a.residual].rank ||
        exposureScore(b) - exposureScore(a) ||
        a.id.localeCompare(b.id),
    )
    .map((risk, index) => {
      const exposure = exposureScore(risk)
      return {
        risk,
        exposure,
        pct: Math.max(0, Math.min(100, (exposure / denominator) * 100)),
        rank: index + 1,
      }
    })
}

/** Count of risks in each residual band (always all three keys present). */
export function residualCounts(risks: RiskEntry[]): Record<Residual, number> {
  const out: Record<Residual, number> = { low: 0, medium: 0, high: 0 }
  for (const r of risks) out[r.residual] += 1
  return out
}
