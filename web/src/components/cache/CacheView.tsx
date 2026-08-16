'use client'

import { Brain, DatabaseZap, ShieldCheck, type LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { Card, CardBody } from '@/components/ui/Card'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { cn } from '@/lib/utils'

/** Which of the three real caches a spec describes. */
type CacheKind = 'memory' | 'retrieval' | 'guardrail'

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

/** One cache's card — its real method, backend, TTL, threshold and eviction. */
function CacheCard({ spec }: { spec: CacheSpec }): ReactElement {
  const Icon = spec.icon
  return (
    <Card>
      <CardBody>
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
      </CardBody>
    </Card>
  )
}

/**
 * Cache (§ caches) — the three real caches made visible with their true method +
 * config: what each one stores, how a hit is decided, which backend holds it, the
 * TTL, the similarity threshold and the eviction rule.
 *
 * There are deliberately no hit/miss counters here. The caches report per-run
 * `*_cache` events on the aegis AG-UI stream and keep no durable aggregate, and
 * the web `/query` contract does not carry those events at all — so any hit-rate
 * shown on this page would be a number the platform never measured.
 */
function CacheView(): ReactElement {
  return (
    <div className="space-y-6">
      <div>
        <p className="eyebrow mb-1">semantic · TTL · hash</p>
        <h1 className="t-hero text-foreground">Cache</h1>
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        {SPECS.map((spec) => (
          <CacheCard key={spec.kind} spec={spec} />
        ))}
      </div>
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
