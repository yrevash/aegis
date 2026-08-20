'use client'

import { useMemo, type ReactElement } from 'react'
import {
  Bar,
  BarChart as RechartsBarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart as RechartsLineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { rampHex } from '@/components/charts/palette'

import type { ChartRow } from './analyticsBoard'

/**
 * The two marks the insight gallery needed and the shared chart kit does not carry.
 *
 * `charts/` owns the console's chart vocabulary and is not this lane's to edit, so the
 * two shapes missing from it live here, built on the same Recharts primitives, the same
 * `ChartTooltip`, and the same validated ramp — `rampHex` from `charts/palette`, whose
 * four steps `scripts/validate_palette.js` passes and whose fifth step it fails. A
 * fourth series is therefore never a fourth colour: {@link seriesHexes} caps at
 * {@link SERIES_MAX} and the caller folds the tail.
 *
 * **Why these are charts and not lists.** The gallery previously drew every board as a
 * label beside a filled track. A filled track has no axis: a reader can compare two
 * rows on it and cannot read a value off it, so a board of fourteen models became a
 * ranking with no quantities. Both marks here carry a quantitative axis, gridlines and
 * a hover layer, which is the whole difference.
 *
 * Nothing animates. DESIGN.md §6 spends motion on confirming a state change, and a bar
 * growing in on mount confirms nothing — so there is no `prefers-reduced-motion` branch
 * to get wrong.
 */

/** The axis, grid and tick treatment every chart on this page shares. */
const TICK = {
  fill: 'var(--muted-foreground)',
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
} as const

/**
 * The most **series** the ramp can carry — three, not four.
 *
 * Four is the ceiling for an *ordinal* use (donut slices, stack bands), where
 * `validate_palette.js --ordinal` reports ALL CHECKS PASS. Series in a legend are a
 * *categorical* use, and the same four run in categorical mode fail:
 *
 * ```
 * $ node scripts/validate_palette.js "#60a5fa,#1570ef,#175cd3,#0b3b8f" --mode light
 *   [FAIL] Normal-vision floor  worst adjacent #175cd3↔#1570ef ΔE 6.4 — below 15
 * $ node scripts/validate_palette.js "#0b3b8f,#1570ef,#60a5fa" --mode light
 *   [PASS] CVD separation       worst adjacent ΔE 15.2 (deutan) · tritan 11.8
 *   [PASS] Normal-vision floor  worst adjacent ΔE 15.6 (normal)
 * ```
 *
 * `rampHex` *samples* rather than truncates, so three resolves to the two ends and the
 * middle — the passing set above — and two resolves to the two ends. The remaining
 * `[FAIL] Lightness band` and `[WARN] Contrast vs surface` are properties of a
 * one-hue system rather than of this choice: the warning obliges relief, and every
 * chart here carries a legend and the full rows table beneath it.
 */
const SERIES_MAX = 3

/** One validated ramp step per series, darkest first, capped at {@link SERIES_MAX}. */
function seriesHexes(count: number): string[] {
  const n = Math.min(count, SERIES_MAX)
  return Array.from({ length: n }, (_, i) => rampHex(i, n))
}

/**
 * Trim a long category label without hiding which row it was.
 *
 * Kept to the **tail** of the identifier rather than the head: every model here is
 * `genailab-maas-<name>`, so the first fourteen characters are the fourteen characters
 * that are the same on every row. The full value is in the table under the chart and in
 * the hover.
 */
function shortLabel(label: string): string {
  if (label.length <= 18) return label
  return `…${label.slice(-17)}`
}

interface CategoryBarsProps {
  /** Rows in the order the server returned them; the chart sorts by the first measure. */
  rows: readonly ChartRow[]
  /** One measure, or several that share a unit and a scale — grouped side by side. */
  series: readonly string[]
  valueFormatter: (value: number) => string
  /** Shorter formatter for the axis gutter, when the full one is too wide. */
  axisFormatter?: (value: number) => string
  height?: number
  /** Rows to draw before the rest are folded into one named band. */
  maxRows?: number
}

/**
 * A horizontal bar chart over a category axis — with an axis, not a progress track.
 *
 * Horizontal because identity here comes from a **name**: fourteen model ids on a
 * vertical axis overlap into a smear at any card width, and aligned lengths beside
 * legible names is the mark that survives. The quantitative axis runs along the bottom
 * with gridlines, so a value can be read off the chart and not only out of a printed
 * number.
 *
 * **The tail is a band, not a fifth colour or a silent truncation.** Past `maxRows` the
 * remaining categories are summed into one `Other (n)` row, which is both the honest
 * treatment of a long tail and the reason the ramp never needs a step it does not have.
 * Every row is still in the table under the chart.
 */
export function CategoryBars({
  rows,
  series,
  valueFormatter,
  axisFormatter,
  height = 200,
  maxRows = 8,
}: CategoryBarsProps): ReactElement {
  const measures = series.slice(0, SERIES_MAX)
  const hexes = seriesHexes(measures.length)

  const data = useMemo(() => {
    const primary = measures[0]
    const sorted = [...rows].sort((a, b) => Number(b[primary]) - Number(a[primary]))
    if (sorted.length <= maxRows) {
      return sorted.map((row) => ({ ...row, label: shortLabel(row.label) }))
    }
    const head = sorted.slice(0, maxRows - 1).map((row) => ({ ...row, label: shortLabel(row.label) }))
    const tail = sorted.slice(maxRows - 1)
    const folded: ChartRow = { label: `Other (${tail.length})` }
    for (const measure of measures) {
      folded[measure] = tail.reduce((sum, row) => sum + (Number(row[measure]) || 0), 0)
    }
    return [...head, folded]
  }, [rows, measures, maxRows])

  // A horizontal bar needs vertical room per category, or the bars overlap their own
  // labels. Grow with the rows rather than squeezing them into a fixed box.
  const plot = Math.max(height, data.length * (measures.length > 1 ? 36 : 28) + 40)

  return (
    <ResponsiveContainer width="100%" height={plot}>
      <RechartsBarChart
        data={data as never}
        layout="vertical"
        margin={{ top: 4, right: 12, left: 0, bottom: 0 }}
        barCategoryGap="18%"
      >
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" horizontal={false} />
        <XAxis
          type="number"
          tick={TICK}
          axisLine={false}
          tickLine={false}
          tickFormatter={axisFormatter ?? valueFormatter}
        />
        <YAxis
          type="category"
          dataKey="label"
          tick={TICK}
          axisLine={false}
          tickLine={false}
          width={104}
          interval={0}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-2)' }}
          content={<ChartTooltip valueFormatter={valueFormatter} />}
        />
        {measures.length > 1 ? (
          <Legend
            verticalAlign="top"
            align="left"
            height={24}
            iconType="circle"
            iconSize={8}
            wrapperStyle={{ fontSize: 11, color: 'var(--muted-foreground)' }}
          />
        ) : null}
        {measures.map((measure, i) => (
          <Bar
            key={measure}
            dataKey={measure}
            name={measure}
            fill={hexes[i]}
            radius={[0, 3, 3, 0]}
            maxBarSize={measures.length > 1 ? 13 : 20}
          />
        ))}
      </RechartsBarChart>
    </ResponsiveContainer>
  )
}

