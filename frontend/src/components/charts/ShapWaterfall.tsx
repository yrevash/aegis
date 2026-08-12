import type { ReactElement } from 'react'

import { cn } from '@/lib/utils'
import type { ShapFeature } from '@/types/stream'

import { buildWaterfall, waterfallPercent } from './shapWaterfall'

interface ShapWaterfallProps {
  /** The model's base/expected value the walk starts from. */
  base: number
  /** Signed SHAP attributions (any order — sorted by magnitude internally). */
  features: ShapFeature[]
  /** The authoritative model prediction, shown as the final row. */
  prediction: number
  /** Optional unit suffix for the figures (e.g. `'h'`). */
  unit?: string
  /** Optional cap on how many drivers to draw (default: all). */
  maxRows?: number
}

/** Colour a raises/lowers segment by direction (not good/bad). */
function hue(raises: boolean): string {
  return raises ? 'var(--danger)' : 'var(--success)'
}

function fmt(value: number, unit?: string): string {
  const abs = Math.abs(value)
  const decimals = abs >= 100 ? 0 : abs >= 10 ? 1 : 2
  return `${value.toFixed(decimals)}${unit ?? ''}`
}

/**
 * A complete SHAP waterfall: base → each signed driver → prediction, drawn as a
 * cumulative bar walk. Each driver's segment starts where the previous total
 * left off; raises are warm, lowers are cool. Every figure lives in an aligned
 * tabular-mono right column so nothing is ever clipped inside a bar.
 */
export function ShapWaterfall({
  base,
  features,
  prediction,
  unit,
  maxRows,
}: ShapWaterfallProps): ReactElement {
  const wf = buildWaterfall(base, features)
  const rows = maxRows != null ? wf.steps.slice(0, maxRows) : wf.steps
  const hidden = wf.steps.length - rows.length

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <span className="eyebrow flex items-center gap-1.5">
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: 'var(--ml-ink)' }}
          />
          SHAP · why this prediction
        </span>
        <span className="tabular font-mono text-[0.66rem] text-muted-foreground">
          base {fmt(base, unit)}
        </span>
      </div>

      <div className="flex flex-col divide-y divide-border/60">
        {rows.map((step) => {
          const lo = Math.min(step.start, step.end)
          const hiPct = waterfallPercent(Math.max(step.start, step.end), wf.domain)
          const loPct = waterfallPercent(lo, wf.domain)
          return (
            <div
              key={step.feature}
              className="grid grid-cols-[minmax(0,1fr)_1.7fr_auto] items-center gap-3 py-1.5"
            >
              <div className="flex min-w-0 items-baseline gap-1.5">
                <span className="min-w-0 flex-1 truncate font-mono text-[0.74rem] text-foreground">
                  {step.feature}
                </span>
                <span className="tabular shrink-0 font-mono text-[0.62rem] text-muted-foreground">
                  {fmt(step.value)}
                </span>
              </div>

              <div className="relative h-3.5">
                <div className="absolute inset-y-0 left-0 right-0 rounded-sm bg-muted/60" />
                <div
                  className="absolute inset-y-0 rounded-sm"
                  style={{
                    left: `${loPct}%`,
                    width: `${Math.max(hiPct - loPct, 1.5)}%`,
                    background: hue(step.raises),
                  }}
                />
              </div>

              <span
                className="tabular w-16 text-right font-mono text-[0.8rem] font-semibold tracking-tight"
                style={{ color: hue(step.raises) }}
              >
                {step.raises ? '+' : '−'}
                {fmt(Math.abs(step.contribution), unit)}
              </span>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t border-border pt-2">
        <div className="flex items-center gap-2">
          <span className="eyebrow text-foreground">prediction</span>
          {hidden > 0 && (
            <span className="tabular font-mono text-[0.62rem] text-muted-foreground">
              +{hidden} more driver{hidden > 1 ? 's' : ''}
            </span>
          )}
        </div>
        <span
          className={cn(
            'tabular font-mono text-[1.1rem] font-bold tracking-tight',
          )}
          style={{ color: 'var(--ml-ink)' }}
        >
          {fmt(prediction, unit)}
        </span>
      </div>
    </div>
  )
}
