'use client'

import type { ReactElement, ReactNode } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceDot,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { chartHex } from '@/components/charts/palette'
import { InfoTip } from '@/components/primitives/InfoTip'
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
 * tells them the date it runs out — the only form of the answer they can act on,
 * so that date is the headline and the chart is drawn to say the same thing: the
 * cap is a solid rule with the over-cap region tinted behind it, and the crossing
 * is marked on the curve where it happens.
 *
 * The shaded envelope around the cumulative curve is the per-step interval bounds
 * summed. That is **not** a calibrated interval on a cumulative total (the sum of
 * marginal quantiles is not the quantile of the sum). The claim stays on the
 * legend where the envelope is named; the reasoning behind it is one hover away.
 */
export function BurndownPanel({ burndown }: { burndown: ForecastBurndown }): ReactElement {
  const ml = chartHex('ml')
  const block = chartHex('block')
  const rows = burndown.points.map((p) => ({
    label: dayLabel(p.ts),
    cumulative: p.cumulative,
    envelope: [p.cumulative_lo, p.cumulative_hi] as [number, number],
  }))
  const over = burndown.exhausted_within_horizon
  const limit = burndown.limit_usd
  const capped = limit != null

  // A fixed top so the over-cap region can be drawn as a real rectangle rather
  // than relying on the axis auto-domain, which moves as the horizon changes.
  const top =
    Math.max(limit ?? 0, ...burndown.points.map((p) => p.cumulative_hi), burndown.spent_usd) * 1.06

  const crossing = over
    ? (burndown.points.find((p) => p.ts === burndown.exhaustion_ts) ?? null)
    : null

  return (
    <div className="space-y-4">
      {/* The headline: a date if the cap is reached, otherwise the room left. */}
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-3">
        <div>
          <p className="eyebrow">
            {!capped ? 'no cap configured' : over ? 'projected to run out' : 'cap holds'}
          </p>
          <p
            className="tabular mt-1 font-mono text-[1.75rem] leading-none font-bold"
            style={{ color: over ? 'var(--danger)' : 'var(--foreground)' }}
          >
            {!capped
              ? usd(burndown.projected_total_usd)
              : over && burndown.exhaustion_ts
                ? dayLabel(burndown.exhaustion_ts)
                : burndown.headroom_usd == null
                  ? '—'
                  : usd(burndown.headroom_usd)}
          </p>
          <p className="mt-1.5 text-[0.7rem] text-muted-foreground">
            {!capped
              ? 'projected total for the window — the curve is drawn but crosses nothing'
              : over
                ? `step ${burndown.exhaustion_step} of ${burndown.points.length} of the horizon`
                : `headroom left after the ${burndown.points.length}-step horizon`}
            {' · '}
            <span className="tabular font-mono">
              {burndown.window} window · {burndown.scope}
              {burndown.scope_id == null ? '' : ` ${burndown.scope_id}`}
            </span>
          </p>
        </div>

        <dl className="flex flex-wrap items-end gap-x-7 gap-y-3">
          <Fact label="spent this window">{usd(burndown.spent_usd)}</Fact>
          <Fact label="cap">{capped ? usd(limit) : '—'}</Fact>
          <Fact label="projected total">{usd(burndown.projected_total_usd)}</Fact>
        </dl>
      </div>

      <ResponsiveContainer width="100%" height={230}>
        <ComposedChart data={rows} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
          <XAxis
            dataKey="label"
            tick={{
              fill: 'var(--muted-foreground)',
              fontSize: 11,
              fontFamily: 'var(--font-mono)',
            }}
            axisLine={false}
            tickLine={false}
            minTickGap={28}
          />
          <YAxis
            domain={[0, Math.ceil(top)]}
            tick={{
              fill: 'var(--muted-foreground)',
              fontSize: 11,
              fontFamily: 'var(--font-mono)',
            }}
            axisLine={false}
            tickLine={false}
            width={52}
          />
          <Tooltip
            cursor={{ stroke: 'var(--border)' }}
            content={<ChartTooltip valueFormatter={usd} />}
          />
          {/* Everything above the cap is overspend — tinted so the rule reads as
              a boundary rather than as one more line on the chart. */}
          {capped ? (
            <ReferenceArea y1={limit} y2={Math.ceil(top)} fill={block} fillOpacity={0.07} />
          ) : null}
          <Area
            dataKey="envelope"
            stroke="none"
            fill={ml}
            fillOpacity={0.14}
            isAnimationActive={false}
            tooltipType="none"
          />
          <Line
            type="monotone"
            name="cumulative"
            dataKey="cumulative"
            stroke={ml}
            strokeWidth={2.5}
            dot={false}
            isAnimationActive={false}
          />
          {capped ? (
            <ReferenceLine
              y={limit}
              stroke={block}
              strokeWidth={2}
              label={{
                value: `cap ${usd(limit)}`,
                position: 'insideTopRight',
                fill: block,
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'var(--font-mono)',
              }}
            />
          ) : null}
          {crossing ? (
            <ReferenceLine
              x={dayLabel(crossing.ts)}
              stroke={block}
              strokeDasharray="3 3"
              label={{
                value: `runs out ${dayLabel(crossing.ts)}`,
                position: 'insideBottomLeft',
                fill: block,
                fontSize: 11,
                fontWeight: 600,
                fontFamily: 'var(--font-mono)',
              }}
            />
          ) : null}
          {crossing ? (
            <ReferenceDot
              x={dayLabel(crossing.ts)}
              y={crossing.cumulative}
              r={4}
              fill={block}
              stroke="var(--card)"
              strokeWidth={2}
            />
          ) : null}
        </ComposedChart>
      </ResponsiveContainer>

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[0.7rem] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="h-0.5 w-4 rounded-full" style={{ background: ml }} />
          projected cumulative — the curve to act on
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            className="h-2.5 w-4 rounded-[3px]"
            style={{ background: `${ml}1f`, border: `1px solid ${ml}4d` }}
          />
          summed {burndown.interval_method} bounds — an envelope,{' '}
          <span className="font-medium text-foreground">not calibrated</span>
          <InfoTip label="Why the envelope is not a calibrated interval">
            The envelope adds the per-step {burndown.interval_method} bounds together. Consecutive
            forecast errors are correlated, and the sum of marginal quantiles is not the quantile of
            the sum, so this band carries no coverage guarantee on the cumulative total.
          </InfoTip>
        </span>
        {capped ? (
          <span className="inline-flex items-center gap-1.5">
            <span className="h-0.5 w-4 rounded-full" style={{ background: block }} />
            cap {usd(limit)}
          </span>
        ) : null}
      </div>
    </div>
  )
}

/** One supporting number: a label above the figure, no box around it. */
function Fact({ label, children }: { label: string; children: ReactNode }): ReactElement {
  return (
    <div>
      <dt className="eyebrow">{label}</dt>
      <dd className="tabular mt-1 font-mono text-[0.95rem] font-semibold text-foreground">
        {children}
      </dd>
    </div>
  )
}
