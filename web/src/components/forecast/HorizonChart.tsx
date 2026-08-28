'use client'

import type { ReactElement, ReactNode } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { chartHex } from '@/components/charts/palette'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { ForecastResult } from '@/lib/api/types'

interface HorizonChartProps {
  result: ForecastResult
  /** How many trailing history points to draw before the forecast starts. */
  historyTail?: number
  height?: number
  valueFormatter?: (value: number) => string
}

interface Row {
  label: string
  observed: number | null
  /** [lo, hi] — a Recharts range Area, so the band is drawn between two bounds. */
  band: [number, number] | null
  forecast: number | null
}

/**
 * The hover read-out — and the reason this chart does not use the shared tooltip.
 *
 * On a projected step the number a reader takes away must be the **interval**, not
 * the centre line running through it. A generic one-number-per-series tooltip reads
 * out the point forecast and silently drops the band, which is the single way this
 * chart could still overclaim after being drawn correctly. So a forecast row shows
 * `lo – hi` first and marks the centre as a centre; an observed row shows the one
 * measured value, because that one *is* a point.
 */
function HorizonTooltip({
  active,
  payload,
  label,
  format,
}: {
  active?: boolean
  payload?: Array<{ payload?: Row }>
  label?: string | number
  format: (value: number) => string
}): ReactElement | null {
  const row = active ? payload?.[0]?.payload : undefined
  if (!row) return null
  const projected = row.band != null && row.forecast != null && row.observed == null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 shadow-pop">
      <p className="mb-1 font-mono text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
        {label}
      </p>
      {projected && row.band ? (
        <dl className="flex flex-col gap-0.5 text-xs">
          <div className="flex items-baseline gap-3">
            <dt className="text-muted-foreground">band</dt>
            <dd className="ml-auto">
              <Figure className="font-medium text-foreground">
                {format(row.band[0])} – {format(row.band[1])}
              </Figure>
            </dd>
          </div>
          <div className="flex items-baseline gap-3">
            <dt className="text-muted-foreground">centre</dt>
            <dd className="ml-auto">
              <Figure className="text-muted-foreground">{format(row.forecast!)}</Figure>
            </dd>
          </div>
        </dl>
      ) : row.observed != null ? (
        <div className="flex items-baseline gap-3 text-xs">
          <span className="text-muted-foreground">observed</span>
          <Figure className="ml-auto font-medium text-foreground">{format(row.observed)}</Figure>
        </div>
      ) : null}
    </div>
  )
}

/** Render a timestamp as a compact `dd MMM` axis label. */
function axisLabel(iso: string): string {
  const d = new Date(iso)
  return `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })}`
}

/**
 * The forecast as a time series: observed history, then the point forecast inside
 * its interval band, split by a marked "now" line.
 *
 * The band is drawn as a *range* area between `lo` and `hi` rather than as a
 * symmetric ± wrapper, because a conformal band is not required to be symmetric
 * about the point and faking symmetry would misdraw it. History and forecast are
 * separate series so the join is visible: the reader can always see where measured
 * data stops and projection starts, which a single continuous line hides.
 *
 * Nothing here labels the band's coverage — that number is measured, differs from
 * the requested level, and belongs next to the backtest that produced it.
 */
