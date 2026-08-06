/**
 * Pure placement + scoring logic for the client Risk Map (§ value + assurance).
 *
 * Side-effect free and recharts-free so it is unit-testable in a plain Node
 * environment (see `riskMatrix.test.ts`); `RiskMap.tsx` only renders what these
 * functions derive. The matrix reads a likelihood(1..5) × impact(1..5) scale and
 * plots each risk into its cell, so the exposure is legible at a glance —
 * top-right (high likelihood + high impact) is the worst corner.
 */

import type { Signal } from '@/config/signals'
import type { RiskEntry } from '@/types/api'

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

/** Resolve a residual band to the signal hue that colours its plotted marker. */
export function residualSignal(residual: Residual): ResidualSignal {
  return RESIDUAL_META[residual].signal
}

/** Inherent-exposure band for a matrix cell. */
export type ExposureBand = 'low' | 'medium' | 'high'

/**
 * Inherent exposure of a cell from its likelihood × impact product (1..25).
 * This tints the *grid* by where a risk sits before mitigation; the plotted
 * marker's colour (residual) shows what is left after the control. Thresholds:
 * ≤ 6 low, 7..14 medium, ≥ 15 high — a clean diagonal from calm to hot.
 */
export function exposureBand(likelihood: number, impact: number): ExposureBand {
  const product = likelihood * impact
  if (product >= 15) return 'high'
  if (product >= 7) return 'medium'
  return 'low'
}

/** Exposure score for ordering: likelihood × impact. */
export function exposureScore(risk: Pick<RiskEntry, 'likelihood' | 'impact'>): number {
  return risk.likelihood * risk.impact
}

/** One cell of the matrix and the risks that plot into it. */
export interface MatrixCell {
  likelihood: number
  impact: number
  band: ExposureBand
  /** Risks whose (likelihood, impact) land in this cell. */
  risks: RiskEntry[]
  /** True for the worst corner (max likelihood + max impact). */
  worstCorner: boolean
}

/** The derived matrix ready to render, plus any risks off the scale. */
export interface RiskMatrix {
  /** Likelihood axis, ascending — columns left → right. */
  likelihoods: number[]
  /** Impact axis, ascending — rows bottom → top. */
  impacts: number[]
  /**
   * Rows top → bottom (impact high → low); each row is cells left → right by
   * ascending likelihood. Renders directly as grid rows.
   */
  rows: MatrixCell[][]
  /** Risks whose bands fall outside the scale — surfaced honestly, never dropped. */
  unplaced: RiskEntry[]
}

/**
 * Place each risk into its (likelihood, impact) cell over the given scale.
 * Rows are ordered impact-descending so the rendered grid has the worst corner
 * (high likelihood + high impact) at the top-right. Risks outside the scale are
 * collected in `unplaced` rather than silently dropped.
 */
export function buildMatrix(
  scale: { likelihood: number[]; impact: number[] },
  risks: RiskEntry[],
): RiskMatrix {
  const likelihoods = [...scale.likelihood].sort((a, b) => a - b)
  const impacts = [...scale.impact].sort((a, b) => a - b)
  const lset = new Set(likelihoods)
  const iset = new Set(impacts)
  const maxL = likelihoods[likelihoods.length - 1]
  const maxI = impacts[impacts.length - 1]

  const unplaced = risks.filter((r) => !lset.has(r.likelihood) || !iset.has(r.impact))

  const rows = [...impacts]
    .sort((a, b) => b - a)
    .map((impact) =>
      likelihoods.map((likelihood): MatrixCell => ({
        likelihood,
        impact,
        band: exposureBand(likelihood, impact),
        risks: risks.filter((r) => r.likelihood === likelihood && r.impact === impact),
        worstCorner: likelihood === maxL && impact === maxI,
      })),
    )

  return { likelihoods, impacts, rows, unplaced }
}

/**
 * Risks worst-first: highest exposure (likelihood × impact) first, ties broken
 * by residual severity, then id for a stable order.
 */
export function worstFirst(risks: RiskEntry[]): RiskEntry[] {
  return [...risks].sort(
    (a, b) =>
      exposureScore(b) - exposureScore(a) ||
      RESIDUAL_META[b.residual].rank - RESIDUAL_META[a.residual].rank ||
      a.id.localeCompare(b.id),
  )
}

/** Count of risks in each residual band (always all three keys present). */
export function residualCounts(risks: RiskEntry[]): Record<Residual, number> {
  const out: Record<Residual, number> = { low: 0, medium: 0, high: 0 }
  for (const r of risks) out[r.residual] += 1
  return out
}
