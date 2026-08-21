'use client'

import { CheckCircle2, XCircle } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'

/** One metric laid against its own bar. Every field comes from `EvalMetricConfig`. */
export interface BulletDatum {
  /** Readable metric name. */
  name: string
  /** One-line gloss, relocated into the row's ⓘ rather than onto the page. */
  gloss?: string
  /** The measured reading, 0..1. */
  value: number
  /** The gate's bar for this metric, 0..1. */
  threshold: number
  /** Which side of the bar counts as good. */
  higherIsBetter: boolean
  /** The gate's own verdict for this metric. */
  passed: boolean
  /** How many cases the reading averages over. */
  cases: number
}

/** Where the axis puts a tick, as a fraction of the 0..1 domain. */
const TICKS = [0, 0.25, 0.5, 0.75, 1] as const

const pct = (v: number): string => `${(v * 100).toFixed(1)}%`
const clamp = (v: number): number => Math.min(1, Math.max(0, v))

/**
 * Value-against-threshold for every gated metric, as **bullet bars**.
 *
 * DESIGN.md §2 names this form explicitly and names why the alternatives lose.
 * A radial gauge is right for exactly one bounded value and *"fails
 * spectacularly when intended for comparison"*, so a row of six of them is the
 * wrong instrument for six metrics. A ranked bar list has no target to read
 * against, and this screen's whole question is *did the reading clear its own
 * bar* — a question that is unanswerable without the bar drawn.
 *
 * So: a bar one third the thickness of its container, a perpendicular target
 * line at the threshold, and a shared 0–100% axis at the foot that every row is
 * measured against. Two qualitative bands, one hue at two intensities, darker on
 * the failing side of the bar — which survives colour-blindness because it is
 * lightness and not hue, and which is backed anyway by the icon-and-word verdict
 * printed on the row.
 *
 * A metric with no reading is not passed in here at all: the caller states its
 * absence instead of drawing a bar at zero, which would read as a measured zero.
 */
export function MetricBullets({
  data,
  label,
}: {
  data: readonly BulletDatum[]
  label: string
}): ReactElement {
  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-4" aria-label={label}>
        {data.map((d) => {
          const value = clamp(d.value)
          const threshold = clamp(d.threshold)
          // The "worse" band is whichever side of the bar the gate counts against.
          const badFrom = d.higherIsBetter ? 0 : threshold
          const badTo = d.higherIsBetter ? threshold : 1
          return (
            <li key={d.name} className="flex flex-col gap-1.5">
              <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
                <span className="inline-flex min-w-0 items-center gap-1.5">
                  <span className="truncate text-[0.82rem] font-medium text-foreground" title={d.name}>
                    {d.name}
                  </span>
                  {d.gloss ? <InfoTip label={`What ${d.name} measures`}>{d.gloss}</InfoTip> : null}
                </span>
                <span className="flex shrink-0 items-baseline gap-2.5">
                  <Figure
                    className={d.passed ? 'text-[0.82rem] leading-5 font-semibold' : 'text-[0.82rem] leading-5 font-semibold text-danger'}
                  >
                    {pct(d.value)}
                  </Figure>
                  <span className="text-[0.7rem] text-muted-foreground">
                    bar {d.higherIsBetter ? '≥' : '≤'}{' '}
                    <Figure className="text-[0.7rem] leading-4">{pct(d.threshold)}</Figure> ·{' '}
                    <Figure className="text-[0.7rem] leading-4">n={d.cases}</Figure>
                  </span>
                  <span
                    className={
                      d.passed
                        ? 'inline-flex items-center gap-1 text-[0.7rem] font-medium text-[color:var(--ok-ink)]'
                        : 'inline-flex items-center gap-1 text-[0.7rem] font-medium text-block-ink'
                    }
                  >
                    {d.passed ? (
                      <CheckCircle2 className="size-3.5" aria-hidden />
                    ) : (
                      <XCircle className="size-3.5" aria-hidden />
                    )}
                    {d.passed ? 'pass' : 'fail'}
                  </span>
                </span>
              </div>

              {/* The container: two qualitative bands, the gridlines the axis
                  labels, the measure at one third thickness, the target line. */}
              <div className="relative h-6 w-full overflow-hidden rounded-sm bg-surface-2">
                <div
                  className="absolute inset-y-0 bg-[color:var(--border)]"
                  style={{ left: `${badFrom * 100}%`, width: `${Math.max(badTo - badFrom, 0) * 100}%` }}
                  aria-hidden
                />
                {TICKS.slice(1, -1).map((t) => (
                  <div
                    key={t}
                    className="absolute inset-y-0 w-px bg-card/70"
                    style={{ left: `${t * 100}%` }}
                    aria-hidden
                  />
                ))}
                <div
                  className="absolute top-1/2 left-0 h-2 -translate-y-1/2 rounded-[2px]"
                  style={{
                    width: `${Math.max(value * 100, 0.6)}%`,
                    background: d.passed ? 'var(--blue-600)' : 'var(--block-ink)',
                  }}
                  aria-hidden
                />
                <div
                  className="absolute inset-y-0 w-[2px] bg-foreground"
                  style={{ left: `calc(${threshold * 100}% - 1px)` }}
                  aria-hidden
                />
              </div>
            </li>
          )
        })}
      </ul>

      {/* The shared axis. Every track above spans exactly this domain. */}
      <div className="relative h-4 w-full" aria-hidden>
        {TICKS.map((t) => (
          <span
            key={t}
            className="tabular absolute top-0 font-mono text-[0.66rem] text-muted-foreground"
            style={{
              left: `${t * 100}%`,
              transform: t === 0 ? 'none' : t === 1 ? 'translateX(-100%)' : 'translateX(-50%)',
            }}
          >
            {t * 100}%
          </span>
        ))}
      </div>

      <p className="flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[0.68rem] text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-2 w-4 rounded-[2px] bg-[color:var(--blue-600)]" aria-hidden />
          reading
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3.5 w-[2px] bg-foreground" aria-hidden />
          threshold
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span className="inline-block h-3 w-4 rounded-[2px] bg-[color:var(--border)]" aria-hidden />
          failing side
        </span>
      </p>
    </div>
  )
}
