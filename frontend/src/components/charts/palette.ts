/**
 * Chart colour resolution. Charts reference signal names ('agent', 'graph', …)
 * so every visualisation stays inside the one coherent trust palette rather
 * than introducing new hues.
 */

import { SIGNALS, type Signal } from '@/config/signals'

/** A chart series colour, named by its subsystem signal. */
export type ChartColor = Signal

/** Resolve a chart colour name to its hex value. */
export function chartHex(color: ChartColor): string {
  return SIGNALS[color].hex
}
