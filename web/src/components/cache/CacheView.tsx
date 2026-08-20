'use client'

import { CircleSlash, DatabaseZap, RefreshCw, Trash2, Zap } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import {
  getCacheStats,
  pipelineMessage,
  type CacheRow,
  type CacheStatsResponse,
} from '@/lib/api/pipeline'
import { useAuth } from '@/lib/auth/AuthContext'

/** How often the counters are re-read, so a hit lands on the screen while you watch. */
const POLL_MS = 5000

/** Render a count with thousands separators. */
function count(n: number): string {
  return n.toLocaleString()
}

/**
 * Sum a counter across the caches, **without inventing the ones that do not have it**.
 *
 * `evictions` is `null` for a cache that cannot evict, and the previous total read
 * `acc + (row.evictions ?? 0)` — which silently turned "this cache does not evict" into
 * "this cache evicted nothing" and rolled it into a headline figure. That is the exact
 * zero-fill this screen exists to refuse: a hardcoded `SPECS` array of fictional numbers
 * was deleted from here for the same reason, and the fix must not reappear one reduce
 * further down.
 *
 * So the total carries its own denominator: how many caches actually reported, out of
 * how many there are. A total over three of six caches is a different fact from a total
 * over six, and the tile is not allowed to blur them.
 *
 * @returns The sum over reporting caches, and the counts on both sides of the question.
 */
function sumReported(
  rows: CacheRow[],
  pick: (row: CacheRow) => number | null,
): { total: number; reporting: number; silent: number } {
  let total = 0
  let reporting = 0
  for (const row of rows) {
    const value = pick(row)
    if (value === null) continue
    total += value
    reporting += 1
  }
  return { total, reporting, silent: rows.length - reporting }
}

/** One label/value row of a cache's registered configuration. */
function ConfigRow({ label, value }: { label: string; value: ReactElement | string }): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="eyebrow shrink-0">{label}</dt>
      <dd className="min-w-0 text-right text-[0.8125rem] leading-relaxed text-foreground">
        {value}
      </dd>
    </div>
  )
}

/** A measured configuration value, or the reason there is not one. Never a zero. */
function OrAbsent({ value }: { value: number | string | null }): ReactElement {
  if (value === null) {
    return <span className="text-muted-foreground italic">not reported</span>
  }
  return <Figure>{value}</Figure>
}

/**
 * A single-hue magnitude meter for one cache's hit rate.
 *
 * One hue, one measure, a direct label — there is nothing categorical here to colour
 * by, so nothing is. An unmeasured rate draws **no bar at all** and says so in a
 * sentence: a hit rate is null before the first lookup, and an empty track next to
 * "0.0%" would be a measurement this process has not made.
 */