export function HorizonChart({
  result,
  historyTail = 45,
  height = 280,
  valueFormatter,
}: HorizonChartProps): ReactElement {
  const ml = chartHex('ml')
  const agent = chartHex('agent')

  const tail = result.history.slice(-historyTail)
  const rows: Row[] = tail.map((p) => ({
    label: axisLabel(p.ts),
    observed: p.value,
    band: null,
    forecast: null,
  }))

  // Repeat the last observed value as the forecast's step 0 so the two lines meet
  // instead of leaving a visual gap that reads as missing data.
  const lastObserved = tail.at(-1)
  if (lastObserved) {
    rows[rows.length - 1] = {
      ...rows[rows.length - 1],
      forecast: lastObserved.value,
      band: [lastObserved.value, lastObserved.value],
    }
  }

  for (const p of result.points) {
    rows.push({
      label: axisLabel(p.ts),
      observed: null,
      band: [p.lo, p.hi],
      forecast: p.point,
    })
  }

  const splitAt = lastObserved ? rows[tail.length - 1].label : undefined

  return (
    <ResponsiveContainer width="100%" height={height}>
      <ComposedChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{
            fill: 'var(--muted-foreground)',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
          }}
          axisLine={false}
          tickLine={false}
          minTickGap={28}
        />
        <YAxis
          tick={{
            fill: 'var(--muted-foreground)',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
          }}
          axisLine={false}
          tickLine={false}
          width={46}
        />
        <Tooltip
          cursor={{ stroke: 'var(--border)' }}
          content={<HorizonTooltip format={valueFormatter ?? ((v: number) => v.toFixed(2))} />}
        />
        {/* The band is a two-valued range, and the hover read-out above shows both
            of its bounds before it shows the centre line. */}
        <Area
          dataKey="band"
          stroke={ml}
          strokeOpacity={0.4}
          strokeWidth={1}
          fill={ml}
          fillOpacity={0.16}
          isAnimationActive={false}
          tooltipType="none"
          connectNulls
        />
        <Line
          type="monotone"
          name="observed"
          dataKey="observed"
          stroke={agent}
          strokeWidth={2}
          dot={false}
          isAnimationActive={false}
        />
        <Line
          type="monotone"
          name="forecast"
          dataKey="forecast"
          stroke={ml}
          strokeWidth={2}
          strokeDasharray="5 3"
          dot={false}
          isAnimationActive={false}
        />
        {splitAt ? (
          <ReferenceLine
            x={splitAt}
            stroke="var(--muted-foreground)"
            strokeDasharray="2 3"
            label={{
              value: 'last observed',
              position: 'insideTopLeft',
              fill: 'var(--muted-foreground)',
              fontSize: 10,
              fontFamily: 'var(--font-mono)',
            }}
          />
        ) : null}
      </ComposedChart>
    </ResponsiveContainer>
  )
}

/** One legend entry: a mark drawn the way the chart draws it, then its meaning. */
function Key({ mark, children }: { mark: ReactElement; children: ReactNode }): ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5">
      {mark}
      {children}
    </span>
  )
}

/**
 * The chart's key, plus the two facts a reader needs to size the band.
 *
 * This replaces a standalone panel that re-drew the terminal interval as a
 * horizontal bar — a second rendering of something the chart already shows.
 * The one fact that panel added, how wide the band gets at its widest, is folded
 * in here as text; the interval *method* stays stated because a parametric band
 * and a conformal band are not interchangeable, and the paragraph of provenance
 * prose that used to sit beside it now lives behind the ⓘ.
 */
export function HorizonLegend({
  result,
  valueFormatter,
}: {
  result: ForecastResult
  valueFormatter?: (value: number) => string
}): ReactElement {
  const ml = chartHex('ml')
  const agent = chartHex('agent')
  const fmt = valueFormatter ?? ((v: number) => v.toFixed(2))
  const conformal = result.interval_method === 'conformal'

  // The widest step is the honest answer to "how far can this drift by the end".
  const widest = result.points.reduce<(typeof result.points)[number] | null>(
    (worst, p) => (worst == null || p.hi - p.lo > worst.hi - worst.lo ? p : worst),
    null,
  )

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[0.7rem] text-muted-foreground">
      <Key mark={<span className="h-0.5 w-4 rounded-full" style={{ background: agent }} />}>
        observed
      </Key>
      <Key
        mark={
          <span
            className="h-0.5 w-4 rounded-full"
            style={{
              backgroundImage: `repeating-linear-gradient(90deg, ${ml} 0 5px, transparent 5px 8px)`,
            }}
          />
        }
      >
        <span className="font-mono text-foreground">{result.model}</span> forecast
      </Key>
      <Key
        mark={
          <span
            className="h-2.5 w-4 rounded-[3px]"
            style={{ background: `${ml}2b`, border: `1px solid ${ml}66` }}
          />
        }
      >
        <span className={conformal ? undefined : 'font-medium text-risk-ink'}>
          {result.requested_level * 100}% {result.interval_method} band
        </span>
        <InfoTip label="How this interval was built">
          {result.interval_method_detail}
          {conformal
            ? ' Calibration is chronological throughout: every band is fitted on data strictly earlier than the points it is scored on, so no future value ever reaches the calibration set.'
            : ' These bounds are the fitted model’s own predictive distribution. They hold only as far as its residual assumptions do — read the achieved coverage below, not this level.'}
        </InfoTip>
      </Key>
      {widest ? (
        <span className="tabular font-mono">
          widest {fmt(widest.hi - widest.lo)} at step {widest.step}
        </span>
      ) : null}
      <span className="tabular ml-auto font-mono">
        {result.history_points} × {result.freq} history · horizon {result.horizon}
      </span>
    </div>
  )
}
