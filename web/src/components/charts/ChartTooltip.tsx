'use client'

import type { ReactElement } from 'react'

/**
 * One entry Recharts passes to a custom tooltip.
 *
 * `value` is a pair when the series is a range `Area` — a forecast band, drawn
 * between its own `lo` and `hi`. That case is the reason this type is not just
 * `number`: an interval read out as its centre is the projection's uncertainty
 * silently dropped, which is the one thing these charts exist to keep visible.
 */
interface TooltipEntry {
  name?: string | number
  value?: string | number | [number, number]
  color?: string
}

interface ChartTooltipProps {
  active?: boolean
  label?: string | number
  payload?: TooltipEntry[]
  /** Optional formatter for values. */
  valueFormatter?: (value: number) => string
}

/** Format one entry's value — a range renders as its two bounds, never as a point. */
function readValue(
  value: TooltipEntry['value'],
  format: (value: number) => string,
): string {
  if (Array.isArray(value)) return `${format(value[0])} – ${format(value[1])}`
  return typeof value === 'number' ? format(value) : String(value ?? '—')
}

/** A light, card-styled Recharts tooltip shared by every chart. */
export function ChartTooltip({
  active,
  label,
  payload,
  valueFormatter = (v) => String(v),
}: ChartTooltipProps): ReactElement | null {
  if (!active || !payload || payload.length === 0) return null
  return (
    <div className="rounded-md border border-border bg-popover px-3 py-2 shadow-pop">
      {label !== undefined && (
        <p className="mb-1 font-mono text-[0.6875rem] tracking-wide text-muted-foreground uppercase">
          {label}
        </p>
      )}
      <ul className="flex flex-col gap-1">
        {payload.map((entry, i) => (
          <li key={i} className="flex items-center gap-2 text-xs">
            <span
              className="size-2 rounded-full"
              style={{ background: entry.color ?? 'var(--primary)' }}
            />
            <span className="text-muted-foreground">{entry.name}</span>
            <span className="tabular ml-auto font-mono font-medium text-foreground">
              {readValue(entry.value, valueFormatter)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}