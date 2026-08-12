'use client'

import {
  Brain,
  DatabaseZap,
  Hash,
  ShieldCheck,
  Timer,
  WifiOff,
  type LucideIcon,
} from 'lucide-react'
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

/** One label/value row in a card's read-only config grid. */
function ConfigRow({
  icon: Icon,
  label,
  value,
}: {
  icon: LucideIcon
  label: string
  value: string
}): ReactElement {
  return (
    <div className="flex items-start gap-2 py-1.5">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <span className="eyebrow">{label}</span>
        <p className="text-sm text-foreground">{value}</p>
      </div>
    </div>
  )
}

/** A single hit/miss/evict counter tile. */
function Counter({ label, value }: { label: string; value: number | null }): ReactElement {
  return (
    <div className="rounded-lg border border-border bg-surface-2/50 px-3 py-2 text-center">
      <p className="tabular-nums text-lg font-semibold text-foreground">
        {value === null ? '—' : value}
      </p>
      <p className="eyebrow mt-0.5">{label}</p>
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
    <ul className="space-y-1.5">
      {events.map((e, i) => (
        <li
          key={i}
          className="flex items-center justify-between gap-2 rounded-md bg-surface-2/40 px-2.5 py-1.5"
        >
          <span className="flex min-w-0 items-center gap-2">
            <Badge tone={EVENT_TONE[e.event]} className="uppercase">
              {e.event}
            </Badge>
            <span className="truncate text-xs text-muted-foreground">{e.detail}</span>
          </span>
          <span className="shrink-0 font-mono text-[0.68rem] text-muted-foreground">
            {ago(e.agoMs)}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** One cache's card — method + real config, sample meter + counters, event feed. */
function CacheCard({ spec, mock }: { spec: CacheSpec; mock: boolean }): ReactElement {
  const Icon = spec.icon
  const stats = mock ? SAMPLE_CACHE_STATS[spec.kind] : null
  const feed = mock ? SAMPLE_CACHE_FEED.filter((e) => e.kind === spec.kind) : []
  const rate = stats ? hitRate(stats) : 0

  return (
    <Card>
      <CardBody>
        <div className="grid gap-6 lg:grid-cols-2">
          {/* Left — method + honest, module-real config. */}
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
            <p className="mt-3 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Caches:</span> {spec.caches}
            </p>
            <p className="mt-1 text-sm text-muted-foreground">
              <span className="font-medium text-foreground">Method:</span> {spec.method}
            </p>
            <div className="mt-3 divide-y divide-border/60 border-t border-border/60">
              <ConfigRow icon={DatabaseZap} label="backend" value={spec.backend} />
              <ConfigRow icon={Timer} label="ttl" value={spec.ttl} />
              <ConfigRow icon={Hash} label="similarity / distance threshold" value={spec.threshold} />
              <ConfigRow icon={DatabaseZap} label="max entries / eviction" value={spec.eviction} />
            </div>
          </div>

          {/* Right — sample hit-rate meter + counters, then the event feed. */}
          <div className="flex flex-col gap-4">
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="eyebrow">hit rate</span>
                {mock ? (
                  <Badge tone="neutral" className="uppercase">
                    sample
                  </Badge>
                ) : (
                  <span className="text-xs text-muted-foreground">no live aggregate</span>
                )}
              </div>
              <div className="flex items-center gap-3">
                <span className="tabular-nums text-2xl font-semibold text-foreground">
                  {stats ? `${Math.round(rate * 100)}%` : '—'}
                </span>
                <MiniMeter value={rate} hex={spec.meterHex} height={8} className="flex-1" />
              </div>
            </div>
            <div className="grid grid-cols-3 gap-2">
              <Counter label="hits" value={stats?.hits ?? null} />
              <Counter label="misses" value={stats?.misses ?? null} />
              <Counter label="evicts" value={stats?.evicts ?? null} />
            </div>
            <div>
              <div className="mb-1.5 flex items-center justify-between">
                <span className="eyebrow">live event feed</span>
                {mock && (
                  <Badge tone="neutral" className="uppercase">
                    sample
                  </Badge>
                )}
              </div>
              <EventFeed events={feed} empty={!mock} />
            </div>
          </div>
        </div>
      </CardBody>
    </Card>
  )
}

/**
 * Cache (§ caches) — the three real caches made visible with their true method +
 * config. Each card shows honest, module-real configuration (backend / TTL /
 * threshold / eviction) alongside a `sample`-badged hit-rate meter, hit/miss/
 * evict counters, and a per-cache event feed. Offline (`?mock=1`) the meters and
 * feed render from an illustrative sample fixture, clearly labelled; live, they
 * fall back to an honest "no live aggregate yet" empty state (the caches emit
 * per-run `*_cache` events, not a durable aggregate counter).
 */
function CacheView({ mock }: { mock: boolean }): ReactElement {
  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">semantic · TTL · hash</p>
        <h1 className="t-hero text-foreground">Cache</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          The three caches that make repeated work cheap — memory recall, retrieval + answers, and
          guardrail verdicts. Each shows its real method and configuration; the hit-rate meters and
          event feed are an illustrative <span className="font-medium text-foreground">sample</span>{' '}
          offline, never a live measured aggregate.
        </p>
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
