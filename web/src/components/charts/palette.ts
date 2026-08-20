/**
 * Chart colour resolution — one hue, light to dark.
 *
 * Charts name their series by signal ('agent', 'graph', …) so a visualisation
 * cannot invent a hue. But a *series* needs something the signal map cannot
 * give it: separation. The three subject signals are near-identical there on
 * purpose (DESIGN.md §2 — subjects are told apart by label, not colour), and two
 * lines drawn in `--blue-700` and `--blue-800` are one line.
 *
 * So a series resolves to a **step on the sequential ramp**, spaced far enough
 * apart to read. Diverging and status series keep the reserved status hues,
 * which are the only non-blue colours in the system.
 */

import { SIGNALS, type Signal } from '@/config/signals'

/** A chart series colour, named by its subsystem signal. */
export type ChartColor = Signal

/**
 * The sequential ramp, spaced for legibility as marks rather than as text.
 * Values mirror `--blue-*` in `app/globals.css`.
 */
const SERIES: Partial<Record<ChartColor, string>> = {
  ml: '#60a5fa', // --blue-400 — the light step
  graph: '#1570ef', // --blue-600 — the mid step
  agent: '#0b3b8f', // --blue-900 — the dark step
}

/**
 * Resolve a chart colour name to its hex value.
 *
 * @param color - The signal the series belongs to.
 * @returns A hex string: a ramp step for a subject, the reserved hue for a state.
 */
export function chartHex(color: ChartColor): string {
  return SERIES[color] ?? SIGNALS[color].hex
}

/**
 * The **ordinal ramp** — one hue, light → dark, for a chart whose categories are
 * ranked rather than merely different: the slices of a spend donut, the bands of
 * a stacked area, the segments of a share bar.
 *
 * Four steps, and four is the ceiling, because the ceiling was measured rather
 * than chosen. `node scripts/validate_palette.js "#60a5fa,#1570ef,#175cd3,#0b3b8f"
 * --ordinal --mode light --surface "#ffffff"` reports ALL CHECKS PASS — monotone
 * lightness, every adjacent ΔL ≥ 0.06, the pale end at 2.54:1 against a white
 * card, hue spread 6°. Adding `--blue-200 #bfdbfe` fails the light end at
 * **1.42:1**, and adding `--blue-800 #1e40af` collapses the gap to `--blue-900`
 * to **ΔL 0.045**. So a fifth category is never a fifth colour: it is folded into
 * an "Other" band, which is also the honest thing to do with a long tail.
 *
 * Read dark → light by rank: the biggest share is the deepest step, so the eye
 * finds the leader without consulting the legend.
 */
export const ORDINAL_RAMP = ['#0b3b8f', '#175cd3', '#1570ef', '#60a5fa'] as const

/** The most categories the ordinal ramp can carry before an "Other" band is required. */
export const ORDINAL_MAX = ORDINAL_RAMP.length

/**
 * The step for rank `i` of `n` ranked categories, darkest first.
 *
 * With fewer than four categories the ramp is *sampled*, not truncated, so two
 * bands never come out as two neighbouring steps a reader cannot separate: n=2
 * resolves to `#0b3b8f`/`#60a5fa` and n=3 to `#0b3b8f`/`#1570ef`/`#60a5fa`, both
 * of which the validator passes on their own.
 */
export function rampHex(i: number, n: number): string {
  if (n <= 1) return ORDINAL_RAMP[0]
  const span = Math.min(n, ORDINAL_MAX)
  const idx = Math.round((Math.min(i, span - 1) * (ORDINAL_MAX - 1)) / (span - 1))
  return ORDINAL_RAMP[idx]
}
