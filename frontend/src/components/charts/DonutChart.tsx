import type { ReactElement } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { ChartTooltip } from './ChartTooltip'
import { chartHex, type ChartColor } from './palette'

/** One slice of the donut. */
export interface DonutDatum {
  name: string
  value: number
  color: ChartColor
}

interface DonutChartProps {
  data: DonutDatum[]
  /** Text rendered in the centre (e.g. a total or headline share). */
  centerLabel?: string
  centerSub?: string
  valueFormatter?: (value: number) => string
  height?: number
}

/** A donut chart with an optional centred read-out. */
export function DonutChart({
  data,
  centerLabel,
  centerSub,
  valueFormatter,
  height = 200,
}: DonutChartProps): ReactElement {
  return (
    <div className="relative" style={{ height }}>
      <ResponsiveContainer width="100%" height="100%">
        <PieChart>
          <Tooltip content={<ChartTooltip valueFormatter={valueFormatter} />} />
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            innerRadius="62%"
            outerRadius="88%"
            paddingAngle={2}
            stroke="var(--background)"
            strokeWidth={2}
          >
            {data.map((d) => (
              <Cell key={d.name} fill={chartHex(d.color)} />
            ))}
          </Pie>
        </PieChart>
      </ResponsiveContainer>
      {centerLabel && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
          <span className="tabular font-display text-2xl font-semibold text-foreground">
            {centerLabel}
          </span>
          {centerSub && <span className="eyebrow mt-0.5">{centerSub}</span>}
        </div>
      )}
    </div>
  )
}