function HitRateMeter({
  value,
  lookups,
}: {
  value: number | null
  lookups: number
}): ReactElement {
  if (value === null) {
    return (
      <p className="flex items-center gap-1.5 text-[0.8125rem] text-muted-foreground">
        <CircleSlash className="size-3.5 shrink-0" aria-hidden />
        No hit rate yet
        <InfoTip label="Why there is no hit rate">
          No lookup has reached this cache in this process, so it has no hit rate to report. An
          empty track next to “0.0%” would be a measurement this process has not made.
        </InfoTip>
      </p>
    )
  }
  const pct = Math.max(0, Math.min(1, value)) * 100
  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <span className="eyebrow">hit rate</span>
        <span className="flex items-baseline gap-2">
          <Figure size="stat" className="text-foreground">{`${pct.toFixed(1)}%`}</Figure>
          <Figure className="text-muted-foreground">{`of ${count(lookups)}`}</Figure>
        </span>
      </div>
      <div
        className="mt-1.5 h-1.5 w-full overflow-hidden rounded-sm bg-surface-2"
        role="img"
        aria-label={`Hit rate ${pct.toFixed(1)} percent`}
      >
        <div
          className="h-full rounded-sm bg-blue-600 transition-[width] duration-[--dur-slow] motion-reduce:transition-none"
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
        title={
          <span className="flex items-center gap-1.5">
            {row.name}
            {/* What the cache holds was a paragraph on every card; it is the
                mechanism, so DESIGN.md §4 puts it one layer down. */}
            <InfoTip label={`What ${row.name} holds`}>{row.holds}</InfoTip>
          </span>
        }
        actions={
          row.registered ? (
            <Badge tone="graph">{row.backend ?? 'unknown backend'}</Badge>
          ) : (
            <Badge tone="neutral" className="gap-1.5">
              <CircleSlash className="size-3 shrink-0" aria-hidden />
              not built here
            </Badge>
          )
        }
      />
      <CardBody>
        <HitRateMeter value={row.hit_rate} lookups={row.lookups} />

        <dl className="mt-4 divide-y divide-border border-t border-border">
          <ConfigRow label="lookups" value={<Figure>{count(row.lookups)}</Figure>} />
          <ConfigRow
            label="hits / misses"
            value={<Figure>{`${count(row.hits)} / ${count(row.misses)}`}</Figure>}
          />
          <ConfigRow label="writes" value={<Figure>{count(row.writes)}</Figure>} />
          <ConfigRow
            label="evictions"
            value={
              row.evictions === null ? (
                <span className="text-muted-foreground italic">
                  none this process can observe
                </span>
              ) : (
                <Figure>{count(row.evictions)}</Figure>
              )
            }
          />
          <ConfigRow
            label="entries"
            value={
              row.entries === null ? (
                <span className="text-muted-foreground italic">not counted for this backend</span>
              ) : (
                <Figure>{count(row.entries)}</Figure>
              )
            }
          />
          <ConfigRow
            label="ttl"
            value={
              row.ttl_seconds === null ? (
                <span className="text-muted-foreground italic">no expiry written</span>
              ) : (
                <Figure unit="s">{row.ttl_seconds}</Figure>
              )
            }
          />
          <ConfigRow
            label="threshold"
            value={
              row.threshold === null ? (
                <span className="text-muted-foreground italic">exact key, no similarity</span>
              ) : (
                <Figure>{`cosine ≥ ${row.threshold}`}</Figure>
              )
            }
          />
          <ConfigRow label="max entries" value={<OrAbsent value={row.capacity} />} />
        </dl>

        {row.registered ? null : (
          <Absence
            className="mt-3"
            figure="This cache's configuration"
            why="No instance of it has been constructed in the API process."
            needed="Something has to build the cache before its TTL, threshold and capacity are facts rather than defaults."
          />
        )}

        <Receipt origin={row.method} className="mt-4" />
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
 * would be a number about nothing. And they are sums over the caches that actually
 * report the counter — see {@link sumReported} for the zero-fill that used to hide in
 * the eviction total.
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

  const rows = data?.caches ?? []
  const lookups = sumReported(rows, (row) => row.lookups)
  const hits = sumReported(rows, (row) => row.hits)
  const writes = sumReported(rows, (row) => row.writes)
  const evictions = sumReported(rows, (row) => row.evictions)

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="counted inside the caches"
        title="Cache"
        actions={
          <>
          <InfoTip label="Where these counters come from">
            Every counter here is incremented on the exact branch that decided hit or miss, inside
            the cache itself. A cache nobody built, and a cache nobody has read, each say so rather
            than reporting a zero. The totals are sums of counts, never an average of rates — the
            exact-hash tiers and the cosine tiers do not decide the same event.
          </InfoTip>
          <button
            type="button"
            onClick={() => void load()}
            className="inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none transition-colors duration-[--dur-fast] hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
          >
            <RefreshCw
              aria-hidden
              className={
                refreshing ? 'size-4 animate-spin motion-reduce:animate-none' : 'size-4'
              }
            />
            Refresh
            <span className="sr-only" role="status">
              {refreshing ? 'Re-reading the cache counters' : ''}
            </span>
          </button>
          </>
        }
      />

      {error ? (
        <Card>
          <CardBody>
            <ErrorState error={error} retry={() => void load()} />
          </CardBody>
        </Card>
      ) : data === null ? (
        <Card>
          <CardBody>
            <LoadingState rows={4} label="Reading the cache counters…" />
          </CardBody>
        </Card>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
            <StatCard
              label="Lookups"
              value={count(lookups.total)}
              icon={DatabaseZap}
              tone="graph"
              source={`summed over ${lookups.reporting} of ${rows.length} caches`}
            />
            <StatCard
              label="Hits"
              value={count(hits.total)}
              icon={Zap}
              tone="ok"
              source={`summed over ${hits.reporting} of ${rows.length} caches`}
            />
            <StatCard
              label="Writes"
              value={count(writes.total)}
              icon={DatabaseZap}
              source={`summed over ${writes.reporting} of ${rows.length} caches`}
            />
            {/* The tile that used to lie. A non-evicting cache reports `null`, and
                adding it in as a zero produced a platform-wide eviction total that
                counted caches which cannot evict at all. */}
            {evictions.reporting === 0 ? (
              <Absence
                figure="Entries evicted"
                why="Not one of these caches reports evictions: none of them evicts, or none has an eviction counter this process can read."
                needed="A cache that evicts has to increment on the branch that drops the entry, the way the hit and miss counters already do."
              />
            ) : (
              <StatCard
                label="Entries evicted"
                value={count(evictions.total)}
                icon={Trash2}
                tone="risk"
                source={
                  evictions.silent === 0
                    ? `summed over all ${rows.length} caches`
                    : `summed over ${evictions.reporting} of ${rows.length} caches · ${evictions.silent} do not evict`
                }
              />
            )}
          </div>

          <Receipt
            origin={data.source}
            detail={`${data.caveat} Read at ${new Date(
              data.generated_at,
            ).toLocaleTimeString()}, and again every ${POLL_MS / 1000} seconds.`}
          />

          <Card>
            <CardHeader
              eyebrow="which tier is earning its keep"
              title="Traffic and hit rate, side by side"
              actions={
                <InfoTip label="How to read this">
                  Each row is one cache. The track&apos;s length is that cache&apos;s share of all
                  lookups this process has made, so a tier nothing asks stays visibly short; the
                  filled part is the share of its own lookups that hit. A cache with no lookups
                  draws no track — there is nothing measured to draw.
                </InfoTip>
              }
            />
            <CardBody>
              <CacheTrafficBars rows={rows} totalLookups={lookups.total} />
            </CardBody>
          </Card>

          <div className="grid gap-4 lg:grid-cols-2 xl:grid-cols-3">
            {rows.map((row) => (
              <CacheCard key={row.key} row={row} />
            ))}
          </div>

          <Card>
            <CardHeader
              eyebrow="stated, not filled in"
              title="What this page does not measure"
            />
            <CardBody className="grid gap-3 md:grid-cols-2">
              {data.not_recorded.map((gap) => (
                <Absence
                  key={gap.figure}
                  figure={gap.figure}
                  why={gap.why}
                  needed={gap.needs}
                />
              ))}
            </CardBody>
          </Card>
        </>
      )}
    </div>
  )
}

