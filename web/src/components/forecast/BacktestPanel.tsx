'use client'

import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import type { ForecastResult } from '@/lib/api/types'

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`

/**
 * The backtest, reported the only way it is worth reporting: requested coverage
 * and achieved coverage side by side, never merged.
 *
 * The two numbers almost always differ, and the gap is the finding. A surface that
 * printed "90% coverage" from `requested_coverage` would be stating an input as
 * though it were a measurement — the exact overclaim the ML module was corrected
 * for. Here the achieved rate gets the big type and a pass/miss badge, and the
 * requested level is demoted to context.
 *
 * The candidate table publishes the losers, including the seasonal-naive baseline,
 * so a reader can see the winner actually beat something.
 */
export function BacktestPanel({ result }: { result: ForecastResult }): ReactElement {
  const bt = result.backtest
  const met = bt.coverage_meets_request

  return (
    <div className="space-y-5">
      {/* Coverage: asked for vs achieved */}
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="rounded-xl border border-border bg-surface-2/40 p-4">
          <span className="eyebrow">coverage requested</span>
          <p className="tabular mt-1.5 font-mono text-[1.5rem] leading-none font-bold text-muted-foreground">
            {pct(bt.requested_coverage)}
          </p>
          <p className="mt-2 text-[0.7rem] leading-snug text-muted-foreground">
            An input. This is what the interval was asked to contain, not what it did.
          </p>
        </div>
        <div
          className="rounded-xl border p-4"
          style={{
            borderColor: met ? 'var(--ok)' : 'var(--risk)',
            background: met ? 'color-mix(in srgb, var(--ok) 8%, transparent)' : 'color-mix(in srgb, var(--risk) 8%, transparent)',
          }}
        >
          <div className="flex items-center justify-between gap-2">
            <span className="eyebrow">coverage achieved</span>
            <Badge tone={met ? 'ok' : 'risk'}>{met ? 'meets request' : 'below request'}</Badge>
          </div>
          <p className="tabular mt-1.5 font-mono text-[1.5rem] leading-none font-bold text-foreground">
            {pct(bt.empirical_coverage)}
          </p>
          <p className="mt-2 text-[0.7rem] leading-snug text-muted-foreground">
            Measured: {Math.round(bt.empirical_coverage * bt.n_points)} of {bt.n_points} held-out
            actuals fell inside the band, across {bt.windows} rolling-origin windows.
          </p>
        </div>
      </div>

      {/* Error metrics on held-out points */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Metric label="sMAPE" value={`${bt.smape.toFixed(2)}%`} />
        <Metric label="MAPE" value={bt.mape == null ? 'undefined' : `${bt.mape.toFixed(2)}%`} />
        <Metric label="MAE" value={bt.mae.toFixed(3)} />
        <Metric label="held-out points" value={String(bt.n_points)} />
      </div>
      {bt.mape == null ? (
        <p className="text-[0.7rem] leading-snug text-muted-foreground">
          MAPE is undefined here because at least one held-out actual is zero — reported as
          undefined rather than as a very large number.
        </p>
      ) : null}

      {/* Candidates, losers included */}
      <div>
        <p className="eyebrow mb-2">candidates · selected on {result.selection_metric}</p>
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
                  style={c.selected ? { background: 'color-mix(in srgb, var(--ml) 10%, transparent)' } : undefined}
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
        <div>
          <p className="eyebrow mb-2">excluded candidates</p>
          <ul className="space-y-1">
            {result.excluded_models.map((e) => (
              <li key={e.model} className="text-[0.74rem] text-muted-foreground">
                <span className="font-mono text-foreground">{e.model}</span> — {e.reason}
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      {result.model_selected_on_backtest_windows ? (
        <p className="text-[0.7rem] leading-snug text-muted-foreground">
          The winner was chosen using the same rolling-origin windows these figures come from,
          which makes them a mildly optimistic in-selection estimate. Stated rather than hidden.
        </p>
      ) : null}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="rounded-xl border border-border bg-surface-2/40 p-3.5">
      <span className="eyebrow">{label}</span>
      <p className="t-title tabular mt-1 font-mono text-[0.95rem] font-semibold text-foreground">
        {value}
      </p>
    </div>
  )
}
