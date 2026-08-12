'use client'

import type { ReactElement } from 'react'

import { conformalDomain, conformalSignals, formatValue, toPercent } from './conformalScale'

interface ConformalIntervalProps {
  prediction: number
  interval: [number, number]
  confidence: number | null
  /**
   * Optional explicit track domain. When omitted (the normal case) the domain
   * is auto-scaled from the interval and prediction so the band renders
   * correctly for ANY numeric target, not just `0..1` probabilities.
   */
  domain?: [number, number]
  /** Optional unit label for the axis figures, e.g. `'h'`. */
  unit?: string
  /** Render at a reduced height for embedding (e.g. inside the approval card). */
  compact?: boolean
  /**
   * Conformal interval width (the deferral signal), or null. Surfaced as a
   * caption when present — only shown honestly when the model emitted it.
   */
  width?: number | null
  /** Conformal prediction-set size, or null. Surfaced alongside the width. */
  setSize?: number | null
}

/**
 * Visualises a conformal prediction interval: the calibrated band with
 * guaranteed coverage, and the point prediction marked within it. This is the
 * "uncertainty-bounded" half of the trust stack made visual.
 *
 * The track domain auto-scales to the data (interval bounds + prediction, with
 * ~12% padding) so a 0–80h regression fills the track just as a 0–1 probability
 * does — previously it was pinned to `[0, 1]` and any non-probability target
 * collapsed against the right edge.
 */
export function ConformalInterval({
  prediction,
  interval,
  confidence,
  domain,
  unit,
  compact = false,
  width = null,
  setSize = null,
}: ConformalIntervalProps): ReactElement {
  const track = domain ?? conformalDomain(interval, prediction)
  const [lo, hi] = interval
  const loPct = toPercent(lo, track)
  const hiPct = toPercent(hi, track)
  const { widthLabel, setLabel } = conformalSignals(width, setSize, unit)

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="eyebrow">Conformal interval</span>
        {confidence != null && (
          <span className="tabular font-mono text-[0.72rem] text-ml-ink">
            {Math.round(confidence * 100)}% coverage
          </span>
        )}
      </div>
      <div className={compact ? 'relative h-6' : 'relative h-9'}>
        {/* Track */}
        <div className="absolute top-1/2 h-1.5 w-full -translate-y-1/2 rounded-full bg-surface-2" />
        {/* Calibrated band */}
        <div
          className="absolute top-1/2 h-1.5 -translate-y-1/2 rounded-full bg-ml/50"
          style={{ left: `${Math.min(loPct, hiPct)}%`, width: `${Math.abs(hiPct - loPct)}%` }}
        />
        {/* Point prediction */}
        <div
          className="absolute top-1/2 size-3.5 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-card bg-ml ring-2 ring-ml/30"
          style={{ left: `${toPercent(prediction, track)}%` }}
          title={`prediction ${formatValue(prediction, unit)}`}
        />
      </div>
      {/* lo/hi anchor the domain ends; `p =` tracks the dot by its own percent
          so it stays under the prediction on asymmetric / padded domains. */}
      <div className="relative mt-1 font-mono text-[0.68rem] text-muted-foreground">
        <div className="flex justify-between">
          <span className="tabular">{formatValue(lo, unit)}</span>
          <span className="tabular">{formatValue(hi, unit)}</span>
        </div>
        <span
          className="tabular absolute top-0 -translate-x-1/2 whitespace-nowrap text-foreground"
          style={{ left: `${toPercent(prediction, track)}%` }}
        >
          p = {formatValue(prediction, unit)}
        </span>
      </div>
      {(widthLabel || setLabel) && (
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 font-mono text-[0.66rem] text-muted-foreground">
          {widthLabel && (
            <span className="tabular rounded bg-ml/[0.08] px-1.5 py-0.5 text-ml-ink" title="Conformal interval width — the deferral signal">
              {widthLabel}
            </span>
          )}
          {setLabel && (
            <span className="tabular rounded bg-ml/[0.08] px-1.5 py-0.5 text-ml-ink" title="Conformal prediction-set size">
              {setLabel}
            </span>
          )}
        </div>
      )}
    </div>
  )
}