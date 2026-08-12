/**
 * Pure derivation of what a {@link import('./Gauge').Gauge} displays — clamped
 * fraction, whole percentage, and centre read-out. Kept in its own module (free
 * of the recharts import) so the value logic is unit-testable without pulling
 * the chart library into the test graph.
 */
export function gaugeDisplay(
  value: number,
  centerLabel?: string,
): { clamped: number; pct: number; readout: string } {
  const clamped = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
  const pct = Math.round(clamped * 100)
  return { clamped, pct, readout: centerLabel ?? `${pct}%` }
}
