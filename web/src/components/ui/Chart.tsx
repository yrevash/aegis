import type { ReactNode } from 'react'
import { cn } from '@/lib/utils'

/**
 * ChartFrame — a thin chart wrapper (title + legend slot + a fixed-height plot
 * area) so surfaces have a consistent home for a charting library (recharts /
 * chart.js) in later tasks. In this scaffold it renders its children (or a
 * neutral empty state) inside the framed plot area. Restyled to our tokens.
 */
export function ChartFrame({
  title,
  eyebrow,
  legend,
  height = 260,
  children,
  className,
}: {
  title: ReactNode
  eyebrow?: ReactNode
  legend?: ReactNode
  height?: number
  children?: ReactNode
  className?: string
}) {
  return (
    <div className={cn('rounded-2xl border border-border bg-card p-5 shadow-card md:p-6', className)}>
      <div className="mb-4 flex items-start justify-between gap-4">
        <div>
          {eyebrow ? <p className="eyebrow mb-1">{eyebrow}</p> : null}
          <h3 className="t-title text-foreground">{title}</h3>
        </div>
        {legend ? <div className="shrink-0">{legend}</div> : null}
      </div>
      <div
        className="flex items-center justify-center rounded-xl border border-dashed border-border bg-surface-2/40"
        style={{ height }}
      >
        {children ?? (
          <span className="text-sm text-muted-foreground">Chart renders here once wired</span>
        )}
      </div>
    </div>
  )
}
