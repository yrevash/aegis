'use client'

import { CircleSlash, DatabaseZap, Loader2, RefreshCw, Trash2, Zap } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { getCacheStats, pipelineMessage, type CacheRow, type CacheStatsResponse } from '@/lib/api/pipeline'
import { useAuth } from '@/lib/auth/AuthContext'

/** How often the counters are re-read, so a hit lands on the screen while you watch. */
const POLL_MS = 5000

/** Render a count with thousands separators. */
function count(n: number): string {
  return n.toLocaleString()
}

/** A value the backend said it does not have, rendered as such rather than as zero. */
function orAbsent(value: number | string | null, suffix = ''): string {
  return value === null ? 'not reported' : `${value}${suffix}`
}

/** One label/value row of a cache's registered configuration. */
function ConfigRow({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="eyebrow shrink-0 text-[0.58rem]">{label}</dt>
      <dd className="min-w-0 text-right text-[0.8rem] leading-snug text-foreground">{value}</dd>
    </div>
  )
}

/**
 * A single-hue magnitude meter for one cache's hit rate.
 *
 * One hue, one measure, a direct label — there is nothing categorical here to colour
 * by, so nothing is. An unmeasured rate draws no bar at all rather than an empty one
 * that reads as 0%.
 */
function HitRateMeter({ value }: { value: number | null }): ReactElement {
  if (value === null) {
    return (
      <p className="text-sm text-muted-foreground">
        No lookup has reached this cache in this process, so it has no hit rate to report.
      </p>
    )
  }
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow text-[0.58rem]">hit rate</span>
        <span className="tabular text-sm font-semibold text-foreground">{pct.toFixed(1)}%</span>
      </div>
      <div
        className="mt-1.5 h-2 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`Hit rate ${pct.toFixed(1)} percent`}
      >
        <div
          className="h-full rounded-full bg-blue-400 transition-[width] duration-500 motion-reduce:transition-none"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  )
}

/** One cache's card: what it is, what the live instance was built as, what it did. */
function CacheCard({ row }: { row: CacheRow }): ReactElement {
  return (
    <Card>
      <CardHeader
        eyebrow={row.key}
        title={row.name}
        actions={
          row.registered ? (
            <Badge tone="graph">{row.backend ?? 'unknown backend'}</Badge>
          ) : (
            <Badge tone="neutral" className="gap-1.5">
              <CircleSlash className="size-3" />
              not built here
            </Badge>
          )
        }
      />
      <CardBody>
        <p className="text-sm leading-snug text-muted-foreground">{row.holds}</p>
        <p className="mt-1 font-mono text-[0.7rem] leading-snug text-muted-foreground">
          {row.method}
        </p>

        <div className="mt-4">
          <HitRateMeter value={row.hit_rate} />
        </div>

        <dl className="mt-3 divide-y divide-border/60 border-t border-border/60">
          <ConfigRow label="lookups" value={count(row.lookups)} />
          <ConfigRow label="hits / misses" value={`${count(row.hits)} / ${count(row.misses)}`} />
          <ConfigRow label="writes" value={count(row.writes)} />
          <ConfigRow
            label="evictions"
            value={
              row.evictions === null
                ? 'none this process can observe'
                : count(row.evictions)
            }
          />
          <ConfigRow
            label="entries"
            value={row.entries === null ? 'not counted for this backend' : count(row.entries)}
          />
          <ConfigRow
            label="ttl"
            value={row.ttl_seconds === null ? 'no expiry written' : `${row.ttl_seconds}s`}
          />
          <ConfigRow
            label="threshold"
            value={row.threshold === null ? 'exact key — no similarity' : `cosine ≥ ${row.threshold}`}
          />
          <ConfigRow label="max entries" value={orAbsent(row.capacity)} />
        </dl>

        {row.registered ? null : (
          <p className="mt-3 text-[0.72rem] leading-snug text-muted-foreground">
            No instance of this cache has been constructed in the API process, so its
            configuration is unknown rather than defaulted.
          </p>
        )}
      </CardBody>
    </Card>
  )
}

