'use client'

import type { ReactElement, ReactNode } from 'react'

import { Card } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

import { CountUp } from './CountUp'
import { MiniTrend } from './MiniTrend'
import { StatDelta, type DeltaDirection, type DeltaTone } from './StatDelta'

interface KpiHeroProps {
  /** Short metric name, e.g. "Cost saved". */
  label: string
  /** Raw number — KpiHero owns the count-up and formatting. */
  value: number
  /** Format the value (default: locale integer). Pass currency/percent here. */
  format?: (n: number) => string
  delta?: { value: number; direction?: DeltaDirection; tone?: DeltaTone; suffix?: string }
  /** Inline trend series drawn under the number. */
  trend?: number[]
  /** Hue accent for the eyebrow + trend (default neutral). */
  signal?: Signal
  /** Relocated prose, surfaced via an ⓘ InfoTip next to the label. */
  info?: ReactNode
  /** Marks the figure as an illustrative sample (kept honest, never hidden). */
  sample?: boolean
  className?: string
}

/**
 * The one-big-number hero tile (§5): a giant count-up figure with an optional
 * delta chip and an inline trend, accented by a signal hue. Self-contained (its
 * own hero Card) so a surface drops it into a bento cell via `className`.
 */
export function KpiHero({
  label,
  value,
  format = (n) => Math.round(n).toLocaleString(),
  delta,
  trend,
  signal = 'neutral',
  info,
  sample = false,
  className,
}: KpiHeroProps): ReactElement {
  const token = SIGNALS[signal]
  return (
    <Card className={cn('h-full gap-4 p-6', className)}>
      <div className="flex items-center gap-2">
        <span className={cn('inline-block size-2 rounded-full', signal === 'neutral' ? 'bg-muted-foreground' : token.bg)} style={signal !== 'neutral' ? { background: token.hex } : undefined} aria-hidden />
        <span className="eyebrow">{label}</span>
        {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
        {sample && (
          <span className="eyebrow ml-auto rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.58rem] text-muted-foreground">
            sample
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
        <CountUp value={value} format={format} className="t-hero text-foreground" />
        {delta && (
          <span className="mb-1">
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
        <MiniTrend data={trend} color={signal} height={48} variant="area" />
      )}
    </Card>
  )
}