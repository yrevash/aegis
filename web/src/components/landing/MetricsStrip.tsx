'use client'

import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { getPublicMetrics } from '@/lib/api/client'
import type { PublicMetricsResponse } from '@/lib/api/types'

import { LandingSection } from './LandingSection'

/**
 * Measured platform figures from the public `GET /platform/public-metrics`.
 *
 * Every value is measured or absent. Where nothing has been measured the tile
 * **says so in the slot the number would have occupied** — DESIGN.md §1 — rather
 * than printing a dash a reader has to decode, and certainly rather than a zero:
 * a fabricated zero and a real zero are different claims.
 *
 * The figures are set in {@link Figure} at `stat`, not at `display`. Five
 * hero-sized numerals on a page that already has a hero headline is the
 * hierarchy failure DESIGN.md §3 names — the strip is five equal facts, and
 * making all five enormous makes none of them the point.
 *
 * The endpoint publishes ratios and counts only; absolute cost figures stay
 * behind auth, because a public page cannot answer "on what workload?".
 */

const pct = (n: number): string => `${Math.round(n * 100)}%`

/** One published figure, or the honest reason there is not one. */
interface Tile {
  label: string
  value: string | null
  /** What is missing, said plainly, when `value` is null. */
  absent?: string
}

export function MetricsStrip(): ReactElement | null {
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

  const tiles: Tile[] = [
    { label: 'Cache hit rate', value: pct(m.cache_hit_rate) },
    { label: 'Small-model share', value: pct(m.small_model_share) },
    {
      label: 'p95 run latency',
      value: m.p95_latency_ms === null ? null : `${Math.round(m.p95_latency_ms)}ms`,
      absent: 'no run timed yet',
    },
    { label: 'Actions approved', value: m.actions_approved.toLocaleString() },
    { label: 'Model calls', value: m.total_calls.toLocaleString() },
  ]

  return (
    <LandingSection
      eyebrow="Measured, not claimed"
      title="Read live from the running platform."
      note="These five are counted by the platform itself and served from a public endpoint. A figure nothing has measured yet says so instead of showing a zero."
    >
      <dl className="grid grid-cols-2 gap-x-8 gap-y-8 sm:grid-cols-3 lg:grid-cols-5">
        {tiles.map((tile) => (
          <div key={tile.label} className="min-w-0 border-t border-border pt-4">
            <dt className="text-[0.8125rem] leading-5 text-muted-foreground">{tile.label}</dt>
            <dd className="mt-2">
              {tile.value === null ? (
                <span className="text-[0.8125rem] leading-7 text-muted-foreground italic">
                  {tile.absent ?? 'not measured yet'}
                </span>
              ) : (
                <Figure size="stat" className="text-foreground">
                  {tile.value}
                </Figure>
              )}
            </dd>
          </div>
        ))}
      </dl>

      <Receipt
        origin="GET /platform/public-metrics"
        detail="counted by the platform, served unauthenticated"
        className="mt-8"
      />
    </LandingSection>
  )
}
