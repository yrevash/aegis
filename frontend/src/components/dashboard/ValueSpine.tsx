import { Coins, FileSearch, Gauge, ShieldCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { BentoTile } from '@/components/shared/BentoGrid'
import { CountUp } from '@/components/shared/CountUp'
import { InfoTip } from '@/components/ui/InfoTip'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { MetricsResponse } from '@/types/api'

import { reductionPct } from './overview'
import { formatUsd } from './roi'

interface ValueTileProps {
  icon: LucideIcon
  /** 1–3 word outcome label (§3). */
  title: string
  signal: Signal
  /** The headline figure, already formatted. */
  value: string
  /** One short line under the number (≤ ~6 words). */
  caption: string
  /** Relocated prose + honest tech, one layer down. */
  info: ReactNode
  live?: boolean
  sample?: boolean
  index: number
}

/** A single outcome tile in the value spine (§4.2 Row 4). */
function ValueTile({
  icon: Icon,
  title,
  signal,
  value,
  caption,
  info,
  live = false,
  sample = false,
  index,
}: ValueTileProps): ReactElement {
  const token = SIGNALS[signal]
  return (
    <BentoTile span={3} interactive reveal index={index}>
      <div className="flex h-full flex-col gap-2.5">
        <div className="flex items-center gap-2">
          <span className={cn('grid size-6 shrink-0 place-items-center rounded-md', token.bg)}>
            <Icon className={cn('size-3.5', token.text)} />
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
          {sample && (
            <span className="eyebrow ml-auto shrink-0 rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.56rem] text-muted-foreground">
              sample
            </span>
          )}
        </div>
        <p className="tabular font-display text-[1.6rem] leading-none font-semibold text-foreground">
          {value}
        </p>
        <p className="t-body mt-auto text-muted-foreground">{caption}</p>
      </div>
    </BentoTile>
  )
}

/**
 * The management **value spine** as a four-tile bento (§4.2 Row 4) — the outcomes
 * a buyer signs off on: **Savings · Security · Performance · Audit**. Each tile is
 * icon + number + one line, with the explanatory prose relocated to an ⓘ tooltip
 * (the glass-box depth stays, one layer down). Live-measured figures carry a
 * green dot; anything the backend cannot measure yet is honestly badged "sample".
 */
export function ValueSpine({ metrics }: { metrics: MetricsResponse | null }): ReactElement {
  const baseline = metrics?.baseline_cost_usd ?? null
  const costPer1k = metrics?.cost_per_1k_queries_usd ?? null
  const saved = metrics?.cost_saved_usd ?? null
  const reduction = reductionPct(baseline, costPer1k)

  return (
    <>
      <ValueTile
        icon={Coins}
        title="Savings"
        signal="ok"
        value={reduction != null ? `${reduction}%` : '—'}
        caption="cheaper per 1k vs frontier"
        live={reduction != null}
        index={0}
        info={
          <>
            Cost per 1,000 queries versus running every query on the frontier
            model — the gap comes from routing easy work to small models and
            serving repeats from cache.
            {saved != null && (
              <>
                {' '}
                <CountUp value={saved} format={formatUsd} className="font-semibold" /> saved
                so far.
              </>
            )}
          </>
        }
      />

      <ValueTile
        icon={ShieldCheck}
        title="Security"
        signal="block"
        value="100%"
        caption="requests guarded"
        sample
        index={1}
        info="Every request passes injection, PII and schema guardrails before it runs. Sample: 37 blocks in 24h, 4 prompt-injection attempts caught, 0 tool calls off-allowlist."
      />

      <ValueTile
        icon={Gauge}
        title="Performance"
        signal="graph"
        value={costPer1k != null ? formatUsd(costPer1k) : '—'}
        caption="cost / 1k queries"
        live={costPer1k != null}
        index={2}
        info="The cheapest model that clears the quality bar handles each request. Blended cost per 1,000 queries, cache included. Sample: p95 latency 1.9s, $0.006 per solved task."
      />

      <ValueTile
        icon={FileSearch}
        title="Audit"
        signal="agent"
        value="100%"
        caption="traced end-to-end"
        sample
        index={3}
        info="Every action is written to an append-only trail with its trace, model and approver. Sample: 1,284 actions logged, 100% traced end-to-end."
      />
    </>
  )
}
