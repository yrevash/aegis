'use client'

import { ArrowRight } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { getPublicMetrics } from '@/lib/api/client'
import type { PublicMetricsResponse } from '@/lib/api/types'
import { cn } from '@/lib/utils'

import { LandingScene } from './LandingScene'
import { LandingSection } from './LandingSection'
import { useCapabilities } from './useCapabilities'

/**
 * The two things on this page that are read from the running platform.
 *
 * These used to be two sections — a module grid and a metrics strip — separated
 * by three sections of static copy, which buried the single most checkable claim
 * the page makes. Together they are one exhibit: *this is what the backend said
 * when the page loaded, and here is the endpoint it said it on.*
 *
 * **The count is read, not written.** The old heading said "Twelve modules" in
 * prose while the grid below it rendered whatever the manifest held — which was
 * fifteen. A hardcoded number in front of a live list is the exact drift this
 * product exists to make impossible, and it had already happened on the page
 * arguing that it could not.
 *
 * Neither block invents anything when its endpoint does not answer — but neither
 * does it go quiet. Rendering nothing left a section whose own lead promised two
 * blocks and then showed empty space, which reads as a bug rather than as a
 * refusal. So an unreachable endpoint states its absence in the slot the block
 * would have occupied, which is the same discipline DESIGN.md §1 asks of a figure
 * and is the more convincing state of the two: a page willing to report its own
 * backend as unreachable is a page not running on fixtures.
 */

const pct = (n: number): string => `${Math.round(n * 100)}%`

/** One published figure, or the honest reason there is not one. */
interface Tile {
  label: string
  value: string | null
  /** What is missing, said plainly, when `value` is null. */
  absent?: string
}

export function LivePlatform(): ReactElement {
  const manifest = useCapabilities()

  return (
    <LandingSection
      id="live"
      tone="surface"
      eyebrow="Read live"
      title="Nothing on this page is a fixture."
      lead="Both blocks below are fetched from public endpoints on load. There is no demo mode behind them."
    >
      {manifest.state === 'up' ? (
        <div className="grid gap-x-12 gap-y-8 lg:grid-cols-[minmax(0,3fr)_minmax(0,9fr)] lg:items-start">
          <LandingScene name="liveData" width={280} className="mx-auto lg:mx-0" />

          <div className="min-w-0">
            <h3 className="font-display text-lg leading-6 font-semibold text-foreground">
              {manifest.data.module_count} modules, and what each one actually runs on
            </h3>
            <p className="mt-2 max-w-[62ch] text-pretty text-sm leading-relaxed text-muted-foreground">
              Both read from the manifest, so neither can drift from what is installed.
            </p>

            <ul className="mt-5 grid gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-2">
              {manifest.data.modules.map((module, index) => (
                <li
                  key={module.name}
                  className={cn(
                    'min-w-0 bg-surface px-5 py-4',
                    // An odd manifest left a tinted half-row of nothing at the end
                    // of the grid, which reads as a module that failed to render.
                    // The last card takes the whole row instead.
                    index === manifest.data.modules.length - 1 &&
                      manifest.data.modules.length % 2 === 1 &&
                      'sm:col-span-2',
                  )}
                >
                  <h4 className="truncate text-sm font-semibold tracking-[-0.01em] text-foreground">
                    {module.name.replace(/^Aegis /, '')}
                  </h4>
                  <p className="tabular truncate font-mono text-[0.7rem] text-blue-700">
                    {module.tech}
                  </p>
                </li>
              ))}
            </ul>

            <Receipt
              origin="GET /platform/capabilities"
              detail="public, unauthenticated"
              className="mt-5"
            />
          </div>
        </div>
      ) : manifest.state === 'down' ? (
        <Absence
          figure="The module list"
          why="the capability manifest did not answer, and this page holds no copy of it"
          needed="a reachable backend — the list is the platform's own, never typed here"
        />
      ) : null}

      <MetricRow className="mt-14" />

      <AdapterSeam className="mt-14" />
    </LandingSection>
  )
}

/** The five files a new domain costs. */
const ADAPTER = ['schema', 'tools', 'prompts', 'ML target', 'corpus'] as const

/**
 * The join between two boxes of the seam.
 *
 * Hidden below `sm`, where the boxes stack and reading order already carries the
 * direction; an arrow rotated a quarter turn would only be a second way of
 * saying "next". Decorative either way — the labels are the information.
 */
