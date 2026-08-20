'use client'

import type { ReactElement } from 'react'
import {
  Area,
  CartesianGrid,
  AreaChart as RechartsAreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from './ChartTooltip'
import { chartHex, type ChartColor } from './palette'

interface AreaChartProps<T extends object> {
  data: readonly T[]
  /** Key for the x-axis category. */
  index: keyof T & string
  /** One series (single value key). */
  category: keyof T & string
  /** Series colour by signal name. */
  color?: ChartColor
  valueFormatter?: (value: number) => string
  height?: number
}

/**
 * A single-series area chart. Tremor-flavoured API (data/index/category) over
 * Recharts, which is Tremor's own charting engine.
 *
 * The fill was a vertical `linearGradient` fading to transparent; it is now a
 * flat wash at a fixed opacity. Two reasons, and neither is taste. DESIGN.md §4
 * has no gradients in the system, and more usefully a fading fill encodes a
 * second dimension that does not exist — the area under a line means the same
 * thing at the bottom of the plot as it does at the top. The forecast band in
 * `HorizonChart` is drawn as a flat fill for exactly this reason, so the two now
 * read as the same kind of mark.
 *
 * Nothing animates on mount: `MOTION_INTENSITY 2` spends motion on confirming a
 * state change, and a line drawing itself in confirms nothing.
 */
export function AreaChart<T extends object>({
  data,
  index,
  category,
  color = 'agent',
  valueFormatter,
  height = 200,
}: AreaChartProps<T>): ReactElement {
  const hex = chartHex(color)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsAreaChart data={data as never} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
        <XAxis
          dataKey={index as never}
          tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
        />
        <YAxis
          tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
          axisLine={false}
          tickLine={false}
          width={40}
        />
        <Tooltip
          cursor={{ stroke: 'var(--border)' }}
          content={<ChartTooltip valueFormatter={valueFormatter} />}
        />
        <Area
          type="monotone"
          dataKey={category as never}
          stroke={hex}
          strokeWidth={2}
          fill={hex}
          fillOpacity={0.14}
          dot={false}
          activeDot={{ r: 5, fill: hex, stroke: 'var(--card)', strokeWidth: 2 }}
          isAnimationActive={false}
        />
      </RechartsAreaChart>
    </ResponsiveContainer>
  )
}