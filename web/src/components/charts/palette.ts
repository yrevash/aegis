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
