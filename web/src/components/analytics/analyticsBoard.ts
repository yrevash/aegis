/**
 * The analytics surface's rules, in a module with no JSX so they can be tested.
 *
 * Three of them earn their place here rather than living inside the component:
 *
 * 1. **Which honest state the page is in.** "Superset is off", "Superset is not
 *    answering", "there are no boards yet" and "here are your charts" are four
 *    different sentences, and picking the wrong one is how a page ends up telling an
 *    operator their tenant has no data when the real answer is that nobody started the
 *    server. {@link analyticsState} is that choice, made once.
 * 2. **Which colour a series gets.** Assigned from the board's own declared series
 *    order — the entity — never from the rank of whatever the query happened to
 *    return, so a filter that drops a series cannot repaint the survivors.
 * 3. **Which rows are drawable.** Superset returns whatever the dataset holds; a row
 *    with no x value has nowhere to sit on the axis and is dropped rather than drawn
 *    at `undefined`.
 */

import type { AnalyticsBoard, AnalyticsBoardData, AnalyticsStatus } from '@/lib/api/analytics'
import type { ChartColor } from '@/components/charts/palette'

/** The four states the page can be in. Exhaustive on purpose — there is no fallthrough. */
export type AnalyticsState = 'off' | 'unconfigured' | 'down' | 'empty' | 'ready'

/**
 * Which state `status` puts the page in.
 *
 * Ordered from most-fundamental outward, so a deployment that is off is never reported
 * as "Superset is not answering" — which would send an operator to restart a service
 * they were never running.
 */
export function analyticsState(status: AnalyticsStatus | null): AnalyticsState {
  if (status === null) return 'down'
  if (!status.enabled) return 'off'
  if (!status.configured) return 'unconfigured'
  if (!status.reachable) return 'down'
  if (status.boards === 0) return 'empty'
  return 'ready'
}

/**
 * The fixed categorical order. Assigned in sequence, never cycled.
 *
 * The order is **three steps of the one blue ramp, and nothing else**. It used to run
 * six deep — `risk`, `ok` and `block` filled slots four to six — which handed the
 * reserved status hues to whichever measure happened to be declared fourth. A spend
 * series drawn in the guardrail red is a series that reads as an alarm, and DESIGN.md §2
 * reserves those three hues so that reading is always the right one.
 *
 * Three is also all this page needs. Each series is drawn as its **own** chart under its
 * own caption — small multiples, not one plot — so nothing has to be told apart by hue
 * in the first place; the ramp step is there to keep a measure recognisable as you move
 * down the page. The steps are ordered by contrast against the card, brightest first, so
 * the pale `#60a5fa` (2.48:1, below the 3:1 mark floor `scripts/validate_palette.js`
 * enforces) is the last one reached and always sits beside its own caption and table.
 *
 * A fourth series does not get a generated hue — {@link seriesColor} folds everything
 * past the third onto `neutral`, which reads as "other" rather than as a new subsystem.
 * A board with seven measures is a board that should be two boards.
 */
export const SERIES_ORDER: readonly ChartColor[] = ['graph', 'agent', 'ml']

/**
 * The colour for one series of a board.
 *
 * Keyed on the series' position in the **board's** declared list, so the colour follows
 * the measure and not its rank in the current result set: filtering the result set down
 * to fewer rows never repaints the series that survive.
 *
 * @param board - The board being drawn.
 * @param series - One of `board.series`.
 */
export function seriesColor(board: AnalyticsBoard, series: string): ChartColor {
  const index = board.series.indexOf(series)
  if (index < 0 || index >= SERIES_ORDER.length) return 'neutral'
  return SERIES_ORDER[index]
}

/** One row, ready for a chart: a label on the x axis and a number per series. */
export interface ChartRow {
  label: string
  [series: string]: string | number
}

/**
 * Turn a board's rows into chart rows.
 *
 * Rows with no x value are dropped — there is nowhere on the axis to put them, and a
 * bar at `undefined` reads as a real category called "undefined". A series value that
 * is not a finite number becomes `0` **and** is reported through {@link countedRows},
 * so a chart never silently invents a zero the query did not return.
 */
export function chartRows(data: AnalyticsBoardData): ChartRow[] {
  const out: ChartRow[] = []
  for (const row of data.rows) {
    const raw = data.x ? row[data.x] : undefined
    if (raw === null || raw === undefined || raw === '') continue
    const next: ChartRow = { label: String(raw) }
    for (const series of data.series) {
      const value = Number(row[series])
      next[series] = Number.isFinite(value) ? value : 0
    }
    out.push(next)
  }
  return out
}

/** How many of `data.rows` survived {@link chartRows}, and how many did not. */
export function countedRows(data: AnalyticsBoardData): { drawn: number; dropped: number } {
  const drawn = chartRows(data).length
  return { drawn, dropped: data.rows.length - drawn }
}

/**
 * Whether this board can be shown as an embedded Superset dashboard right now.
 *
 * All three have to hold. `EMBEDDED_SUPERSET` is a Superset 6.1.0 feature flag on a
 * wheel that has already shipped broken paths, so the embed is treated as the thing
 * most likely to be missing — and its absence costs the iframe and nothing else.
 */
export function embedAvailable(board: AnalyticsBoard, status: AnalyticsStatus | null): boolean {
  if (status === null || !status.reachable || !status.embedEnabled) return false
  return board.kinds.includes('dashboard')
}

/** Whether Aegis can draw this board itself, from rows it fetched server-side. */
export function chartAvailable(board: AnalyticsBoard): boolean {
  return board.kinds.includes('chart')
}

/** Compact number formatting for a chart axis and a table cell. */
export function formatValue(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(2)
}
