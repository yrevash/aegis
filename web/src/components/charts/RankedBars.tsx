'use client'

import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'

import { chartHex, type ChartColor } from './palette'

/** One ranked row: a named thing and its magnitude. */
export interface RankedDatum {
  name: string
  value: number
}

interface RankedBarsProps {
  /** Rows in any order — the component sorts them descending. */
  data: readonly RankedDatum[]
  /** Format the value for its direct label. */
  valueFormatter?: (value: number) => string
  /** Ramp step for the bars. One hue; every row is the same colour. */
  color?: ChartColor
  /** Cap the visible rows; the remainder folds into a stated "other" row. */
  maxRows?: number
  /** What the rows are, for the screen reader's list. */
  label: string
}

/**
 * A ranked magnitude list — the form that replaced four categorical donuts.
 *
 * **Why not a pie.** Every one of these questions is *which is biggest, and by how
 * much*: which model costs most, which role spends most, which deployment carries
 * the most roles. A pie answers that badly — humans compare angles far worse than
 * they compare aligned lengths — and it answers it at a price this design system
 * cannot pay: N slices need N distinguishable hues, so the page was cycling a
 * six-colour array that mixed blue ramp steps with all three reserved status hues.
 * A green slice on a spend chart reads as a verdict on the spend, and DESIGN.md §2
 * reserves those hues precisely so it can't.
 *
 * Sorted bars need no palette at all. Identity comes from **position and label** —
 * DESIGN.md's stated first resort — so every bar is one hue and the encoding that
 * matters, length, is left doing the work alone. Each row carries its own value,
 * which also satisfies the direct-labelling relief the palette validator asks for
 * on the lighter ramp steps.
 *
 * Colour never follows rank: a filter that removes the top row does not repaint
 * the survivors, because they were never painted differently.
 */
export function RankedBars({
  data,
  valueFormatter = (v) => String(v),
  color = 'graph',
  maxRows = 6,
  label,
}: RankedBarsProps): ReactElement {
  const sorted = [...data].sort((a, b) => b.value - a.value)
  const shown = sorted.slice(0, maxRows)
  const rest = sorted.slice(maxRows)
  const restTotal = rest.reduce((sum, d) => sum + d.value, 0)
  const rows = restTotal > 0 ? [...shown, { name: `${rest.length} others`, value: restTotal }] : shown
  const max = rows.reduce((m, d) => Math.max(m, d.value), 0)
  const hex = chartHex(color)

  return (
    <ul className="flex flex-col gap-2.5" aria-label={label}>
      {rows.map((d) => (
        <li key={d.name} className="flex flex-col gap-1">
          <div className="flex items-baseline justify-between gap-3">
            {/* Model ids and deployment names are identifiers — auto-translate
                must not rewrite them. */}
            <span
              className="min-w-0 truncate text-[0.8rem] text-foreground"
              title={d.name}
              translate="no"
            >
              {d.name}
            </span>
            <Figure className="shrink-0 text-[0.8rem] leading-5 font-medium">
              {valueFormatter(d.value)}
            </Figure>
          </div>
          {/* The bar restates the figure printed beside it, so it is decorative. */}
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden>
            <div
              className="h-full rounded-full"
              style={{
                width: `${max > 0 ? Math.max(1.5, (d.value / max) * 100) : 0}%`,
                background: hex,
              }}
            />
          </div>
        </li>
      ))}
    </ul>
  )
}
