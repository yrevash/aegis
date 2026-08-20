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

// ── which mark a board deserves ─────────────────────────────────────────────

/**
 * Whether a board's x axis is time.
 *
 * Read off the **values**, not the column name, so `day`, `bucket` and `started_at`
 * behave the same and a categorical column that happens to be called `date` does not
 * get an axis it has not earned. One row is not a series, so it is never temporal.
 */
export function isTemporal(rows: readonly ChartRow[]): boolean {
  return rows.length > 1 && rows.every((row) => !Number.isNaN(Date.parse(row.label)))
}

/**
 * Whether a measure's parts sum to a whole — the question a donut silently asserts.
 *
 * A donut says *these are the pieces of one thing*. Counts are; averages, rates and
 * shares are not: `AVG(latency)` across four outcomes has no total, and drawing it as
 * four slices of a circle claims one. The catalogue's metric names carry the answer
 * (`avg_latency_ms`, `block_rate_avg`, `avg_decision_seconds_m`), which is why they are
 * read here rather than guessed from the numbers — three values that happen to sum to
 * something are not evidence that the sum means anything.
 *
 * Wrong in only one direction on purpose: an unrecognised name is treated as additive
 * only when nothing in it announces otherwise, and every non-additive metric this
 * catalogue defines announces itself.
 */
export function additiveMeasure(name: string): boolean {
  return !/(^|_)(avg|mean|median|rate|share|pct|percent|ratio|p50|p95|p99)(_|$)|latency|_ms(_|$)/i.test(
    name,
  )
}

/**
 * Whether several measures can honestly share one axis.
 *
 * DESIGN.md §2 forbids a dual axis outright, so the alternative to a shared axis is
 * **two charts**, not two scales. A shared axis is only honest when the measures are
 * within reach of each other: `block_rate_avg` (0.82) beside `redteam_runs_total` (9)
 * draws the rate as a line on the floor, which is a chart that hides its own data.
 *
 * The threshold is 25× — wide enough that `runs_total` (903) and `blocks_total` (32)
 * still share an axis, where the small series is legible at ~3.5% of the tall one, and
 * narrow enough that a rate beside a count does not.
 */
export function comparableScale(rows: readonly ChartRow[], series: readonly string[]): boolean {
  // A count and a mean are not the same *unit*, whatever their magnitudes: putting
  // `gates_total` and `avg_decision_seconds_m` on one axis invites the reader to
  // compare a number of approvals against a number of seconds. Units first, then size.
  const additive = series.map(additiveMeasure)
  if (additive.some((one) => one !== additive[0])) return false

  const peaks = series.map((key) =>
    rows.reduce((max, row) => Math.max(max, Math.abs(Number(row[key]) || 0)), 0),
  )
  const tallest = Math.max(...peaks)
  const shortest = Math.min(...peaks)
  if (tallest === 0) return true // nothing to hide behind anything else
  if (shortest === 0) return false // one series would be invisible against the other
  return tallest / shortest <= 25
}

/** The mark a board is drawn with, chosen from the rows rather than from its id. */
export type BoardForm =
  /** A trend over a time axis: one measure as an area, several as lines. */
  | { kind: 'trend'; series: string[] }
  /** Part-to-whole: a few additive categories that really do sum to something. */
  | { kind: 'donut'; series: string[] }
  /** A category bar chart with a quantitative axis — one measure or several, grouped. */
  | { kind: 'bars'; series: string[] }
  /** Measures that cannot share an axis: one small chart each, form chosen per measure. */
  | { kind: 'multiples'; series: string[] }
  /** One row of data. A number with its label, never a chart of a single bar. */
  | { kind: 'figure'; series: string[] }

/** Above this many categories a circle is unreadable and a bar chart is right. */
const DONUT_MAX = 6

/**
 * Which mark this board's rows deserve.
 *
 * The gallery used to draw **every** board as the same label-and-progress-bar list,
 * which threw away the time axis on seven boards, buried every second measure behind a
 * disclosure, and made a page of twenty cards read as one component repeated twenty
 * times. Two verdicts on that screen said the same thing, so the rule now is: **a
 * quantitative axis or a circle, never a filled track with the number printed beside
 * it.** Form follows the data's job, and the job is readable off the rows:
 *
 * | shape | mark | why |
 * |---|---|---|
 * | one row | a figure | a single value has no shape; one bar is a chart pretending |
 * | time axis, one measure | area | the axis carries the meaning |
 * | time axis, several measures, one unit | lines | compared at every bucket, never stacked — `blocks_total` is a *subset* of `runs_total`, and a stack would add it to itself |
 * | categories, one additive measure, ≤6 | donut | a part-to-whole story reads instantly as one |
 * | categories, one measure, >6 or non-additive | bar chart | a real axis a value can be read off |
 * | categories, several measures, one unit and scale | grouped bars | the pair is the question |
 * | measures that cannot share a scale | small multiples | rather than a forbidden second axis |
 *
 * @param rows - The board's drawable rows, from {@link chartRows}.
 * @param series - The board's declared measures, in catalogue order.
 */
