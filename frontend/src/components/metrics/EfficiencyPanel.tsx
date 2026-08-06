import { Coins, DatabaseZap, Gauge, Cpu } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { cn } from '@/lib/utils'
import type { MetricsResponse } from '@/types/api'
import type { RunState } from '@/state/runReducer'

interface StatProps {
  icon: typeof Gauge
  label: string
  value: string
  meter?: number
  meterColor?: string
}

function Stat({ icon: Icon, label, value, meter, meterColor }: StatProps): ReactElement {
  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-1.5">
        <Icon className="size-3.5 text-muted-foreground" />
        <span className="eyebrow">{label}</span>
      </div>
      <p className="tabular font-display text-lg leading-none font-semibold">{value}</p>
      {meter != null && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
          <div
            className="h-full rounded-full transition-all duration-700"
            style={{ width: `${Math.round(meter * 100)}%`, background: meterColor }}
          />
        </div>
      )}
    </div>
  )
}

interface EfficiencyPanelProps {
  metrics: MetricsResponse | null
  state: RunState
}

/**
 * The eval + token/cost panel. Fleet efficiency (cache-hit rate, small-model
 * share, cost per 1k queries, quality score) from `GET /metrics`, plus this
 * run's usage from `run_finished`. Tokens are visible to the jury, so
 * efficiency is a number on screen — measured, not claimed.
 */
export function EfficiencyPanel({ metrics, state }: EfficiencyPanelProps): ReactElement {
  const usage = state.usage
  const runTokens = usage ? usage.prompt_tokens + usage.completion_tokens : null

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Gauge className="size-4 text-ok" />
        <CardTitle>Efficiency & evals</CardTitle>
        <Badge variant="ok" className="ml-auto">
          fleet
        </Badge>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid grid-cols-2 gap-x-5 gap-y-4">
          <Stat
            icon={DatabaseZap}
            label="Cache-hit rate"
            value={metrics ? `${Math.round(metrics.cache_hit_rate * 100)}%` : '—'}
            meter={metrics?.cache_hit_rate}
            meterColor="var(--ok)"
          />
          <Stat
            icon={Cpu}
            label="Small-model share"
            value={metrics ? `${Math.round(metrics.small_model_share * 100)}%` : '—'}
            meter={metrics?.small_model_share}
            meterColor="var(--agent)"
          />
          <Stat
            icon={Coins}
            label="Cost / 1k queries"
            value={metrics ? `$${metrics.cost_per_1k_queries_usd.toFixed(2)}` : '—'}
          />
          <Stat
            icon={Gauge}
            label="Quality score"
            value={metrics?.quality_score != null ? metrics.quality_score.toFixed(2) : '—'}
            meter={metrics?.quality_score ?? undefined}
            meterColor="var(--ml)"
          />
        </div>

        <Separator />

        <div>
          <div className="mb-2 flex items-center justify-between">
            <span className="eyebrow">This run</span>
            {usage &&
              (usage.cache_hit ? (
                <Badge variant="ok">cache hit</Badge>
              ) : (
                <Badge variant="secondary">cache miss</Badge>
              ))}
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md border border-border/70 bg-surface/50 py-2">
              <p className="tabular font-display text-base font-semibold text-agent-ink">
                {runTokens ?? (state.phase === 'streaming' ? '···' : '—')}
              </p>
              <p className="eyebrow mt-0.5">tokens</p>
            </div>
            <div className="rounded-md border border-border/70 bg-surface/50 py-2">
              <p className="tabular font-display text-base font-semibold text-foreground">
                {usage ? `$${usage.cost_usd.toFixed(4)}` : '—'}
              </p>
              <p className="eyebrow mt-0.5">cost</p>
            </div>
            <div className="rounded-md border border-border/70 bg-surface/50 py-2">
              <p
                className={cn(
                  'font-display text-base font-semibold',
                  state.finishedStatus === 'completed'
                    ? 'text-ok-ink'
                    : state.finishedStatus === 'blocked'
                      ? 'text-block-ink'
                      : 'text-muted-foreground',
                )}
              >
                {state.finishedStatus ?? state.phase}
              </p>
              <p className="eyebrow mt-0.5">status</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
