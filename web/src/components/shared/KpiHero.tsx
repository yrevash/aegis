'use client'

import type { ReactElement, ReactNode } from 'react'

import { Card } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { Signal } from '@/config/signals'
import { cn } from '@/lib/utils'

import { MiniTrend } from './MiniTrend'
import { StatDelta, type DeltaDirection, type DeltaTone } from './StatDelta'

interface KpiHeroProps {
  /** Short metric name, e.g. "Cost saved". */
  label: string
  /** Raw number — KpiHero owns the formatting. */
  value: number
  /** Format the value (default: locale integer). Pass currency/percent here. */
  format?: (n: number) => string
  delta?: { value: number; direction?: DeltaDirection; tone?: DeltaTone; suffix?: string }
  /** Inline trend series drawn under the number. */
  trend?: number[]
  /** Hue accent for the trend (default neutral). */
  signal?: Signal
  /** Relocated prose, surfaced via an ⓘ InfoTip next to the label. */
  info?: ReactNode
  /** Marks the figure as an illustrative sample (kept honest, never hidden). */
  sample?: boolean
  className?: string
}

/**
 * The one-big-number hero tile: a screen's single display figure, its delta, and
 * an inline trend.
 *
 * **The number no longer counts up.** DESIGN.md §6 rules that out in as many
 * words — "no counting-up numbers on a governance figure — a spend cap that
 * animates is a spend cap that looks approximate" — and this tile's own doc named
 * "Cost saved" as its example, which is exactly the case the rule is about. A
 * figure a jury is being asked to trust must not spend its first second being
 * wrong on purpose. `CountUp` still exists and is still exported for the eval
 * scores that legitimately tick when a new sample lands; it is just not what a
 * money figure does.
 *
 * The figure is set through {@link Figure} at `display`, so it is mono and
 * tabular like every other numeral in the console, and so a screen can only have
 * one of these — a second hero-sized number on a page is a hierarchy failure
 * rather than emphasis (DESIGN.md §3).
 *
 * The coloured dot beside the label is gone with it: it repeated the trend's hue
 * and named nothing, which is the "a colour that means nothing" case in §8.
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
  return (
    <Card className={cn('flex h-full flex-col gap-4 p-6', className)}>
      <div className="flex items-center gap-2">
        <span className="eyebrow">{label}</span>
        {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
        {sample && (
          <span className="eyebrow ml-auto rounded-sm border border-border px-1.5 py-0.5 text-[0.6875rem]">
            sample
          </span>
        )}
      </div>

      <div className="flex flex-wrap items-end gap-x-3 gap-y-1">
        <Figure size="display" className="text-foreground">
          {format(value)}
        </Figure>
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