export function boardForm(rows: readonly ChartRow[], series: readonly string[]): BoardForm {
  const measures = series.length > 0 ? [...series] : ['value']

  // One row is a number, not a shape. A "chart" of a single bar is the same defect as
  // a progress bar wearing an axis, and a figure is what the rest of the console uses
  // for one measured value.
  if (rows.length <= 1) return { kind: 'figure', series: measures }

  if (isTemporal(rows)) {
    if (measures.length === 1) return { kind: 'trend', series: measures }
    return comparableScale(rows, measures)
      ? { kind: 'trend', series: measures }
      : { kind: 'multiples', series: measures }
  }

  if (measures.length > 1) {
    return comparableScale(rows, measures)
      ? { kind: 'bars', series: measures }
      : { kind: 'multiples', series: measures }
  }

  // A donut also has to be *drawable*: a slice worth 0 is invisible, so a measure with
  // an empty category renders as a circle that silently omits it. `blocks_total by
  // status` is the live case — 32 blocked and three zeros — and as a donut it reads as
  // "everything was blocked". Bars show a zero as a zero.
  const drawable = rows.every((row) => Number(row[measures[0]]) > 0)
  if (rows.length <= DONUT_MAX && drawable && additiveMeasure(measures[0])) {
    return { kind: 'donut', series: measures }
  }
  return { kind: 'bars', series: measures }
}

/**
 * The mark for **one** measure of a board — the rule {@link boardForm} applies when a
 * card has to draw several measures that cannot share an axis.
 *
 * Exported so small multiples pick each panel's form by the same rule the whole card
 * would have used, rather than defaulting every panel to a bar.
 */
export function measureForm(rows: readonly ChartRow[], measure: string): BoardForm {
  return boardForm(rows, [measure])
}

/** One heading on the gallery: a dimension, and every board broken down by it. */
export interface BoardGroup {
  /** The x column the boards share, verbatim from the server. Empty for the tail. */
  dimension: string
  boards: AnalyticsBoard[]
}

/**
 * Split the catalogue into sections, one per dimension the boards are grouped by.
 *
 * Twenty cards in one flat grid is a wall, and the reader has no way to tell that
 * *Spend by model* and *Token volume by model* are the same cut of the same data while
 * *Runs by outcome* is a different one. The board's own `x` column is that fact, and it
 * arrives from the server — so the sections are **read off the data**, not off a
 * hand-kept list of themes that a board added next month would fall out of.
 *
 * **A dimension with one board is not a section.** Five headings each followed by a
 * single card in a three-column grid is two thirds white space and reads as a fault;
 * every such board is folded into one trailing group whose `dimension` is empty, and
 * each card still names its own cut in its caption. So nothing is hidden — only the
 * heading is dropped, and only where the heading said nothing the card did not.
 *
 * Ordered by size, largest first, then by name, with the folded tail last.
 * Deliberately **not** ordered by "is this column temporal": that cannot be known
 * before the rows arrive, and a gallery that reorders itself as each query lands is
 * worse than one whose order is arbitrary but stable. Within a section the catalogue's
 * own order is kept.
 *
 * @param boards - The chart-backed boards, in catalogue order.
 */
export function groupByDimension(boards: readonly AnalyticsBoard[]): BoardGroup[] {
  const sections = new Map<string, AnalyticsBoard[]>()
  for (const board of boards) {
    const key = board.x || 'no dimension'
    const bucket = sections.get(key)
    if (bucket) bucket.push(board)
    else sections.set(key, [board])
  }
  const named = [...sections.entries()]
    .map(([dimension, grouped]) => ({ dimension, boards: grouped }))
    .sort((a, b) => b.boards.length - a.boards.length || a.dimension.localeCompare(b.dimension))

  const shared = named.filter((group) => group.boards.length > 1)
  // The tail has no shared dimension to order it by, so it keeps the catalogue's own
  // order — the same promise every other section makes about its contents.
  const lonely = new Set(
    named.filter((group) => group.boards.length === 1).map((group) => group.boards[0].id),
  )
  const alone = boards.filter((board) => lonely.has(board.id))
  return alone.length > 0 ? [...shared, { dimension: '', boards: alone }] : shared
}

/** Compact number formatting for a chart axis and a table cell. */
export function formatValue(value: number): string {
  if (!Number.isFinite(value)) return '—'
  if (Math.abs(value) >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (Math.abs(value) >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  if (Number.isInteger(value)) return String(value)
  return value.toFixed(2)
}
