import { Coins, Cpu, Loader2, TrendingUp } from 'lucide-react'
import type { ReactElement } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { formatUsd } from '@/components/dashboard/roi'
import { KpiTile } from '@/components/metrics/KpiTile'
import { BentoGrid, BentoTile } from '@/components/shared'
import { Card, CardContent } from '@/components/ui/card'
import { InfoTip } from '@/components/ui/InfoTip'
import { getUsage } from '@/api/client'
import type { UsageResponse } from '@/types/api'

import { formatTokens, modelSpendData, totalTokens } from './usage'
import { useAsync } from './useAsync'

/** A small titled section header for a bento tile. */
function TileHead({
  title,
  info,
}: {
  title: string
  info?: string
}): ReactElement {
  return (
    <div className="mb-3 flex items-center gap-2">
      <span className="t-title text-foreground">{title}</span>
      {info && <InfoTip label={`About ${title}`}>{info}</InfoTip>}
    </div>
  )
}

/**
 * Usage — measured spend and tokens for the tenant, per model, with a cost
 * trend. Every figure reads from `GET /admin/usage`, so it is attributable, not
 * asserted. The prose that used to caption the charts now lives in tooltips.
 */
export function UsageView({ token }: { token: string | null }): ReactElement {
  const { state } = useAsync<UsageResponse>(() => getUsage(token, { window: 'month' }), [token])

  if (state.status === 'loading') {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading usage…
        </CardContent>
      </Card>
    )
  }
  if (state.status === 'error') {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-block-ink">
          Could not load usage. {state.message}
        </CardContent>
      </Card>
    )
  }

  const u = state.data
  const bars = modelSpendData(u.by_model)
  const donut: DonutDatum[] = bars.map((d) => ({ name: d.model, value: d.cost, color: d.color }))
  const series = u.series.map((p) => ({ day: p.ts.slice(5), cost: Number(p.cost_usd.toFixed(2)) }))

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <KpiTile label="Total spend" value={formatUsd(u.total_cost_usd)} signal="ml" icon={Coins} tint="purple" sub="this month" />
        <KpiTile label="Total tokens" value={formatTokens(totalTokens(u))} signal="graph" icon={Cpu} tint="blue" sub={`${formatTokens(u.total_prompt_tokens)} in · ${formatTokens(u.total_completion_tokens)} out`} />
        <KpiTile label="Models in use" value={String(u.by_model.length)} signal="agent" icon={TrendingUp} sub="distinct models" />
      </div>

      <BentoGrid>
        <BentoTile span={7} reveal index={0}>
          <TileHead
            title="Spend by model"
            info="Measured USD spend per model for the current month window, largest first. The long tail folds into a single row."
          />
          {bars.length > 0 ? (
            <BarChart data={bars} index="model" category="cost" color="ml" valueFormatter={(v) => formatUsd(v)} height={220} />
          ) : (
            <EmptyChart />
          )}
        </BentoTile>

        <BentoTile span={5} reveal index={1}>
          <TileHead
            title="Model mix"
            info="Each model's share of month-to-date spend. Same figures as the bars, seen as proportions."
          />
          {donut.length > 0 ? (
            <DonutChart data={donut} centerLabel={formatUsd(u.total_cost_usd)} centerSub="total" valueFormatter={(v) => formatUsd(v)} height={220} />
          ) : (
            <EmptyChart />
          )}
        </BentoTile>

        <BentoTile span={12} reveal index={2}>
          <TileHead
            title="Cost trend"
            info="Daily measured spend over the window — the shape of cost over time, not a projection."
          />
          <AreaChart data={series} index="day" category="cost" color="ml" valueFormatter={(v) => formatUsd(v)} height={200} />
        </BentoTile>
      </BentoGrid>
    </div>
  )
}

/** A calm placeholder when there is no spend to chart yet. */
function EmptyChart(): ReactElement {
  return (
    <div className="flex h-[220px] items-center justify-center text-sm text-muted-foreground">
      No spend recorded in this window yet.
    </div>
  )
}
