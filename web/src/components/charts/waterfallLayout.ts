/**
 * Pure domain math for the SHAP waterfall visualisation.
 *
 * A SHAP explanation is additive: `prediction ≈ base + Σ contribution`. The
 * waterfall walks that sum one driver at a time, so each feature's bar picks up
 * where the previous one left off. Kept framework-free so the cumulative
 * positioning is unit-testable without a DOM.
 *
 * Colour semantics are by **direction**, not good/bad: a driver that *raises*
 * the prediction is one hue, one that *lowers* it is another. The consuming
 * component labels which direction is desirable for the target.
 */

import type { ShapFeature } from '@/lib/stream'

/** One driver's segment in the cumulative walk from base to prediction. */
export interface WaterfallStep {
  feature: string
  /** The feature's raw value (categorical features report 1). */
  value: number
  /** Signed SHAP attribution. */
  contribution: number
  /** Cumulative prediction *before* this driver is applied. */
  start: number
  /** Cumulative prediction *after* this driver is applied. */
  end: number
  /** Whether this driver raises (`true`) or lowers (`false`) the prediction. */
  raises: boolean
}

/** The full base → drivers → prediction walk, plus the track domain. */
export interface Waterfall {
  base: number
  steps: WaterfallStep[]
  /** The SHAP-reconstructed prediction (`base + Σ contribution`). */
  reconstructed: number
  /** `[min, max]` cumulative value across the walk, for scaling the track. */
  domain: [number, number]
}

/**
 * Build the cumulative SHAP waterfall from a base value and signed features.
 *
 * Drivers are ordered by descending absolute contribution so the biggest movers
 * read first. Each step records the cumulative prediction before and after it,
 * and the domain spans every cumulative point so the track never clips.
 *
 * @param base - The model's base/expected value.
 * @param features - Signed SHAP attributions (any order).
 * @returns The ordered walk, the reconstructed prediction, and the track domain.
 */
export function buildWaterfall(base: number, features: ShapFeature[]): Waterfall {
  const sorted = [...features].sort(
    (a, b) => Math.abs(b.contribution) - Math.abs(a.contribution),
  )

  let cumulative = base
  let min = base
  let max = base

  const steps: WaterfallStep[] = sorted.map((f) => {
    const start = cumulative
    cumulative += f.contribution
    min = Math.min(min, start, cumulative)
    max = Math.max(max, start, cumulative)
    return {
      feature: f.feature,
      value: f.value,
      contribution: f.contribution,
      start,
      end: cumulative,
      raises: f.contribution > 0,
    }
  })

  return { base, steps, reconstructed: cumulative, domain: [min, max] }
}

/**
 * Project a value onto `0..100` (percent) within the waterfall's track domain,
 * clamped to the track. Positioning of each bar segment is built from this.
 *
 * @param value - The value to place.
 * @param domain - The `[min, max]` track domain from {@link buildWaterfall}.
 * @returns Percent position in `[0, 100]`.
 */
export function waterfallPercent(value: number, domain: [number, number]): number {
  const [lo, hi] = domain
  const span = hi - lo || 1
  return Math.min(100, Math.max(0, ((value - lo) / span) * 100))
}
