'use client'

import { Brain, DatabaseZap, ShieldCheck, WifiOff, type LucideIcon } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { isMock, probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { Card, CardBody } from '@/components/ui/Card'
import { Badge } from '@/components/ui/Badge'
import { MiniMeter } from '@/components/memory/MiniMeter'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { cn } from '@/lib/utils'
import {
  hitRate,
  SAMPLE_CACHE_FEED,
  SAMPLE_CACHE_STATS,
  type CacheFeedEvent,
  type CacheKind,
} from '@/mock/cacheStats'

/**
 * The honest, module-real configuration for one cache. Every field here mirrors
 * what the corresponding Python module actually does (see
 * `aegis/src/aegis/memory/cache.py`, `retrieval/cache.py` + `answer_cache.py`,
 * `guardrails/cache.py`) — it is read-only config data, NOT a measured metric.
 */
interface CacheSpec {
  kind: CacheKind
  title: string
  /** The `*_cache` stream event name this surface listens for. */
  event: string
  icon: LucideIcon
  /** Accent token base (agent / graph / block) for the card's tint. */
  tint: string
  ink: string
  meterHex: string
  /** What the cache stores. */
  caches: string
  /** The key method — how a hit is decided. */
  method: string
  /** Redis / in-memory, with the honest prod-vs-fallback split. */
  backend: string
  /** Time-to-live written on entries. */
  ttl: string
  /** Similarity / distance threshold for a hit (or n/a for an exact-hash cache). */
  threshold: string
  /** Max entries + eviction policy. */
  eviction: string
}

/** The three real caches, in the order they sit on the read/guard path. */
const SPECS: CacheSpec[] = [
  {
    kind: 'memory',
    title: 'Memory semantic cache',
    event: 'memory_cache',
    icon: Brain,
    tint: 'bg-agent/12',
    ink: 'text-agent-ink',
    meterHex: 'var(--agent)',
    caches: 'Recall + assembly payload, keyed by (subject, query)',
    method: 'Semantic — cosine nearest-neighbour over the query embedding',
    backend: 'RedisVL (prod) · labeled in-memory fallback',
    ttl: '900s',
    threshold: 'cosine distance ≤ 0.05 (similarity ≥ 0.95)',
    eviction: '512 max entries · oldest-out + TTL sweep',
  },
  {
    kind: 'retrieval',
    title: 'Retrieval answer / semantic cache',
    event: 'retrieval_cache',
    icon: DatabaseZap,
    tint: 'bg-graph/12',
    ink: 'text-graph-ink',
    meterHex: 'var(--graph)',
    caches: 'Retrieval results + final generated answers',
    method: 'Near-exact sha256 hash, then semantic cosine fallback',
    backend: 'Redis (portable — no RediSearch module)',
    ttl: '3600s results · 1800s answers',
    threshold: 'cosine ≥ 0.985 (results) · ≥ 0.97 (answers)',
    eviction: 'TTL only (Redis key expiry)',
  },
  {
    kind: 'guardrail',
    title: 'Guardrail injection cache',
    event: 'guardrail_cache',
    icon: ShieldCheck,
    tint: 'bg-block/12',
    ink: 'text-block-ink',
    meterHex: 'var(--block)',
    caches: 'Prompt-injection classifier verdicts',
    method: 'sha256 of the PII-redacted text → verdict (exact key)',
    backend: 'Redis (full mode) · in-memory (lite/auto)',
    ttl: 'none (persistent key → verdict)',
    threshold: 'n/a — exact hash match',
    eviction: 'none (one stable verdict per text)',
  },
]

/** One label/value row in a card's read-only config list. */
function ConfigRow({ label, value }: { label: string; value: string }): ReactElement {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="eyebrow shrink-0 text-[0.58rem]">{label}</dt>
      <dd className="min-w-0 text-right text-[0.8rem] leading-snug text-foreground">{value}</dd>
    </div>
  )
}

/** A single hit/miss/evict counter tile. */
function Counter({ label, value }: { label: string; value: number | null }): ReactElement {
  return (
    <div className="rounded-lg border border-border bg-surface-2/50 px-3 py-1.5 text-center">
      <p className="tabular-nums text-lg font-semibold text-foreground">
        {value === null ? '—' : value}
      </p>
      <p className="eyebrow text-[0.58rem]">{label}</p>
    </div>
  )
}

const EVENT_TONE = {
  hit: 'ok',
  miss: 'neutral',
  evict: 'risk',
} as const

