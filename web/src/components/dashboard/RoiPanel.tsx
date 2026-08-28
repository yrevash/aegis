'use client'

import { Clock, TrendingDown, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
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
}

/**
 * A soft-tinted "cost at scale" projection tile.
 *
 * The figure does not animate. These are money projections read next to a spend
 * cap, and DESIGN.md §6 rules out a governance figure that counts up to itself —
 * a number still arriving reads as a number still being decided.
 */
function Projection({ label, value, format, sub }: ProjectionProps): ReactElement {
  return (
    <div className="rounded-lg border border-border bg-tint-blue p-3.5">
      <p className="eyebrow">{label}</p>
      <p className="mt-1.5">
        <Figure size="stat" className={value == null ? 'text-muted-foreground' : 'text-foreground'}>
          {value == null ? '—' : format(value)}
        </Figure>
      </p>
      <p className="mt-1.5 font-mono text-[0.6875rem] text-muted-foreground">{sub}</p>
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
          <TrendingDown className="size-3.5 text-blue-600" aria-hidden />
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
          />
          <Projection
            label="$ / month"
            value={perMonth}
            format={formatUsdCompact}
            sub={savedPerMonth != null ? `saves ${formatUsdCompact(savedPerMonth)} / month` : 'awaiting metrics'}
          />
        </div>
        <Receipt
          origin="GET /metrics · cost_per_1k_queries_usd"
          detail={`projected at a sample volume of ${formatCountCompact(a.monthlyVolume)} queries/month`}
        />
      </div>

      {/* Manual vs agent — real unit cost against a sample labour rate. */}
      <div className="space-y-2.5">
        <div className="flex items-center gap-2">
          <Users className="size-3.5 text-blue-800" aria-hidden />
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
            <p className="mt-1.5">
              <Figure size="stat">{formatUsd(manualCostPerCaseUsd(a))}</Figure>
            </p>
            <p className="mt-1.5 flex items-center gap-1 font-mono text-[0.6875rem] text-muted-foreground">
              <Clock className="size-3" aria-hidden /> ~{a.manualMinutes} min · ${a.laborRateUsdPerHour}/hr
            </p>
          </div>
          <div className="flex items-center justify-center">
            <Figure className="font-semibold text-ok-ink" unit="cheaper">
              {multiple != null ? `${Math.round(multiple).toLocaleString('en-US')}×` : '—'}
            </Figure>
          </div>
          <div className="flex-1 rounded-lg border border-ok/30 bg-ok/[0.06] p-3">
            <p className="eyebrow text-ok-ink">Agent</p>
            <p className="mt-1.5">
              <Figure size="stat">{unitCost != null ? formatUsd(unitCost, 4) : '—'}</Figure>
            </p>
            <p className="mt-1.5 flex items-center gap-1 font-mono text-[0.6875rem] text-muted-foreground">
              <Clock className="size-3" aria-hidden /> ~{a.agentSeconds}s · measured unit cost
            </p>
          </div>
        </div>
        <Receipt
          origin="GET /metrics · cost_per_1k_queries_usd"
          detail={`compared against a sample rate of $${a.laborRateUsdPerHour}/hr over ${a.manualMinutes} min — the human side is an assumption, not a measurement`}
        />
      </div>
    </div>
  )
}
