'use client'

import { Coins, FileSearch, Gauge, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { BentoTile } from '@/components/shared/BentoGrid'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { MetricsResponse } from '@/lib/api/types'

import { reductionPct } from './overview'
import { formatUsd } from './roi'

/** The tile head — icon chip, outcome label, the ⓘ, and the live pip. */
function TileHead({
  icon: Icon,
  title,
  signal,
  info,
  live = false,
}: {
  icon: LucideIcon
  title: string
  signal: Signal
  info: ReactNode
  live?: boolean
}): ReactElement {
  const token = SIGNALS[signal]
  return (
    <div className="flex items-center gap-2">
      <span className={cn('grid size-6 shrink-0 place-items-center rounded-md', token.bg)}>
        <Icon className={cn('size-3.5', token.text)} aria-hidden />
      </span>
      <span className="t-label text-foreground">{title}</span>
      <InfoTip label={`About ${title}`} className="shrink-0">
        {info}
      </InfoTip>
      {live && (
        <span
          className="animate-pip ml-auto size-1.5 shrink-0 rounded-full bg-ok"
          style={{ ['--pip-color' as string]: 'var(--ok)' }}
          title="live from /metrics"
        />
      )}
    </div>
  )
}

interface MeasuredTileProps {
  icon: LucideIcon
  /** 1–3 word outcome label. */
  title: string
  signal: Signal
  /** The headline figure, already formatted. `null` renders a dash, never a guess. */
  value: string | null
  /** One short line under the number (≤ ~6 words). */
  caption: string
  /** Relocated prose, one layer down. */
  info: ReactNode
  /** Where the figure came from — the field, not the vibe. */
  origin: string
  index: number
}

/** An outcome tile whose number is measured, with the field that produced it. */
function MeasuredTile({
  icon,
  title,
  signal,
  value,
  caption,
  info,
  origin,
  index,
}: MeasuredTileProps): ReactElement {
  return (
    <BentoTile span={3} reveal index={index}>
      <div className="flex h-full flex-col gap-2.5">
        <TileHead icon={icon} title={title} signal={signal} info={info} live={value != null} />
        <Figure size="display" className={value == null ? 'text-muted-foreground' : 'text-foreground'}>
          {value ?? '—'}
        </Figure>
        <p className="t-body text-muted-foreground">{caption}</p>
        <Receipt origin={origin} className="mt-auto pt-3" />
      </div>
    </BentoTile>
  )
}

interface AbsentTileProps {
  icon: LucideIcon
  title: string
  signal: Signal
  /** The figure a reader expects here, named the way they would name it. */
  figure: string
  /** Why the platform cannot derive it. The honest reason, not an apology. */
  why: string
  /**
   * What would have to be emitted before it became a measurement.
   *
   * Shown in the ⓘ rather than in the tile: at `lg` a spine tile is a 3-of-12
   * column, and printing all three parts there ran to eight lines against four in
   * the measured tiles, which broke the row at every width above `sm`.
   */
  needed: string
  index: number
}

/**
 * An outcome tile whose number **is not recorded**, stated in the number's slot.
 *
 * This is the half of the spine that used to lie. Security and Audit each printed
 * a flat `100%` behind a small grey "sample" badge, with invented supporting
 * counts one hover away — "37 blocks in 24h", "1,284 actions logged". Nothing on
 * the platform measures either figure, so both were 100% by construction: a
 * number that cannot come out any other way is not evidence, it is decoration
 * shaped like evidence, and it sat on the surface leadership reads first.
 *
 * DESIGN.md §1 is unambiguous about the replacement — a figure that cannot be
 * sourced is never rendered as a number, it is a stated absence in the slot the
 * number would have occupied. So that is what these two tiles render, in the same
 * {@link Absence} treatment the forecast page uses, un-boxed because the tile is
 * already the surface.
 */
function AbsentTile({
  icon,
  title,
  signal,
  figure,
  why,
  needed,
  index,
}: AbsentTileProps): ReactElement {
  return (
    <BentoTile span={3} reveal index={index}>
      <div className="flex h-full flex-col gap-2.5">
        <TileHead
          icon={icon}
          title={title}
          signal={signal}
          info={
            <>
              Nothing on the platform records this yet, so the tile states what is missing
              instead of drawing a figure that would be the same number whatever happened. To
              measure it: {needed}
            </>
          }
        />
        <Absence figure={figure} why={why} className="mt-auto border-0 bg-transparent p-0" />
      </div>
    </BentoTile>
  )
}

/**
 * The management **value spine** — the four outcomes a buyer signs off on:
 * **Savings · Security · Performance · Audit**.
 *
 * Two of them are measured off `GET /metrics` and carry the field they came from.
 * Two of them are not measured at all, and say so. That asymmetry is the point:
 * a spine where every tile reads 100% tells a reader nothing about which claims
 * the platform can actually stand behind.
 */
export function ValueSpine({ metrics }: { metrics: MetricsResponse | null }): ReactElement {
  const baseline = metrics?.baseline_cost_usd ?? null
  const costPer1k = metrics?.cost_per_1k_queries_usd ?? null
  const saved = metrics?.cost_saved_usd ?? null
  const reduction = reductionPct(baseline, costPer1k)

  return (
    <>
      <MeasuredTile
        icon={Coins}
        title="Savings"
        signal="ok"
        value={reduction == null ? null : `${reduction}%`}
        caption="cheaper per 1k vs frontier"
        index={0}
        origin="GET /metrics · cost_per_1k_queries_usd vs baseline_cost_usd"
        info={
          <>
            Cost per 1,000 queries versus running every query on the frontier model — the gap
            comes from routing easy work to small models and serving repeats from cache.
            {saved != null && <> {formatUsd(saved)} saved so far.</>}
          </>
        }
      />

      <AbsentTile
        icon={ShieldCheck}
        title="Security"
        signal="block"
        index={1}
        figure="Share of requests the guardrails cleared"
        why="No counter records how many requests reached the rails or what they decided, so any share computed here would be 100% by construction."
        needed="A guardrail outcome tally on GET /metrics — evaluated, blocked, and by which rail."
      />

      <MeasuredTile
        icon={Gauge}
        title="Performance"
        signal="graph"
        value={costPer1k == null ? null : formatUsd(costPer1k)}
        caption="cost / 1k queries"
        index={2}
        origin="GET /metrics · cost_per_1k_queries_usd"
        info="The cheapest model that clears the quality bar handles each request. Blended cost per 1,000 queries, cache included."
      />

      <AbsentTile
        icon={FileSearch}
        title="Audit"
        signal="agent"
        index={3}
        figure="Share of actions traced end to end"
        why="The audit trail is append-only and has no denominator: nothing counts the actions that were never written to it, so the share can only ever come out at 100%."
        needed="An emitted-versus-recorded pair on GET /metrics, written at the same chokepoint that appends the trail."
      />
    </>
  )
}
