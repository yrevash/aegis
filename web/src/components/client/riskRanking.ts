/**
 * Pure movement + ranking logic for the client Risk Map.
 *
 * Side-effect free and chart-library-free so it is unit-testable in a plain Node
 * environment; `RiskMap.tsx` and `RiskDumbbell.tsx` only render what these
 * functions derive.
 *
 * The page answers exactly one question — **"how much has Aegis reduced my
 * risk?"** — so every function here is about the *distance between two points*,
 * not about a single score. Each risk arrives with an inherent coordinate
 * (`likelihood × impact`, before any control) and a residual coordinate
 * (`residual_likelihood × residual_impact`, after the real control named in
 * `control_ref`). The gap between them is the product story.
 *
 * Two encodings, and only two:
 *
 *   • **position + distance** → exposure before, exposure after, and the move.
 *   • **colour**              → the residual band, i.e. what is still carried.
 *
 * Colour has one meaning on this page. The "before" mark is deliberately
 * achromatic — it is where you started, not a second severity scale.
 *
 * History worth not repeating: v1 was a 5×5 matrix (20 of 25 cells empty, two
 * competing colour scales); v2 was a ranked ladder that encoded before and after
 * as *separate* channels and so never showed the movement between them. This
 * module exists to make the movement first-class.
 */

import type { Signal } from '@/config/signals'
import type { RiskEntry } from '@/lib/api/types'

/** Residual risk band after the mitigating control (derived server-side). */
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

/** Residual bands worst-first — the order the tally reads in. */
export const RESIDUAL_ORDER: readonly Residual[] = ['high', 'medium', 'low']

/** Resolve a residual band to the signal hue that colours its mark. */
export function residualSignal(residual: Residual): ResidualSignal {
  return RESIDUAL_META[residual].signal
}

/** Exposure before any control: inherent likelihood × impact. */
export function inherentExposure(risk: Pick<RiskEntry, 'likelihood' | 'impact'>): number {
  return risk.likelihood * risk.impact
}

/** Exposure left after the control: residual likelihood × impact. */
export function residualExposure(
  risk: Pick<RiskEntry, 'residual_likelihood' | 'residual_impact'>,
): number {
  return risk.residual_likelihood * risk.residual_impact
}

/** The scale the backend publishes with the map (`likelihood` × `impact` bands). */
export interface RiskScale {
  likelihood: number[]
  impact: number[]
}

/**
 * Worst exposure the published scale allows (max likelihood × max impact) — the
 * shared axis every row is drawn against, so distances are comparable across
 * rows and the "of 25" in the key is derived from the response rather than
 * hard-coded. Falls back to the largest observed exposure when a scale arrives
 * empty.
 */
export function maxExposure(scale: RiskScale, risks: RiskEntry[] = []): number {
  const maxL = Math.max(0, ...scale.likelihood)
  const maxI = Math.max(0, ...scale.impact)
  const fromScale = maxL * maxI
  const fromRisks = Math.max(0, ...risks.map(inherentExposure))
  return Math.max(fromScale, fromRisks, 1)
}

/** One risk as a *move*: where it started, where it ended, and how far it came. */
export interface RiskMovement {
  risk: RiskEntry
  /** Exposure before the control (likelihood × impact). */
  inherent: number
  /** Exposure after the control (residual likelihood × impact). */
  residual: number
  /** Exposure points the control removed — `inherent − residual`, never negative. */
  removed: number
  /** Share of the inherent exposure removed, 0..100 (the row's "−67%"). */
  removedPct: number
  /** Inherent position along the shared axis, 0..100 — the hollow "before" mark. */
  inherentPos: number
  /** Residual position along the shared axis, 0..100 — the filled "after" mark. */
  residualPos: number
}

/** Clamp a raw percentage into 0..100 so a stray value can never overflow a track. */
function clampPct(n: number): number {
  return Math.max(0, Math.min(100, n))
}

/**
 * Turn each risk into its movement along a shared 0..`maxExposure` axis, ordered
 * **largest reduction first** (tie: the larger residual first, so the thing still
 * worth worrying about outranks an equal-sized win; then id for stability).
 *
 * Reduction-first ordering is the point of the page: the rows form a wedge of
 * long arrows narrowing to short ones, which *is* the answer to "how much did
 * you reduce my risk?". It does not hide the honest part — the shortest arrows
 * sit at the bottom with their residual dots furthest right, exactly where an
 * unsolved problem like prompt injection belongs.
 */
export function rankByReduction(scale: RiskScale, risks: RiskEntry[]): RiskMovement[] {
  const axis = maxExposure(scale, risks)
  return risks
    .map((risk): RiskMovement => {
      const inherent = inherentExposure(risk)
      const residual = residualExposure(risk)
      const removed = Math.max(0, inherent - residual)
      return {
        risk,
        inherent,
        residual,
        removed,
        removedPct: inherent > 0 ? clampPct((removed / inherent) * 100) : 0,
        inherentPos: clampPct((inherent / axis) * 100),
        residualPos: clampPct((residual / axis) * 100),
      }
    })
    .sort(
      (a, b) =>
        b.removed - a.removed ||
        b.residual - a.residual ||
        a.risk.id.localeCompare(b.risk.id),
    )
}

/** The one number a client repeats to their boss. */
export interface RiskTotals {
  /** Total exposure across every risk with no Aegis control in the way. */
  inherent: number
  /** Total exposure left after the controls. */
  residual: number
  /** Exposure points removed — `inherent − residual`. */
  removed: number
  /** Share of total exposure removed, 0..100. */
  removedPct: number
  /** How many risks actually moved (an unmoved risk would be an honest zero). */
  moved: number
  /** How many risks are on the map. */
  total: number
}

/**
 * Aggregate the whole map into the headline: total exposure before, total after,
 * and the percentage removed. Summing likelihood × impact is a crude aggregate
 * and is labelled as such in the UI — but it is *derived from the same numbers
 * every row shows*, so a reader can audit the headline against the rows.
 */
export function riskTotals(risks: RiskEntry[]): RiskTotals {
  let inherent = 0
  let residual = 0
  let moved = 0
  for (const r of risks) {
    const before = inherentExposure(r)
    const after = residualExposure(r)
    inherent += before
    residual += after
    if (after < before) moved += 1
  }
  const removed = Math.max(0, inherent - residual)
  return {
    inherent,
    residual,
    removed,
    removedPct: inherent > 0 ? clampPct((removed / inherent) * 100) : 0,
    moved,
    total: risks.length,
  }
}

/** Count of risks in each residual band (always all three keys present). */
export function residualCounts(risks: RiskEntry[]): Record<Residual, number> {
  const out: Record<Residual, number> = { low: 0, medium: 0, high: 0 }
  for (const r of risks) out[r.residual] += 1
  return out
}

/**
 * Residual *exposure* summed per band — how the leftover total splits between
 * green and amber. This is what lets the headline bar show, in the same colour
 * scale the rows use, that the remainder is not uniformly harmless: the amber
 * slice is prompt injection and friends, and it stays visible at the top of the
 * page rather than being averaged away.
 */
export function residualByBand(risks: RiskEntry[]): Record<Residual, number> {
  const out: Record<Residual, number> = { low: 0, medium: 0, high: 0 }
  for (const r of risks) out[r.residual] += residualExposure(r)
  return out
}
