'use client'

import type { ReactElement, ReactNode } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { BentoTile } from '@/components/shared/BentoGrid'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { MetricsResponse } from '@/lib/api/types'

import { costTrendSeries, modelMixData, queryVolumeSeries } from './overview'

/**
 * The three-chart row of the Overview bento: **Cost trend** (`AreaChart`),
 * **Model mix** (`DonutChart`) and **Query volume** (`BarChart`). Real recharts,
 * each drawing in on mount. All three are now **measured**: the cost trend and
 * query volume are derived from the real in-session `/metrics` samples (the
 * polled history), and the model-mix donut from the live `small_model_share`.
 * Before enough samples have accumulated each series shows an honest
 * "accumulating…" state rather than a fabricated constant series.
 *
 * **All three single-series charts are drawn in the same ramp step.** The cost
 * trend used to be `ok` — the reserved status green — which DESIGN.md §2 forbids
 * as a series colour: a reader who has learned that green means healthy reads a
 * green cost line as a verdict on the cost. These are three separate charts with
 * three titles, not three series in one, so nothing has to tell them apart by
 * hue; they use `graph` (#1570ef), the ramp step that clears 3:1 against the card
 * surface. The lighter step `#60a5fa` does not, which is why it is only ever used
 * where a label sits beside it.
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
      <h3 className="t-title text-foreground">{title}</h3>
      <InfoTip label={`About ${title}`}>{info}</InfoTip>
      {live && (
        <span
          className="animate-pip ml-auto size-1.5 rounded-full bg-ok"
          style={{ ['--pip-color' as string]: 'var(--ok)' }}
          title="live from /metrics"
        />
      )}
      {sample && (
        <span className="eyebrow ml-auto rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.6875rem] text-muted-foreground">
          sample
        </span>
      )}
    </div>
  )
}

/** An honest placeholder while a measured series is still accumulating samples. */
function Accumulating(): ReactElement {
  return (
    <div
      role="status"
      className="flex h-[200px] items-center justify-center text-sm text-muted-foreground"
    >
      Accumulating…
    </div>
  )
}

export function DashboardCharts({
  metrics,
  history = [],
}: {
  metrics: MetricsResponse | null
  /** The real in-session `/metrics` samples, oldest → newest. */
  history?: readonly MetricsResponse[]
}): ReactElement {
  const share = metrics?.small_model_share ?? null
  const modelMix: DonutDatum[] = modelMixData(share)
  const costTrend = costTrendSeries(history)
  const queryVolume = queryVolumeSeries(history)

  return (
    <>
      <BentoTile span={4} reveal index={0}>
        <ChartHead
          title="Cost trend"
          live={costTrend.length >= 2}
          info="Blended cost per 1,000 queries across the polled /metrics samples — a measured in-session trend, not a fixture. It fills in as the dashboard polls."
        />
        {costTrend.length < 2 ? (
          <Accumulating />
        ) : (
          <AreaChart
            data={costTrend}
            index="t"
            category="cost"
            color="graph"
            valueFormatter={(v) => `$${v.toFixed(2)}`}
            height={200}
          />
        )}
      </BentoTile>

      <BentoTile span={4} reveal index={1}>
        <ChartHead
          title="Model mix"
          live={share != null}
          info="Share of traffic served by small models versus the frontier model — the routing decision that drives most of the savings."
        />
        {share == null ? (
          <div
            role="status"
            className="flex h-[200px] items-center justify-center text-sm text-muted-foreground"
          >
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
          live={queryVolume.length >= 1}
          info="LLM calls served between successive /metrics polls — the real per-interval throughput, derived from the measured cumulative call count. It fills in as the dashboard polls."
        />
        {queryVolume.length < 1 ? (
          <Accumulating />
        ) : (
          <BarChart data={queryVolume} index="t" category="calls" color="graph" height={200} allowDecimals={false} />
        )}
      </BentoTile>
    </>
  )
}

export default DashboardCharts
