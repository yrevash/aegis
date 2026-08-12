'use client'

import type { ReactElement } from 'react'

import {
  conformalDomain,
  conformalSignals,
  formatValue,
  toPercent,
} from '@/components/ml/conformalScale'

interface ConformalBandProps {
  /** The point prediction (numeric for a band; string for a class label). */
  prediction: number | string
  /** Calibrated `[lo, hi]` bounds, or null for a non-interval prediction. */
  interval: [number, number] | null
  /** Target coverage rate, e.g. 0.9. */
  confidence: number | null
  /** Interval width (upper − lower); the regression deferral signal. */
  intervalWidth: number | null
  /** Prediction-set size; the classification deferral signal. */
  setSize: number | null
  /** Optional unit suffix for the figures (e.g. `'h'`). */
  unit?: string
}

/**
 * A calibrated conformal interval drawn as a band on a number line: the shaded
 * region spans exactly the true `[lo, hi]` bounds, a marker pins the point
 * prediction, and both endpoints are labelled in bold ink. The domain is
 * derived from the data (never a hardcoded 0..1 track) so non-probability
 * targets fill the width instead of collapsing against an edge. Falls back to a
 * set-size readout when the prediction has no numeric interval.
 */
export function ConformalBand({
  prediction,
  interval,
  confidence,
  intervalWidth,
  setSize,
  unit,
}: ConformalBandProps): ReactElement {
  const coverage = confidence != null ? `${Math.round(confidence * 100)}%` : null
  const signals = conformalSignals(intervalWidth, setSize, unit)

  const numeric = typeof prediction === 'number' && interval != null
  if (!numeric) {
    // Classification / no-interval: show the point prediction + set-size signal.
    return (
      <div className="flex flex-col gap-2">
        <BandHeader coverage={coverage} />
        <div className="flex items-baseline gap-2">
          <span
            className="tabular font-mono text-[1.2rem] font-bold tracking-tight"
            style={{ color: 'var(--ml-ink)' }}
          >
            {String(prediction)}
          </span>
          {signals.setLabel && (
            <span className="tabular font-mono text-[0.7rem] text-muted-foreground">
              {signals.setLabel}
            </span>
          )}
        </div>
      </div>
    )
  }

  const point = prediction
  const domain = conformalDomain(interval, point)
  const loPct = toPercent(Math.min(interval[0], interval[1]), domain)
  const hiPct = toPercent(Math.max(interval[0], interval[1]), domain)
  const pointPct = toPercent(point, domain)

  return (
    <div className="flex flex-col gap-3">
      <BandHeader coverage={coverage} />

      {/* Prediction value above the track */}
      <div className="flex items-baseline gap-2">
        <span
          className="tabular font-mono text-[1.35rem] font-bold leading-none tracking-tight"
          style={{ color: 'var(--ml-ink)' }}
        >
          {formatValue(point, unit)}
        </span>
        <span className="tabular font-mono text-[0.72rem] text-muted-foreground">
          {formatValue(Math.min(interval[0], interval[1]), unit)} –{' '}
          {formatValue(Math.max(interval[0], interval[1]), unit)}
          {signals.widthLabel ? ` · ${signals.widthLabel}` : ''}
        </span>
      </div>

      {/* The band track */}
      <div className="relative h-7">
        <div className="absolute inset-x-0 top-1/2 h-px -translate-y-1/2 bg-border" />
        <div
          className="absolute top-1/2 h-4 -translate-y-1/2 rounded-sm"
          style={{
            left: `${loPct}%`,
            width: `${Math.max(hiPct - loPct, 1)}%`,
            background: 'var(--ml)',
            border: '1px solid var(--ml-ink)',
          }}
        />
        <div
          className="absolute top-1/2 h-6 w-[2.5px] -translate-y-1/2 rounded-full"
          style={{ left: `calc(${pointPct}% - 1.25px)`, background: 'var(--ml-ink)' }}
        />
      </div>

      {/* Endpoint labels aligned under the band */}
      <div className="relative h-4">
        <span
          className="tabular absolute top-0 -translate-x-1/2 font-mono text-[0.66rem] font-semibold text-foreground"
          style={{ left: `${loPct}%` }}
        >
          {formatValue(Math.min(interval[0], interval[1]), unit)}
        </span>
        <span
          className="tabular absolute top-0 -translate-x-1/2 font-mono text-[0.66rem] font-semibold text-foreground"
          style={{ left: `${hiPct}%` }}
        >
          {formatValue(Math.max(interval[0], interval[1]), unit)}
        </span>
      </div>

      <p className="text-[0.7rem] leading-snug text-muted-foreground">
        {coverage ? (
          <>
            Calibrated to contain the true value{' '}
            <span className="font-semibold text-foreground">{coverage}</span> of the
            time. A wider band means the model is less certain.
          </>
        ) : (
          'Calibrated bounds around the prediction. A wider band means less certainty.'
        )}
      </p>
    </div>
  )
}

function BandHeader({ coverage }: { coverage: string | null }): ReactElement {
  return (
    <div className="flex items-center justify-between">
      <span className="eyebrow flex items-center gap-1.5">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: 'var(--ml-ink)' }}
        />
        conformal · calibrated confidence
      </span>
      {coverage && (
        <span
          className="tabular font-mono text-[0.66rem] font-semibold"
          style={{ color: 'var(--ml-ink)' }}
        >
          {coverage} coverage
        </span>
      )}
    </div>
  )
}