'use client'

import { useEffect, useState } from 'react'

import { getPublicMetrics } from '@/lib/api/client'
import type { PublicMetricsResponse } from '@/lib/api/types'

/**
 * Measured platform figures from the public `GET /platform/public-metrics`.
 *
 * Numbers only — the explanatory sentence under each tile has been dropped. A
 * figure with a label is read; a figure with a paragraph is skipped.
 *
 * Every value is measured or absent. Where nothing has been measured the tile
 * says so rather than printing a zero: an unmeasured p95 shows an em dash, not
 * "0 ms". A fabricated zero and a real zero are different claims.
 *
 * The endpoint publishes ratios and counts only; absolute cost figures stay
 * behind auth, because a public page cannot answer "on what workload?".
 */

const pct = (n: number): string => `${Math.round(n * 100)}%`

export function MetricsStrip() {
  const [m, setM] = useState<PublicMetricsResponse | null>(null)

  useEffect(() => {
    let live = true
    getPublicMetrics()
      .then((r) => live && setM(r))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [])

  // Unreachable backend ⇒ render nothing rather than invent figures.
  if (m === null) return null

  const tiles: [string, string | null][] = [
    ['Cache hit rate', pct(m.cache_hit_rate)],
    ['Small-model share', pct(m.small_model_share)],
    ['p95 run latency', m.p95_latency_ms === null ? null : `${Math.round(m.p95_latency_ms)}ms`],
    ['Actions approved', m.actions_approved.toLocaleString()],
    ['Model calls', m.total_calls.toLocaleString()],
  ]

  return (
    <section className="border-b border-border bg-surface-2">
      <div className="mx-auto max-w-6xl px-6 py-16">
        <div className="flex flex-col gap-8 lg:flex-row lg:items-center lg:justify-between">
          <div className="shrink-0">
            <p className="eyebrow mb-2">Measured, not claimed</p>
            <h2 className="max-w-xs text-2xl font-semibold tracking-tight text-foreground">
              Read live from the running platform.
            </h2>
          </div>

          <dl className="grid grid-cols-2 gap-x-10 gap-y-7 sm:grid-cols-3 lg:grid-cols-5 lg:gap-x-12">
            {tiles.map(([label, value]) => (
              <div key={label}>
                <dd className="font-mono text-[1.9rem] font-medium leading-none tracking-tight text-foreground tabular-nums">
                  {value ?? <span className="text-muted-foreground">&mdash;</span>}
                </dd>
                <dt className="mt-2 font-mono text-[0.62rem] uppercase tracking-[0.09em] text-muted-foreground">
                  {label}
                </dt>
              </div>
            ))}
          </dl>
        </div>
      </div>
    </section>
  )
}
