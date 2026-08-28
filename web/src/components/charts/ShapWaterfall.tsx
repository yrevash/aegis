'use client'

import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import type { ShapFeature } from '@/lib/stream'

import { buildWaterfall, waterfallPercent } from './waterfallLayout'

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

/**
 * Colour a raises/lowers segment by direction (not good/bad).
 *
 * This is the one **diverging** scale in the console, and it was drawn in red and
 * green — the single worst pair a diverging scale can use, because red/green is
 * exactly the axis most colour-vision deficiency runs along. `scripts/validate_palette.js`
 * puts numbers on it: `#12b76a ↔ #d92d20` separates by only **ΔE 10.9 under
 * deuteranopia** (against ΔE 35.2 for normal vision — so a deutan reader loses
 * two thirds of the signal), and the green also warns at 2.55:1 against the
 * surface, below the 3:1 mark floor.
 *
 * DESIGN.md §2 already specified the replacement — *diverging = blue ↔ warm* —
 * and this file's own docstring already claimed "raises are warm, lowers are
 * cool"; only the implementation had drifted. The specified pair validates at
 * **ΔE 31.1 under protanopia** with every check passing, contrast included.
 *
 * Red and green also carried a verdict this scale must not carry: a SHAP driver
 * that pushes a prediction up is not a *bad* driver, and green/red is read as
 * good/bad before it is read as up/down. Direction never rests on hue alone
 * regardless — every row prints a signed figure beside its bar.
 */
function hue(raises: boolean): string {
  return raises ? 'var(--risk-ink)' : 'var(--blue-600)'
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
            style={{ background: 'var(--blue-800)' }}
          />
          SHAP · why this prediction
        </span>
        <Figure className="text-[0.6875rem] leading-4 text-muted-foreground">
          base {fmt(base, unit)}
        </Figure>
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
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[0.74rem] text-foreground"
                  translate="no"
                >
                  {step.feature}
                </span>
                <Figure className="shrink-0 text-[0.6875rem] leading-4 text-muted-foreground">
                  {fmt(step.value)}
                </Figure>
              </div>

              {/* The bar restates the signed figure printed beside it. */}
              <div className="relative h-3.5" aria-hidden>
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

              <span className="w-16 text-right" style={{ color: hue(step.raises) }}>
                <Figure className="text-[0.8rem] leading-5 font-semibold">
                  {step.raises ? '+' : '−'}
                  {fmt(Math.abs(step.contribution), unit)}
                </Figure>
              </span>
            </div>
          )
        })}
      </div>

      <div className="flex items-center justify-between border-t border-border pt-2">
        <div className="flex items-center gap-2">
          <span className="eyebrow text-foreground">prediction</span>
          {hidden > 0 && (
            <Figure className="text-[0.6875rem] leading-4 text-muted-foreground">
              +{hidden} more driver{hidden > 1 ? 's' : ''}
            </Figure>
          )}
        </div>
        <Figure size="stat" className="text-blue-800">
          {fmt(prediction, unit)}
        </Figure>
      </div>
    </div>
  )
}