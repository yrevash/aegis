'use client'

import { useMemo, useState, type ReactElement } from 'react'
import {
  Area,
  CartesianGrid,
  AreaChart as RechartsAreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { Figure } from '@/components/primitives/Figure'

import { ChartTooltip } from './ChartTooltip'
import { ORDINAL_MAX, rampHex } from './palette'

/** The figure a legend entry prints — its explicit summary, else its last value. */
function legendValue(
  s: StackSeries,
  latest: Record<string, string | number> | null,
): number | null {
  if (s.value != null && Number.isFinite(s.value)) return s.value
  const last = latest?.[s.key]
  return typeof last === 'number' ? last : null
}

/** One band of the stack: the key it reads out of each row, and what to call it. */
export interface StackSeries {
  key: string
  label: string
  /**
   * The figure printed beside the band's swatch. Defaults to the band's value in
   * the last row, which is the wrong summary for most stacks: a 30-day card whose
   * legend reads yesterday's spend invites the reader to compare it against a
   * y-axis scaled to the whole window. Pass the window total instead.
   */
  value?: number
}

interface StackedAreaProps {
  /** Rows in chronological order. Each row carries the index key and every series key. */
  data: ReadonlyArray<Record<string, string | number>>
  /** The x-axis key (a formatted bucket label). */
  index: string
  /** Bands, **largest first** — the ramp is read dark → light by rank. */
  series: readonly StackSeries[]
  valueFormatter?: (value: number) => string
  height?: number
  /** Ticks to keep on the x-axis; the rest are dropped rather than rotated. */
  xTickCount?: number
}

/**
 * A stacked area chart over the one blue ramp.
 *
 * Three rules from DESIGN.md §2 are load-bearing here and none of them is taste.
 *
 * **The ramp is ordinal, not categorical.** Bands of a stack are ranked — the
 * biggest contributor is a fact about the data, not a label — so the encoding is
 * one hue light→dark, deepest at the bottom of the stack where the largest band
 * sits. `rampHex` carries the validated steps and caps at {@link ORDINAL_MAX};
 * a caller with more categories folds the tail into an "Other" band rather than
 * asking for a fifth colour that does not exist.
 *
 * **Identity is never colour alone.** Every band is named in the legend beside
 * its swatch, and at four bands or fewer the legend also carries each band's
 * latest value — so the chart is still readable by someone who cannot separate
 * two steps of one blue, and readable at a glance by someone who can.
 *
 * **The hover layer is a crosshair, not a nearest-point guess.** A stacked area
 * read at a single x is the only way to compare bands, so the cursor is a full
 * vertical rule and the tooltip lists every band at that bucket.
 *
 * Nothing animates: a stack that grows in on mount confirms no state change, and
 * `prefers-reduced-motion` should not need a special case to be honoured.
 */
export function StackedArea({
  data,
  index,
  series,
  valueFormatter = (v) => String(v),
  height = 260,
  xTickCount = 6,
}: StackedAreaProps): ReactElement {
  const [focused, setFocused] = useState<string | null>(null)

  const colors = useMemo(
    () => new Map(series.map((s, i) => [s.key, rampHex(i, series.length)])),
    [series],
  )

  // Keep a readable number of x ticks by sampling, never by rotating labels:
  // a rotated axis costs vertical space and legibility in equal measure.
  const ticks = useMemo(() => {
    if (data.length <= xTickCount) return undefined
    const stride = Math.ceil(data.length / xTickCount)
    return data.filter((_, i) => i % stride === 0).map((row) => String(row[index]))
  }, [data, index, xTickCount])

  const latest = data.length > 0 ? data[data.length - 1] : null

  return (
    <div className="flex flex-col gap-3">
      <ResponsiveContainer width="100%" height={height}>
        <RechartsAreaChart data={data as never} margin={{ top: 8, right: 22, left: -12, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey={index}
            ticks={ticks}
            interval={0}
            tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            minTickGap={8}
          />
          <YAxis
            tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={(v: number) => valueFormatter(v)}
          />
          <Tooltip
            cursor={{ stroke: 'var(--blue-600)', strokeWidth: 1, strokeDasharray: '4 4' }}
            content={<ChartTooltip valueFormatter={valueFormatter} />}
          />
          {series.map((s) => (
            <Area
              key={s.key}
              type="monotone"
              dataKey={s.key}
              name={s.label}
              stackId="stack"
              stroke="var(--card)"
              strokeWidth={1}
              fill={colors.get(s.key)}
              fillOpacity={focused != null && focused !== s.key ? 0.22 : 0.92}
              isAnimationActive={false}
            />
          ))}
        </RechartsAreaChart>
      </ResponsiveContainer>

      <ul className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
        {series.map((s) => (
          <li key={s.key}>
            <button
              type="button"
              className="flex items-center gap-1.5 rounded-sm px-0.5 text-[0.72rem] focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
              onMouseEnter={() => setFocused(s.key)}
              onMouseLeave={() => setFocused(null)}
              onFocus={() => setFocused(s.key)}
              onBlur={() => setFocused(null)}
            >
              <span
                className="size-2 shrink-0 rounded-[2px]"
                style={{ background: colors.get(s.key) }}
                aria-hidden
              />
              <span className="text-muted-foreground">{s.label}</span>
              {series.length <= ORDINAL_MAX && legendValue(s, latest) != null && (
                <Figure className="text-[0.72rem] leading-4 font-medium text-foreground">
                  {valueFormatter(legendValue(s, latest) as number)}
                </Figure>
              )}
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}