/** Relative "Xs ago" from a ms-before-now offset. */
function ago(ms: number): string {
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s ago`
  const m = Math.round(s / 60)
  return `${m}m ago`
}

/** A small stream log of `*_cache` events for one cache (sample offline). */
function EventFeed({
  events,
  empty,
}: {
  events: CacheFeedEvent[]
  empty: boolean
}): ReactElement {
  if (empty) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/30 px-3 py-4 text-center text-xs text-muted-foreground">
        No live aggregate yet — caches emit per-run events; run a query to populate.
      </div>
    )
  }
  return (
    <ul className="space-y-1">
      {events.map((e, i) => (
        <li
          key={i}
          className="flex items-center justify-between gap-2 rounded-md bg-surface-2/40 px-2.5 py-1"
        >
          <span className="flex min-w-0 items-center gap-2">
            <Badge tone={EVENT_TONE[e.event]} className="px-2 text-[0.58rem] uppercase">
              {e.event}
            </Badge>
            <span className="truncate text-xs text-muted-foreground">{e.detail}</span>
          </span>
          <span className="tabular shrink-0 font-mono text-[0.65rem] text-muted-foreground">
            {ago(e.agoMs)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** One cache's card — the real config list, then the activity block. */
function CacheCard({ spec, mock }: { spec: CacheSpec; mock: boolean }): ReactElement {
  const Icon = spec.icon
  const stats = mock ? SAMPLE_CACHE_STATS[spec.kind] : null
  const feed = mock ? SAMPLE_CACHE_FEED.filter((e) => e.kind === spec.kind) : []
  const rate = stats ? hitRate(stats) : 0

  return (
    <Card>
      <CardBody>
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Left — the honest, module-real config, as one uniform list. */}
          <div>
            <div className="flex items-center gap-3">
              <span className={cn('flex size-9 items-center justify-center rounded-xl', spec.tint)}>
                <Icon className={cn('size-5', spec.ink)} />
              </span>
              <div className="min-w-0">
                <h3 className="t-title truncate text-foreground">{spec.title}</h3>
                <code className="font-mono text-[0.7rem] text-muted-foreground">{spec.event}</code>
              </div>
            </div>
            <dl className="mt-3 divide-y divide-border/60 border-t border-border/60">
              <ConfigRow label="caches" value={spec.caches} />
              <ConfigRow label="method" value={spec.method} />
              <ConfigRow label="backend" value={spec.backend} />
              <ConfigRow label="ttl" value={spec.ttl} />
              <ConfigRow label="threshold" value={spec.threshold} />
              <ConfigRow label="max entries / eviction" value={spec.eviction} />
            </dl>
          </div>

          {/* Right — measured activity. One honest provenance label covers the
              hit-rate meter, the counters and the feed: `sample` offline, the
              "no live aggregate" empty state live. */}
          <div className="flex flex-col gap-3">
            <div className="flex items-center justify-between gap-2 border-b border-border/60 pb-1.5">
              <span className="eyebrow">activity</span>
              {mock ? (
                <Badge tone="neutral" className="uppercase">
                  sample
                </Badge>
              ) : (
                <span className="text-xs text-muted-foreground">no live aggregate</span>
              )}
            </div>
            <div className="flex items-center gap-3">
              <span className="eyebrow shrink-0">hit rate</span>
              <span className="tabular-nums text-2xl font-semibold text-foreground">
                {stats ? `${Math.round(rate * 100)}%` : '—'}
              </span>
              <MiniMeter value={rate} hex={spec.meterHex} height={8} className="flex-1" />
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Counter label="hits" value={stats?.hits ?? null} />
              <Counter label="misses" value={stats?.misses ?? null} />
              <Counter label="evicts" value={stats?.evicts ?? null} />
            </div>
            <EventFeed events={feed} empty={!mock} />
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

/**
 * Cache (§ caches) — the three real caches made visible with their true method +
 * config. Each card pairs the honest, module-real configuration list (caches /
 * method / backend / TTL / threshold / eviction) with an activity block: the
 * hit-rate meter, hit/miss/evict counters and the per-cache event feed, all
 * under one provenance label. Offline (`?mock=1`) that block reads from an
 * illustrative sample fixture, badged `sample`; live it falls back to the honest
 * "no live aggregate yet" empty state (the caches emit per-run `*_cache` events,
 * not a durable aggregate counter).
 */
function CacheView({ mock }: { mock: boolean }): ReactElement {
  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">semantic · TTL · hash</p>
        <h1 className="t-hero text-foreground">Cache</h1>
      </div>
      {SPECS.map((spec) => (
        <CacheCard key={spec.kind} spec={spec} mock={mock} />
      ))}
    </div>
  )
}

/**
 * Client entry for the Cache section. Runs the boot probe once (live-first, mock
 * fallback) before mounting the view, mirroring `MemoryMount` / `EvalsMount`.
 * Offline is labelled with the honest banner and the meters/feed read the sample
 * fixture; live shows the honest "no live aggregate yet" empty states.
 */
export function CacheMount(): ReactElement {
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className={cn(
            'mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white',
          )}
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <CacheView mock={isMock()} />
    </TooltipProvider>
  )
}