/**
 * Cache — the live counters, read from the caches themselves.
 *
 * **What changed and why.** This screen used to render a hand-written array of
 * configuration under a heading that reads like a measurement. Every cache now
 * increments a counter on the exact branch that decided hit or miss
 * (`aegis.core.cache_stats`), and this page renders those counters and the
 * configuration the *live instance* registered. A cache nobody constructed says so; a
 * cache nobody has read says so; a figure nothing records is on the "not recorded"
 * card with the emission that would create it — never zero-filled into a tile.
 *
 * The totals are sums of counts, never an average of rates: the exact-hash tiers and
 * the cosine tiers do not decide the same event, so one blended hit-rate headline
 * would be a number about nothing.
 */
function CacheView(): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [data, setData] = useState<CacheStatsResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    setRefreshing(true)
    try {
      const next = await getCacheStats(token)
      setData(next)
      setError(null)
    } catch (err) {
      setError(pipelineMessage(err, 'Could not read the cache counters.'))
    } finally {
      setRefreshing(false)
    }
  }, [token])

  useEffect(() => {
    // Wait for the persisted session to hydrate; fetching now would send no bearer.
    if (!hydrated) return
    let alive = true
    const tick = (): void => {
      if (alive) void load()
    }
    tick()
    // Polled rather than one-shot: the whole claim of this page is that the numbers
    // move under load, and a figure you have to reload the tab to see does not
    // demonstrate that.
    const timer = setInterval(tick, POLL_MS)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [hydrated, load])

  const totals = data
    ? data.caches.reduce(
        (acc, row) => ({
          lookups: acc.lookups + row.lookups,
          hits: acc.hits + row.hits,
          writes: acc.writes + row.writes,
          evictions: acc.evictions + (row.evictions ?? 0),
        }),
        { lookups: 0, hits: 0, writes: 0, evictions: 0 },
      )
    : null

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow mb-1">counted inside the caches</p>
          <h1 className="t-hero text-foreground">Cache</h1>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-2 rounded-lg border border-border bg-card px-3 py-1.5 text-sm text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[color:var(--ring)]"
        >
          <RefreshCw
            className={
              refreshing
                ? 'size-4 animate-spin motion-reduce:animate-none'
                : 'size-4'
            }
          />
          Refresh
        </button>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : data === null || totals === null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin motion-reduce:animate-none" />
              Reading the cache counters…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard label="Lookups" value={count(totals.lookups)} icon={DatabaseZap} tone="graph" />
            <StatCard label="Hits" value={count(totals.hits)} icon={Zap} tone="ok" />
            <StatCard label="Writes" value={count(totals.writes)} icon={DatabaseZap} />
            <StatCard label="Entries evicted" value={count(totals.evictions)} icon={Trash2} tone="risk" />
          </div>

          <p className="text-[0.72rem] leading-snug text-muted-foreground">
            {data.caveat} Source: {data.source}. Read at{' '}
            {new Date(data.generated_at).toLocaleTimeString()}, and again every{' '}
            {POLL_MS / 1000} seconds.
          </p>

          <div className="grid gap-4 lg:grid-cols-3">
            {data.caches.map((row) => (
              <CacheCard key={row.key} row={row} />
            ))}
          </div>

          <Card>
            <CardHeader
              eyebrow="stated, not filled in"
              title="What this page does not measure"
            />
            <CardBody>
              <dl className="divide-y divide-border/60">
                {data.not_recorded.map((gap) => (
                  <div key={gap.figure} className="py-3 first:pt-0 last:pb-0">
                    <dt className="text-sm font-medium text-foreground">{gap.figure}</dt>
                    <dd className="mt-1 text-[0.8rem] leading-snug text-muted-foreground">
                      {gap.why}
                      <span className="block pt-1 text-foreground">Needs: {gap.needs}</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </CardBody>
          </Card>
        </>
      )}
    </div>
  )
}

/** Client entry for the Cache section — gated on a reachable backend. */
export function CacheMount(): ReactElement {
  return (
    <BackendGate>
      <TooltipProvider>
        <CacheView />
      </TooltipProvider>
    </BackendGate>
  )
}
