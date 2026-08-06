/**
 * Pure domain math for the conformal-interval visualisation.
 *
 * Extracted from the React component so the auto-scaling logic is unit-testable
 * in a framework-free environment. The bug this fixes: the interval used to be
 * rendered against a hardcoded `[0, 1]` track, so for any target that is not a
 * probability (e.g. a 0–80h regression) the band collapsed against the right
 * edge. Here we derive the track domain from the actual data — the interval
 * bounds AND the point prediction — with a little padding so nothing sits flush
 * against the ends.
 */

/** Fraction of the data span to pad on each side of the track (12% default). */
export const DEFAULT_PAD_RATIO = 0.12

/**
 * Compute the visual track domain `[min, max]` for a conformal interval.
 *
 * The domain always contains the interval bounds AND the point prediction (so
 * the prediction dot is guaranteed to render inside the track even when it
 * falls outside the interval), padded by `padRatio` of the data span on each
 * side. Interval bounds are order-insensitive. A degenerate span (all three
 * values equal) yields a small symmetric window centred on the value so the
 * dot sits in the middle rather than dividing by zero.
 *
 * @param interval - Calibrated `[lo, hi]` bounds (any order).
 * @param prediction - The point prediction.
 * @param padRatio - Fractional padding per side; defaults to {@link DEFAULT_PAD_RATIO}.
 * @returns The `[min, max]` track domain, always with `min < max`.
 */
export function conformalDomain(
  interval: [number, number],
  prediction: number,
  padRatio: number = DEFAULT_PAD_RATIO,
): [number, number] {
  const lo = Math.min(interval[0], interval[1])
  const hi = Math.max(interval[0], interval[1])
  const dataMin = Math.min(lo, prediction)
  const dataMax = Math.max(hi, prediction)
  const span = dataMax - dataMin

  if (span === 0) {
    // All values coincide — build a small symmetric window so the dot centres.
    const pad = Math.max(Math.abs(dataMax) * padRatio, 1)
    return [dataMin - pad, dataMax + pad]
  }

  const pad = span * padRatio
  return [dataMin - pad, dataMax + pad]
}

/**
 * Project a value onto `0..100` (percent) within a domain, clamped to the
 * track. `left`/`width` positioning in the component is built from this.
 *
 * @param value - The value to place.
 * @param domain - The `[min, max]` track domain.
 * @returns Percent position in `[0, 100]`.
 */
export function toPercent(value: number, domain: [number, number]): number {
  const [min, max] = domain
  const span = max - min || 1
  return Math.min(100, Math.max(0, ((value - min) / span) * 100))
}

/**
 * Format a numeric target for a compact axis label. Larger magnitudes get
 * fewer decimals so `[0, 80]h` reads cleanly while probability-scale values
 * keep their precision. An optional unit is appended.
 *
 * @param value - The number to format.
 * @param unit - Optional unit suffix (e.g. `'h'`).
 * @returns The formatted label.
 */
export function formatValue(value: number, unit?: string): string {
  const abs = Math.abs(value)
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : 2
  return `${value.toFixed(decimals)}${unit ?? ''}`
}

/** Compact, human-readable labels for the conformal deferral signals. */
export interface ConformalSignals {
  /** e.g. `"width 0.29"` when an interval width is present, else `null`. */
  widthLabel: string | null
  /** e.g. `"singleton set"` / `"set size 2"` when a set size is present, else `null`. */
  setLabel: string | null
}

/**
 * Derive the display labels for the two conformal "deferral signals" — the
 * regression **interval width** and the classification **prediction-set size**.
 * Both are honest: a label is only produced when the underlying value is present
 * (non-null), so we never fabricate a signal the model did not emit.
 *
 * @param intervalWidth - Conformal interval width (upper − lower), or null.
 * @param setSize - Conformal prediction-set size, or null.
 * @param unit - Optional unit suffix for the width figure (e.g. `'h'`).
 */
export function conformalSignals(
  intervalWidth: number | null | undefined,
  setSize: number | null | undefined,
  unit?: string,
): ConformalSignals {
  const widthLabel =
    intervalWidth != null ? `width ${formatValue(intervalWidth, unit)}` : null
  const setLabel =
    setSize != null ? (setSize <= 1 ? 'singleton set' : `set size ${setSize}`) : null
  return { widthLabel, setLabel }
}
