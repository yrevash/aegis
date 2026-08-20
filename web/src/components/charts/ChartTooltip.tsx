'use client'

import type { ReactElement } from 'react'

/** One entry Recharts passes to a custom tooltip. */
interface TooltipEntry {
  name?: string | number
  value?: string | number
  color?: string
}

interface ChartTooltipProps {
  active?: boolean
  label?: string | number
  payload?: TooltipEntry[]
  /** Optional formatter for values. */
  valueFormatter?: (value: number) => string
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
        <p className="mb-1 font-mono text-[0.68rem] tracking-wide text-muted-foreground uppercase">
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
              {typeof entry.value === 'number' ? valueFormatter(entry.value) : entry.value}
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}