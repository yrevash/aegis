'use client'

import { Clock, TrendingDown, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { CountUp } from '@/components/shared/CountUp'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { MetricsResponse } from '@/lib/api/types'

import {
  PER_MILLION,
  SAMPLE_ASSUMPTIONS,
  costAtVolumeUsd,
  costPerQueryUsd,
  formatCountCompact,
  formatUsd,
  formatUsdCompact,
  manualCostPerCaseUsd,
  manualVsAgentMultiple,
  savedAtVolumeUsd,
  savedPerCaseUsd,
} from './roi'

interface ProjectionProps {
  label: string
  value: number | null
  format: (n: number) => string
  sub: string
  tint: 'blue' | 'purple'
}

/** A soft-tinted "cost at scale" projection tile with a count-up figure. */
function Projection({ label, value, format, sub, tint }: ProjectionProps): ReactElement {
  return (
    <div className={cn('rounded-lg p-3.5', tint === 'blue' ? 'bg-tint-blue' : 'bg-tint-purple')}>
      <p className="eyebrow">{label}</p>
      {value == null ? (
        <p className="tabular font-display mt-1.5 text-2xl leading-none font-semibold text-muted-foreground">
          —
        </p>
      ) : (
        <CountUp
          value={value}
          format={format}
          className="font-display mt-1.5 block text-2xl leading-none font-semibold text-foreground"
        />
      )}
      <p className="mt-1.5 font-mono text-[0.64rem] text-muted-foreground">{sub}</p>
    </div>
  )
}

/**
 * The unit-economics detail — the layer beneath the money hero. Two honestly-
 * separated stories, both derived in the pure `roi.ts` module:
 *  - **Cost at scale** — $/1M and $/month projected from the *measured* per-1k
 *    rate (the volume is a labelled sample assumption), each against the
 *    frontier-model baseline saving.
 *  - **Manual vs agent** — a human at a sample labour rate versus the agent's
 *    *real* per-query cost.
 *
 * Every non-measured input keeps its sample note.
 */
export function RoiPanel({ metrics }: { metrics: MetricsResponse | null }): ReactElement {
  const a = SAMPLE_ASSUMPTIONS
  const costPer1k = metrics?.cost_per_1k_queries_usd ?? null
  const baseline = metrics?.baseline_cost_usd ?? null
  const hasScale = costPer1k != null && baseline != null

  const perMillion = costPer1k != null ? costAtVolumeUsd(costPer1k, PER_MILLION) : null
  const savedPerMillion =
    hasScale && costPer1k != null ? savedAtVolumeUsd(baseline, costPer1k, PER_MILLION) : null
  const perMonth = costPer1k != null ? costAtVolumeUsd(costPer1k, a.monthlyVolume) : null
  const savedPerMonth =
    hasScale && costPer1k != null ? savedAtVolumeUsd(baseline, costPer1k, a.monthlyVolume) : null

  const unitCost = costPer1k != null ? costPerQueryUsd(costPer1k) : null
  const savedPerCase = costPer1k != null ? savedPerCaseUsd(costPer1k, a) : null
  const multiple = costPer1k != null ? manualVsAgentMultiple(costPer1k, a) : null

  return (
    <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
      {/* Cost at scale — projected from the measured per-1k rate. */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-2">
          <TrendingDown className="size-3.5 text-graph-ink" />
          <span className="eyebrow">Cost at scale</span>
          <InfoTip label="About cost at scale">
            {`$/1M is measured (cost-per-1k × 1,000). $/month assumes ~${formatCountCompact(a.monthlyVolume)} queries/month; baseline savings use the real frontier-model cost.`}
          </InfoTip>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Projection
            label="$ / 1M queries"
            value={perMillion}
            format={formatUsdCompact}
            sub={savedPerMillion != null ? `saves ${formatUsdCompact(savedPerMillion)} vs baseline` : 'awaiting metrics'}
            tint="blue"
          />
          <Projection
            label="$ / month"
            value={perMonth}
            format={formatUsdCompact}
            sub={savedPerMonth != null ? `saves ${formatUsdCompact(savedPerMonth)} / month` : 'awaiting metrics'}
            tint="purple"
          />
        </div>
      </div>

      {/* Manual vs agent — real unit cost against a sample labour rate. */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-2">
          <Users className="size-3.5 text-ml-ink" />
          <span className="eyebrow">Manual vs agent · per case</span>
          <InfoTip label="About manual vs agent">
            {savedPerCase != null && unitCost != null
              ? `Agent unit cost is measured (${formatUsd(unitCost, 4)}). Human time (${a.manualMinutes} min), labour rate ($${a.laborRateUsdPerHour}/hr) and agent seconds are illustrative — net ${formatUsd(savedPerCase)} saved per case.`
              : 'Human time, labour rate and agent seconds are illustrative; agent unit cost is measured.'}
          </InfoTip>
        </div>
        <div className="flex items-stretch gap-3">
          <div className="flex-1 rounded-lg border border-border bg-surface-2/60 p-3">
            <p className="eyebrow">Human, unaided</p>
            <p className="tabular font-display mt-1.5 text-xl leading-none font-semibold text-foreground">
              {formatUsd(manualCostPerCaseUsd(a))}
            </p>
            <p className="mt-1.5 flex items-center gap-1 font-mono text-[0.64rem] text-muted-foreground">
              <Clock className="size-3" /> ~{a.manualMinutes} min · ${a.laborRateUsdPerHour}/hr
            </p>
          </div>
          <div className="flex items-center justify-center">
            <span className="tabular font-display text-sm font-semibold text-ok-ink">
              {multiple != null ? `${Math.round(multiple).toLocaleString('en-US')}×` : '—'}
              <span className="ml-1 text-[0.6rem] font-normal text-muted-foreground">cheaper</span>
            </span>
          </div>
          <div className="flex-1 rounded-lg border border-ok/30 bg-ok/[0.06] p-3">
            <p className="eyebrow text-ok-ink">Agent</p>
            <p className="tabular font-display mt-1.5 text-xl leading-none font-semibold text-foreground">
              {unitCost != null ? formatUsd(unitCost, 4) : '—'}
            </p>
            <p className="mt-1.5 flex items-center gap-1 font-mono text-[0.64rem] text-muted-foreground">
              <Clock className="size-3" /> ~{a.agentSeconds}s · measured unit cost
            </p>
          </div>
        </div>
      </div>
    </div>
  )
}
