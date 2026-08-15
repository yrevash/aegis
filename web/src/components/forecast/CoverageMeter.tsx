'use client'

import type { ReactElement } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge } from '@/components/ui/Badge'
import type { ForecastResult } from '@/lib/api/types'

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`

/** Clamp to [0,1] so a malformed rate can never draw a bar outside its track. */
const unit = (v: number): number => Math.max(0, Math.min(1, v))

/**
 * Requested vs achieved interval coverage, on one track.
 *
 * These are the two numbers this whole surface exists to keep apart: the
 * requested level is an *input* the interval was asked to satisfy, the achieved
 * rate is the only one that is *evidence*. Printing either alone — or worse,
 * printing the request in the achieved number's place — is the overclaim the
 * module was corrected for.
 *
 * Drawing them on a shared 0–100% track makes the gap literal: the fill stops
 * short of the requested marker, so a reader sees the shortfall before reading
 * a single digit. It sits directly under the chart because that is where the
 * band it describes is drawn.
 */
export function CoverageMeter({ result }: { result: ForecastResult }): ReactElement {
  const bt = result.backtest
  const met = bt.coverage_meets_request
  const achieved = unit(bt.empirical_coverage)
  const requested = unit(bt.requested_coverage)
  const inside = Math.round(bt.empirical_coverage * bt.n_points)
  const tone = met ? 'ok' : 'risk'

  return (
    <div
      className="rounded-xl border p-4"
      style={{
        borderColor: `var(--${tone})`,
        background: `color-mix(in srgb, var(--${tone}) 7%, transparent)`,
      }}
    >
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <div className="flex items-center gap-1.5">
          <span className="eyebrow">interval coverage · measured</span>
          <InfoTip label="How coverage is measured">
            {pct(bt.requested_coverage)} is an input — the level the band was asked to contain.{' '}
            {pct(bt.empirical_coverage)} is what it achieved on data held out chronologically: every
            band was fitted only on points strictly earlier than the ones it was scored on, so no
            future value reached the calibration set.
          </InfoTip>
        </div>
        <Badge tone={tone}>{met ? 'meets request' : 'below request'}</Badge>
      </div>

      <div className="mt-1.5 flex flex-wrap items-baseline gap-x-2.5 gap-y-0.5">
        <span className="tabular font-mono text-[1.6rem] leading-none font-bold text-foreground">
          {pct(bt.empirical_coverage)}
        </span>
        <span className="text-[0.74rem] text-muted-foreground">
          achieved, against{' '}
          <span className="tabular font-mono text-foreground">{pct(bt.requested_coverage)}</span>{' '}
          requested
        </span>
      </div>

      {/* One 0–100% track: the fill is what was measured, the tick is what was asked. */}
      <div className="relative mt-3 h-2 rounded-full bg-surface-2" aria-hidden>
        <div
          className="h-2 rounded-full"
          style={{ width: `${achieved * 100}%`, background: `var(--${tone})` }}
        />
        <div
          className="absolute -top-1 -bottom-1 w-px bg-foreground"
          style={{ left: `${requested * 100}%` }}
        />
      </div>

      <div className="mt-1.5 flex flex-wrap items-baseline justify-between gap-x-4 gap-y-0.5 text-[0.68rem] text-muted-foreground">
        <span className="tabular font-mono">
          {inside} of {bt.n_points} held-out actuals inside the band · {bt.windows} rolling-origin
          windows
        </span>
        <span className="tabular font-mono">↑ requested {pct(bt.requested_coverage)}</span>
      </div>
    </div>
  )
}
