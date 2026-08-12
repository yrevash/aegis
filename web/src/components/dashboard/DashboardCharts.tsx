'use client'

import type { ReactElement, ReactNode } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { BentoTile } from '@/components/shared/BentoGrid'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { MetricsResponse } from '@/lib/api/types'

import { COST_TREND, QUERY_VOLUME } from './data'
import { modelMixData } from './overview'

/**
 * The three-chart row of the Overview bento: **Cost trend** (`AreaChart`),
 * **Model mix** (`DonutChart`, live from `/metrics`) and **Query volume**
 * (`BarChart`). Real recharts, each drawing in on mount. The trend and volume
 * series are illustrative fixtures (no backend field yet) and keep their honest
 * "sample" badge; the model-mix donut is backed by real `/metrics` data.
 */

/** A tile header — short title, an ⓘ for detail, and a live/sample marker. */
function ChartHead({
  title,
  info,
  live = false,
  sample = false,
}: {
  title: string
  info: ReactNode
  live?: boolean
  sample?: boolean
}): ReactElement {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="t-title text-foreground">{title}</span>
      <InfoTip label={`About ${title}`}>{info}</InfoTip>
      {live && (
        <span
          className="animate-pip ml-auto size-1.5 rounded-full bg-ok"
          style={{ ['--pip-color' as string]: 'var(--ok)' }}
          title="live from /metrics"
        />
      )}
      {sample && (
        <span className="eyebrow ml-auto rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.56rem] text-muted-foreground">
          sample
        </span>
      )}
    </div>
  )
}

export function DashboardCharts({ metrics }: { metrics: MetricsResponse | null }): ReactElement {
  const share = metrics?.small_model_share ?? null
  const modelMix: DonutDatum[] = modelMixData(share)

  return (
    <>
      <BentoTile span={4} reveal index={0}>
        <ChartHead
          title="Cost trend"
          sample
          info="Blended cost per 1,000 queries over recent intervals — trending down as routing and caching take effect."
        />
        <AreaChart
          data={COST_TREND}
          index="t"
          category="cost"
          color="ok"
          valueFormatter={(v) => `$${v.toFixed(2)}`}
          height={200}
        />
      </BentoTile>

      <BentoTile span={4} reveal index={1}>
        <ChartHead
          title="Model mix"
          live={share != null}
          info="Share of traffic served by small models versus the frontier model — the routing decision that drives most of the savings."
        />
        {share == null ? (
          <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
            Awaiting live metrics…
          </div>
        ) : (
          <DonutChart
            data={modelMix}
            centerLabel={`${Math.round(share * 100)}%`}
            centerSub="small model"
            valueFormatter={(v) => `${v}%`}
            height={200}
          />
        )}
      </BentoTile>

      <BentoTile span={4} reveal index={2}>
        <ChartHead
          title="Query volume"
          sample
          info="Requests handled across a working day — the throughput the cost and quality figures are measured over."
        />
        <BarChart data={QUERY_VOLUME} index="hour" category="queries" color="graph" height={200} />
      </BentoTile>
    </>
  )
}

export default DashboardCharts
