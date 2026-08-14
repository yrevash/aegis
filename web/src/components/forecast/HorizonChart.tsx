'use client'

import type { ReactElement } from 'react'
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

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { chartHex } from '@/components/charts/palette'
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
  height = 260,
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
          tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
          minTickGap={28}
        />
        <YAxis
          tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
          width={46}
        />
        <Tooltip
          cursor={{ stroke: 'var(--border)' }}
          content={<ChartTooltip valueFormatter={valueFormatter} />}
        />
        {/* The band is a two-valued range; excluded from the tooltip, which reads
            one number per row. Its bounds are shown beside the selected step. */}
        <Area
          dataKey="band"
          stroke="none"
          fill={ml}
          fillOpacity={0.18}
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