function Seam(): ReactElement {
  return (
    <ArrowRight
      aria-hidden
      className="hidden size-4 shrink-0 self-center text-muted-foreground sm:block"
      strokeWidth={2}
    />
  )
}

/**
 * What porting Aegis to a new domain costs, as a seam rather than a paragraph.
 *
 * It was three lines of prose naming five things, which is a list wearing a
 * sentence. Drawn, the claim is also *sharper*: the seam is visible, the five
 * chips are countable, and "the core never learns the domain" is a label on the
 * left box rather than an assertion at the end of a paragraph.
 */
function AdapterSeam({ className }: { className?: string }): ReactElement {
  return (
    <figure className={className}>
      <figcaption className="text-[0.9375rem] leading-relaxed font-semibold text-foreground">
        The core is a package you import, not an application you fork.
      </figcaption>

      <div className="mt-4 grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto_minmax(0,1.2fr)_auto_minmax(0,1fr)] sm:items-stretch">
        <div className="min-w-0 rounded-lg border border-border bg-surface-2 px-4 py-3.5">
          <p className="text-sm font-medium text-foreground">Everything above</p>
          <p className="mt-1 text-[0.8125rem] leading-5 text-muted-foreground">
            domain-agnostic, and it stays that way
          </p>
        </div>

        <Seam />

        <div className="min-w-0 rounded-lg border border-blue-200 bg-blue-50 px-4 py-3.5">
          <p className="eyebrow mb-2">One adapter</p>
          <ul className="flex flex-wrap gap-1.5">
            {ADAPTER.map((part) => (
              <li
                key={part}
                className="tabular rounded-full bg-surface px-2.5 py-1 font-mono text-[0.7rem] text-blue-700"
              >
                {part}
              </li>
            ))}
          </ul>
        </div>

        <Seam />

        <div className="min-w-0 rounded-lg border border-border bg-surface-2 px-4 py-3.5">
          <p className="text-sm font-medium text-foreground">Your domain</p>
          <p className="mt-1 text-[0.8125rem] leading-5 text-muted-foreground">
            the core never learns it
          </p>
        </div>
      </div>
    </figure>
  )
}

/**
 * The five public figures, or the honest absence in the slot each would occupy.
 *
 * Set at `stat`, not `display`: five hero-sized numerals on a page that already
 * has a hero headline is the hierarchy failure DESIGN.md §3 names — these are
 * five equal facts, and making all five enormous makes none of them the point.
 *
 * The endpoint publishes ratios and counts only. Absolute cost figures stay
 * behind auth, because a public page cannot answer "on what workload?".
 */
function MetricRow({ className }: { className?: string }): ReactElement | null {
  const [metrics, setMetrics] = useState<PublicMetricsResponse | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let live = true
    getPublicMetrics()
      .then((response) => live && setMetrics(response))
      .catch(() => live && setFailed(true))
    return () => {
      live = false
    }
  }, [])

  if (failed) {
    return (
      <div className={className}>
        <Absence
          figure="The five published figures"
          why="the public metrics endpoint did not answer"
          needed="a reachable backend — these are counted by the platform as it runs, not stored on this page"
        />
      </div>
    )
  }

  if (metrics === null) return null

  const tiles: Tile[] = [
    { label: 'Cache hit rate', value: pct(metrics.cache_hit_rate) },
    { label: 'Small-model share', value: pct(metrics.small_model_share) },
    {
      label: 'p95 run latency',
      value:
        metrics.p95_latency_ms === null
          ? null
          : `${Math.round(metrics.p95_latency_ms).toLocaleString()}ms`,
      absent: 'no run timed yet',
    },
    { label: 'Actions approved', value: metrics.actions_approved.toLocaleString() },
    { label: 'Model calls', value: metrics.total_calls.toLocaleString() },
  ]

  return (
    <div className={className}>
      <h3 className="font-display text-lg leading-6 font-semibold text-foreground">
        Counted by the platform itself
      </h3>
      <p className="mt-2 max-w-[62ch] text-pretty text-sm leading-relaxed text-muted-foreground">
        A figure nothing has measured says so, rather than showing a zero.
      </p>

      <dl className="mt-6 grid grid-cols-2 gap-x-8 gap-y-7 sm:grid-cols-3 lg:grid-cols-5">
        {tiles.map((tile) => (
          <div key={tile.label} className="min-w-0 border-t border-border pt-3.5">
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
        detail="public, unauthenticated"
        className="mt-5"
      />
    </div>
  )
}
