'use client'

import type { ReactElement } from 'react'
import {
  Bar,
  CartesianGrid,
  BarChart as RechartsBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from './ChartTooltip'
import { chartHex, type ChartColor } from './palette'

interface BarChartProps<T extends object> {
  data: readonly T[]
  index: keyof T & string
  category: keyof T & string
  color?: ChartColor
  valueFormatter?: (value: number) => string
  height?: number
  /**
   * Whether the value axis may invent fractional ticks. **Pass `false` for a count.**
   *
   * Recharts picks tick *values* from the domain without knowing the quantity is discrete,
   * so a histogram whose tallest bar is 3 gets ticks at 0 / 0.75 / 1.5 / 2.25 / 3 — and
   * "0.75 facts" is not a thing that can exist. It shipped that way on the memory
   * confidence histogram, where a 40px axis gutter clipped those labels down to `75`,
   * `1.5`, `25`, which reads as a broken chart rather than a wrong one.
   */
  allowDecimals?: boolean
  /** Formats the value-axis ticks. The tooltip keeps using `valueFormatter`. */
  axisFormatter?: (value: number) => string
}

/** A single-series bar chart with the console styling. */
export function BarChart<T extends object>({
  data,
  index,
  category,
  color = 'graph',
  valueFormatter,
  height = 200,
  allowDecimals = true,
  axisFormatter,
}: BarChartProps<T>): ReactElement {
  const hex = chartHex(color)
  return (
    <ResponsiveContainer width="100%" height={height}>
      <RechartsBarChart data={data as never} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
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
          allowDecimals={allowDecimals}
          tickFormatter={axisFormatter as never}
        />
        <Tooltip
          cursor={{ fill: 'var(--surface-2)' }}
          content={<ChartTooltip valueFormatter={valueFormatter} />}
        />
        <Bar
          dataKey={category as never}
          fill={hex}
          radius={[4, 4, 0, 0]}
          maxBarSize={42}
          isAnimationActive={false}
        />
      </RechartsBarChart>
    </ResponsiveContainer>
  )
}