/**
 * The caches compared on the two facts that decide whether one is worth keeping:
 * **how much traffic it takes** and **how much of that traffic it answers**.
 *
 * A grid of per-cache cards gives each tier equal visual weight regardless of
 * whether anything asks it, which is the opposite of the truth — a semantic tier
 * fielding four lookups and an exact tier fielding forty thousand look identical
 * as cards. Here the track length *is* the share of lookups, so the tier carrying
 * the load is the long row, and the fill is its own hit rate. One hue, two
 * intensities; the words carry everything the fill does.
 *
 * Nothing is scaled against an invented denominator: a cache with zero lookups
 * draws no track and says so.
 */
function CacheTrafficBars({
  rows,
  totalLookups,
}: {
  rows: CacheRow[]
  totalLookups: number
}): ReactElement {
  if (rows.length === 0 || totalLookups <= 0) {
    return (
      <Absence
        figure="Lookup traffic per cache"
        why="No lookup has reached any cache in this process yet, so there are no shares to compare."
        needed="Run a query. The counters increment on the branch that decides hit or miss."
      />
    )
  }
  return (
    <ul className="flex flex-col gap-3">
      {rows.map((row) => {
        const share = (row.lookups / totalLookups) * 100
        const hitPct = row.lookups > 0 ? (row.hits / row.lookups) * 100 : 0
        return (
          <li key={row.key} className="grid grid-cols-[minmax(0,9rem)_1fr_auto] items-center gap-3">
            <span className="min-w-0 truncate text-[0.8125rem] text-foreground">{row.name}</span>
            <div className="relative h-3.5 rounded-sm bg-surface-2">
              {row.lookups === 0 ? (
                <span className="absolute inset-y-0 left-0 flex items-center pl-2 text-[0.68rem] text-muted-foreground italic">
                  no lookups yet
                </span>
              ) : (
                <div
                  className="absolute inset-y-0 left-0 overflow-hidden rounded-sm bg-blue-200"
                  style={{ width: `${Math.max(share, 1.5)}%` }}
                  role="img"
                  aria-label={`${row.name}: ${row.lookups} lookups, ${hitPct.toFixed(0)} percent of them hits`}
                >
                  <span
                    className="block h-full rounded-sm bg-blue-600"
                    style={{ width: `${hitPct}%` }}
                  />
                </div>
              )}
            </div>
            <span className="flex w-32 items-baseline justify-end gap-2">
              <Figure className="text-muted-foreground">{count(row.lookups)}</Figure>
              {row.hit_rate === null ? (
                <span className="text-[0.68rem] text-muted-foreground italic">no rate</span>
              ) : (
                <Figure className="font-semibold text-foreground">{`${(row.hit_rate * 100).toFixed(0)}%`}</Figure>
              )}
            </span>
          </li>
        )
      })}
      <li className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-t border-border pt-2.5 text-[0.72rem] text-muted-foreground">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm bg-blue-200" aria-hidden />
          share of all lookups
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-4 rounded-sm bg-blue-600" aria-hidden />
          of which hits
        </span>
      </li>
    </ul>
  )
}

/** Client entry for the Cache section — gated on a reachable backend. */
export function CacheMount(): ReactElement {
  return (
    <BackendGate>
      <CacheView />
    </BackendGate>
  )
}
