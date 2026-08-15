'use client'

import type { ReactElement, ReactNode } from 'react'

import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge } from '@/components/ui/Badge'
import type { ForecastResult } from '@/lib/api/types'

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`

/**
 * What the rolling-origin backtest measured, and which candidates lost.
 *
 * Coverage is deliberately *not* repeated here: requested vs achieved is drawn
 * once, on the `CoverageMeter` directly under the band it describes. This panel
 * carries the rest of the evidence — the error metrics on held-out points, and
 * the candidate table including the seasonal-naive baseline, so a reader can see
 * the winner actually beat something rather than being declared the winner.
 *
 * Per-candidate coverage stays in the table: there it is a comparison between
 * models, not a second claim about the shipped band.
 */
export function BacktestPanel({ result }: { result: ForecastResult }): ReactElement {
  const bt = result.backtest

  return (
    <div className="space-y-4">
      {/* One hairline-divided strip, not five separate boxes. */}
      <div className="grid grid-cols-2 gap-px overflow-hidden rounded-xl border border-border bg-border sm:grid-cols-5">
        <Metric label="sMAPE" value={`${bt.smape.toFixed(2)}%`} />
        <Metric label="MAPE" value={bt.mape == null ? '—' : `${bt.mape.toFixed(2)}%`}>
          {bt.mape == null ? (
            <InfoTip label="Why MAPE is undefined">
              At least one held-out actual is zero, so the percentage error is undefined. Reported
              as undefined rather than as a very large number.
            </InfoTip>
          ) : null}
        </Metric>
        <Metric label="MAE" value={bt.mae.toFixed(3)} />
        <Metric label="held-out points" value={String(bt.n_points)} />
        <Metric label="windows" value={String(bt.windows)} />
      </div>

      {/* Candidates, losers included */}
      <div>
        <div className="mb-2 flex items-center gap-1.5">
          <p className="eyebrow">candidates · selected on {result.selection_metric}</p>
          {result.model_selected_on_backtest_windows ? (
            <InfoTip label="How the winner was chosen">
              The winner was chosen using the same rolling-origin windows these figures come from,
              which makes them a mildly optimistic in-selection estimate. Stated rather than hidden.
            </InfoTip>
          ) : null}
        </div>
        <div className="overflow-x-auto rounded-xl border border-border">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border text-left text-muted-foreground">
                <th className="px-4 py-2 font-medium">model</th>
                <th className="px-4 py-2 text-right font-medium">sMAPE</th>
                <th className="px-4 py-2 text-right font-medium">MAE</th>
                <th className="px-4 py-2 text-right font-medium">coverage</th>
              </tr>
            </thead>
            <tbody>
              {result.candidates.map((c) => (
                <tr
                  key={c.model}
                  className="border-b border-border last:border-0"
                  style={
                    c.selected
                      ? {
                          background: 'color-mix(in srgb, var(--ml) 10%, transparent)',
                        }
                      : undefined
                  }
                >
                  <td className="px-4 py-2 font-mono text-foreground">
                    {c.model}
                    {c.selected ? (
                      <Badge tone="ml" className="ml-2">
                        selected
                      </Badge>
                    ) : null}
                  </td>
                  <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                    {c.smape.toFixed(2)}%
                  </td>
                  <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                    {c.mae.toFixed(3)}
                  </td>
                  <td className="tabular px-4 py-2 text-right font-mono text-foreground">
                    {pct(c.empirical_coverage)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      {result.excluded_models.length > 0 ? (
        <ul className="space-y-1">
          {result.excluded_models.map((e) => (
            <li key={e.model} className="text-[0.72rem] text-muted-foreground">
              <span className="font-mono text-foreground">{e.model}</span> — excluded: {e.reason}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

function Metric({
  label,
  value,
  children,
}: {
  label: string
  value: string
  children?: ReactNode
}): ReactElement {
  return (
    <div className="bg-card p-3.5">
      <span className="eyebrow inline-flex items-center gap-1">
        {label}
        {children}
      </span>
      <p className="t-title tabular mt-1 font-mono text-[0.95rem] font-semibold text-foreground">
        {value}
      </p>
    </div>
  )
}
