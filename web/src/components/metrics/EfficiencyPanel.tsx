'use client'

import { Coins, DatabaseZap, Gauge, Cpu } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { Separator } from '@/components/primitives/separator'
import { cn } from '@/lib/utils'
import type { MetricsResponse } from '@/lib/api/types'
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
        <Icon className="size-3.5 text-muted-foreground" aria-hidden />
        <span className="eyebrow">{label}</span>
      </div>
      <Figure size="stat" className="text-lg leading-6">{value}</Figure>
      {meter != null && (
        /*
          Decorative: the figure above states the same rate. The 700ms
          `transition-all` is gone — DESIGN.md §6 caps product motion at 200ms and
          spends it on confirming a state change, and a rate sliding into place
          over two-thirds of a second reads as a rate still being decided.
        */
        <div className="h-1 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden>
          <div
            className="h-full rounded-full"
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
      <CardHeader
        title={
          <span className="flex items-center gap-2">
            <Gauge className="size-4 shrink-0 text-ok" aria-hidden />
            Efficiency &amp; evals
          </span>
        }
        actions={<Badge tone="ok">fleet</Badge>}
      />
      <CardBody className="space-y-4">
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
            meterColor="var(--blue-200)"
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
            meterColor="var(--blue-100)"
          />
        </div>

        <Separator />

        <div className="space-y-3">
          <div className="flex items-center justify-between">
            <span className="eyebrow">This run</span>
            {usage &&
              (usage.cache_hit ? (
                <Badge tone="ok">cache hit</Badge>
              ) : (
                <Badge tone="neutral">cache miss</Badge>
              ))}
          </div>
          <div className="grid grid-cols-3 gap-2 text-center">
            <div className="rounded-md border border-border/70 bg-surface/50 py-2">
              <Figure className="text-base leading-6 font-semibold">
                {runTokens ?? (state.phase === 'streaming' ? '···' : '—')}
              </Figure>
              <p className="eyebrow mt-0.5">tokens</p>
            </div>
            <div className="rounded-md border border-border/70 bg-surface/50 py-2">
              <Figure className="text-base leading-6 font-semibold">
                {usage ? `$${usage.cost_usd.toFixed(4)}` : '—'}
              </Figure>
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
          <Receipt
            origin="GET /metrics · fleet · this process"
            detail="the run figures come from the run_finished event on the open stream"
          />
        </div>
      </CardBody>
    </Card>
  )
}