interface TrendLinesProps {
  rows: readonly ChartRow[]
  /** Two or more measures sharing a unit and a scale — never a second axis. */
  series: readonly string[]
  valueFormatter: (value: number) => string
  axisFormatter?: (value: number) => string
  height?: number
}

/**
 * Several measures over one time axis, as lines.
 *
 * **Lines rather than a stack**, because these measures are not slices of each other's
 * total: `blocks_total` is a *subset* of `runs_total`, and stacking would draw the
 * blocked runs on top of a total that already contains them. A stack asserts addition;
 * only a caller who knows the parts are disjoint may use one.
 *
 * **One axis, always.** Where the measures cannot honestly share a scale, the caller
 * has already been sent to small multiples by `comparableScale` — there is no code path
 * here that could produce the second axis DESIGN.md §2 forbids.
 *
 * The cursor is a full vertical rule and the tooltip lists every series at that bucket,
 * because comparing series at one x is the only thing this mark is for.
 */
export function TrendLines({
  rows,
  series,
  valueFormatter,
  axisFormatter,
  height = 200,
}: TrendLinesProps): ReactElement {
  const measures = series.slice(0, SERIES_MAX)
  const hexes = seriesHexes(measures.length)
  const ticks = useMemo(() => {
    if (rows.length <= 6) return undefined
    const stride = Math.ceil(rows.length / 6)
    return rows.filter((_, i) => i % stride === 0).map((row) => row.label)
  }, [rows])

  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsLineChart
        data={rows as never}
        margin={{ top: 4, right: 8, left: -12, bottom: 0 }}
      >
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={TICK}
          axisLine={false}
          tickLine={false}
          ticks={ticks as never}
          tickFormatter={(value: string) => String(value).slice(0, 10)}
        />
        <YAxis
          tick={TICK}
          axisLine={false}
          tickLine={false}
          width={48}
          tickFormatter={axisFormatter ?? valueFormatter}
        />
        <Tooltip
          cursor={{ stroke: 'var(--border)', strokeWidth: 1 }}
          content={<ChartTooltip valueFormatter={valueFormatter} />}
        />
        <Legend
          verticalAlign="top"
          align="left"
          height={24}
          iconType="circle"
          iconSize={8}
          wrapperStyle={{ fontSize: 11, color: 'var(--muted-foreground)' }}
        />
        {measures.map((measure, i) => (
          <Line
            key={measure}
            type="monotone"
            dataKey={measure}
            name={measure}
            stroke={hexes[i]}
            strokeWidth={2}
            dot={false}
            activeDot={{ r: 3 }}
            isAnimationActive={false}
          />
        ))}
      </RechartsLineChart>
    </ResponsiveContainer>
  )
}
