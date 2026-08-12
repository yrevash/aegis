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
 * A single-series gradient area chart. Tremor-flavoured API (data/index/category)
 * over Recharts, which is Tremor's own charting engine.
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
  const gradientId = `area-${String(category)}`
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsAreaChart data={data as never} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={hex} stopOpacity={0.45} />
            <stop offset="100%" stopColor={hex} stopOpacity={0} />
          </linearGradient>
        </defs>
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
          fill={`url(#${gradientId})`}
          dot={false}
          activeDot={{ r: 4, fill: hex, stroke: 'var(--background)' }}
          isAnimationActive
        />
      </RechartsAreaChart>
    </ResponsiveContainer>
  )
}