'use client'

import type { ReactElement } from 'react'
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'

import { Figure } from '@/components/primitives/Figure'

import { ChartTooltip } from './ChartTooltip'
import { chartHex, type ChartColor } from './palette'

/** The step a slice is painted in — its explicit override, else its signal. */
const sliceHex = (d: DonutDatum): string => d.hex ?? chartHex(d.color)

/** One slice of the donut. */
export interface DonutDatum {
  name: string
  value: number
  color: ChartColor
  /**
   * An explicit step, overriding `color`.
   *
   * A signal name resolves to one of three ramp steps, which is the right answer
   * when the slices *mean* something (small model versus large). It is the wrong
   * answer when they are simply ranked — four models by spend have no signal
   * between them, only an order — and there is no signal named "third biggest".
   * Pass `rampHex(i, n)` from `./palette` there; it is the same validated ramp,
   * addressed by rank instead of by meaning.
   */
  hex?: string
}

interface DonutChartProps {
  data: DonutDatum[]
  /** Text rendered in the centre (e.g. a total or headline share). */
  centerLabel?: string
  centerSub?: string
  valueFormatter?: (value: number) => string
  height?: number
}

/**
 * A donut chart with a centred read-out and a **legend that carries the values**.
 *
 * The legend is not decoration here, it is the required relief. `scripts/validate_palette.js`
 * warns that the light ramp step `#60a5fa` sits at 2.48:1 against the page surface,
 * below the 3:1 mark floor, and that warning is not dismissable — it obligates
 * visible labels or a table view. So every slice is named and its value printed
 * beside its swatch, which means the chart is readable with the colour ignored
 * entirely: a reader who cannot separate two steps of one blue ramp still gets
 * both numbers, and a hover is an enhancement rather than the only way in.
 */
export function DonutChart({
  data,
  centerLabel,
  centerSub,
  valueFormatter = (v) => String(v),
  height = 200,
}: DonutChartProps): ReactElement {
  return (
    <div className="flex flex-col gap-3">
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
              stroke="var(--card)"
              strokeWidth={2}
              isAnimationActive={false}
            >
              {data.map((d) => (
                <Cell key={d.name} fill={sliceHex(d)} />
              ))}
            </Pie>
          </PieChart>
        </ResponsiveContainer>
        {centerLabel && (
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <Figure size="stat">{centerLabel}</Figure>
            {centerSub && <span className="eyebrow mt-0.5">{centerSub}</span>}
          </div>
        )}
      </div>

      {data.length > 1 && (
        <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
          {data.map((d) => (
            <li key={d.name} className="flex items-center gap-1.5 text-[0.72rem]">
              <span
                className="size-2 shrink-0 rounded-[2px]"
                style={{ background: sliceHex(d) }}
                aria-hidden
              />
              <span className="text-muted-foreground">{d.name}</span>
              <Figure className="text-[0.72rem] leading-4 font-medium text-foreground">
                {valueFormatter(d.value)}
              </Figure>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
