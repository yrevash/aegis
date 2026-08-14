'use client'

import type { ReactElement } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { chartHex } from '@/components/charts/palette'
import { Badge } from '@/components/ui/Badge'
import type { ForecastBurndown } from '@/lib/api/types'

const usd = (v: number): string => `$${v.toFixed(2)}`

/** Render an ISO timestamp as `dd MMM`. */
function dayLabel(iso: string): string {
  const d = new Date(iso)
  return `${d.getUTCDate()} ${d.toLocaleString('en', { month: 'short', timeZone: 'UTC' })}`
}

/**
 * The budget burn-down: cumulative projected spend climbing towards the cap.
 *
 * A percentage bar tells an operator that 58% of the month's budget is gone. This
 * tells them the date it runs out — the only form of the answer they can act on.
 *
 * The shaded envelope around the cumulative curve is the per-step interval bounds
 * summed. That is **not** a calibrated interval on a cumulative total (the sum of
 * marginal quantiles is not the quantile of the sum), and the panel says so in
 * plain words rather than letting the shading imply a coverage guarantee.
 */
export function BurndownPanel({ burndown }: { burndown: ForecastBurndown }): ReactElement {
  const ml = chartHex('ml')
  const rows = burndown.points.map((p) => ({
    label: dayLabel(p.ts),
    cumulative: p.cumulative,
    envelope: [p.cumulative_lo, p.cumulative_hi] as [number, number],
  }))
  const over = burndown.exhausted_within_horizon

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <Fact label="spent this window" value={usd(burndown.spent_usd)} />
        <Fact
          label="cap"
          value={burndown.limit_usd == null ? 'no cap set' : usd(burndown.limit_usd)}
        />
        <Fact label="projected total" value={usd(burndown.projected_total_usd)} />
        <Fact
          label={over ? 'projected to run out' : 'headroom'}
          value={
            over && burndown.exhaustion_ts
              ? dayLabel(burndown.exhaustion_ts)
              : burndown.headroom_usd == null
                ? '—'
                : usd(burndown.headroom_usd)
          }
          tone={over ? 'risk' : 'ok'}
        />
      </div>

      {burndown.limit_usd != null ? (
        <div className="flex flex-wrap items-center gap-2">
          <Badge tone={over ? 'risk' : 'ok'}>
            {over
              ? `cap reached at step ${burndown.exhaustion_step} of ${burndown.points.length}`
              : `cap holds through the ${burndown.points.length}-step horizon`}
          </Badge>
          <span className="tabular font-mono text-[0.68rem] text-muted-foreground">
            {burndown.window} window · {burndown.scope}
            {burndown.scope_id == null ? '' : ` ${burndown.scope_id}`}
          </span>
        </div>
      ) : (
        <p className="text-[0.74rem] text-muted-foreground">
          No cap is configured for this scope, so the curve is projected but never crosses
          anything.
        </p>
      )}

      <ResponsiveContainer width="100%" height={220}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            minTickGap={28}
          />
          <YAxis
            tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            cursor={{ stroke: 'var(--border)' }}
            content={<ChartTooltip valueFormatter={usd} />}
          />
          <Area
            dataKey="envelope"
            stroke="none"
            fill={ml}
            fillOpacity={0.15}
            isAnimationActive={false}
            tooltipType="none"
          />
          <Line
            type="monotone"
            name="cumulative"
            dataKey="cumulative"
            stroke={ml}
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {burndown.limit_usd != null ? (
            <ReferenceLine
              y={burndown.limit_usd}
              stroke={chartHex('block')}
              strokeDasharray="4 3"
              label={{
                value: `cap ${usd(burndown.limit_usd)}`,
                position: 'insideTopRight',
                fill: chartHex('block'),
                fontSize: 10,
                fontFamily: 'var(--font-mono)',
              }}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>

      <p className="text-[0.7rem] leading-snug text-muted-foreground">
        The shaded envelope is the per-step {burndown.interval_method} bounds added up. That is
        an envelope, not a calibrated interval on the total — consecutive forecast errors are
        correlated, so the sum of marginal bounds carries no coverage guarantee. The point curve
        is the projection to act on.
      </p>
    </div>
  )
}

function Fact({
  label,
  value,
  tone,
}: {
  label: string
  value: string
  tone?: 'risk' | 'ok'
}): ReactElement {
  return (
    <div
      className="rounded-xl border p-3.5"
      style={{
        borderColor: tone ? `var(--${tone})` : 'var(--border)',
        background: tone
          ? `color-mix(in srgb, var(--${tone}) 8%, transparent)`
          : 'color-mix(in srgb, var(--surface-2) 40%, transparent)',
      }}
    >
      <span className="eyebrow">{label}</span>
      <p className="t-title tabular mt-1 font-mono text-[1rem] font-semibold text-foreground">
        {value}
      </p>
    </div>
  )
}
