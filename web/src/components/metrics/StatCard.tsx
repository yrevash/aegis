'use client'

import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { MiniTrend } from '@/components/shared/MiniTrend'
import { StatDelta, type DeltaDirection, type DeltaTone } from '@/components/shared/StatDelta'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

interface StatCardProps {
  label: string
  /** Raw number — the tile owns the formatting. Null renders "—". */
  value: number | null
  format?: (n: number) => string
  icon?: LucideIcon
  signal?: Signal
  delta?: { value: number; direction?: DeltaDirection; tone?: DeltaTone; suffix?: string }
  /** Inline trend drawn under the value (SVG, no recharts). */
  trend?: number[]
  /** Green live dot — the figure is measured from `/metrics` this session. */
  live?: boolean
  /** Honest "sample" marker — an illustrative figure with no backend field yet. */
  sample?: boolean
  /** Relocated prose surfaced via an ⓘ tooltip next to the label. */
  info?: ReactNode
  className?: string
}

/**
 * A compact numeric stat tile for the Overview bento — icon chip, figure,
 * optional delta chip and inline trend. Numbers lead; any explanation lives one
 * layer down in the `info` tooltip. Honest to the end: live figures carry a
 * green dot, illustrative ones keep their `sample` badge.
 *
 * **The figure does not animate, and that is the point.** This tile draws spend
 * against a cap (`MyBudgetTile`), and a governance figure that counts up to its
 * value is a governance figure that looks approximate while it is arriving —
 * DESIGN.md §6 rules it out for exactly that reason. It is now a {@link Figure},
 * so it is mono and tabular as well: a column of these tiles aligns on the
 * digits and none of them reflows sideways when a poll ticks the value.
 */
export function StatCard({
  label,
  value,
  format = (n) => Math.round(n).toLocaleString(),
  icon: Icon,
  signal = 'neutral',
  delta,
  trend,
  live = false,
  sample = false,
  info,
  className,
}: StatCardProps): ReactElement {
  const token = SIGNALS[signal]
  return (
    <div className={cn('flex h-full flex-col gap-2.5', className)}>
      <div className="flex items-center gap-2">
        {Icon && (
          <span className={cn('grid size-6 shrink-0 place-items-center rounded-md', token.bg)}>
            <Icon className={cn('size-3.5', token.text)} />
          </span>
        )}
        <span className="eyebrow truncate">{label}</span>
        {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
        {live && (
          <span
            className="animate-pip ml-auto size-1.5 shrink-0 rounded-full bg-ok"
            style={{ ['--pip-color' as string]: 'var(--ok)' }}
            title="live from /metrics"
          />
        )}
        {sample && (
          <span className="eyebrow ml-auto shrink-0 rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.56rem] text-muted-foreground">
            sample
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-x-2 gap-y-0.5">
        {value == null ? (
          <Figure size="display" className="text-muted-foreground" label="not recorded">
            —
          </Figure>
        ) : (
          <Figure size="display" className="text-foreground">
            {format(value)}
          </Figure>
        )}
        {delta && (
          <span className="mb-0.5">
            <StatDelta
              value={delta.value}
              direction={delta.direction}
              tone={delta.tone}
              suffix={delta.suffix}
            />
          </span>
        )}
      </div>

      {trend && trend.length > 0 && (
        <div className="mt-auto">
          <MiniTrend data={trend} color={signal} height={32} variant="area" />
        </div>
      )}
    </div>
  )
}
