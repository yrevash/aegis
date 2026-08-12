/**
 * In-browser SAMPLE fixture backing the Cache dashboard's hit-rate meters and
 * live event feed when no run stream is present (offline demo / `?mock=1`).
 *
 * IMPORTANT — honesty contract: there is no aggregate cache-STATS backend
 * endpoint. The three caches (memory / retrieval / guardrail) emit per-run
 * `*_cache` CustomEvents over the query SSE stream, not durable counters. So the
 * hit/miss/evict numbers below are an illustrative `sample`, badged as such in
 * the UI, and MUST never be presented as a live measured aggregate. The
 * per-cache METHOD / CONFIG shown alongside them (in `CacheView`) is the real,
 * honest configuration read from the modules — only these counters are sampled.
 */

/** Which of the three real caches an entry belongs to. */
export type CacheKind = 'memory' | 'retrieval' | 'guardrail'

/** A sampled hit/miss/evict summary for one cache (illustrative, not measured). */
export interface CacheSampleStats {
  kind: CacheKind
  hits: number
  misses: number
  /** Evictions (TTL sweep + size ceiling). Guardrail cache has none → 0. */
  evicts: number
}

/** One entry in the live/sample event feed (mirrors a `*_cache` stream event). */
export interface CacheFeedEvent {
  kind: CacheKind
  /** The outcome the cache reported for this lookup. */
  event: 'hit' | 'miss' | 'evict'
  /** Short, honest detail (provenance/backend), e.g. "cache-near · cos 0.991". */
  detail: string
  /** ms before "now" this event is anchored to (feeds a relative "Xs ago"). */
  agoMs: number
}

/** Hit rate = hits / (hits + misses); 0 when nothing has been looked up yet. */
export function hitRate(s: Pick<CacheSampleStats, 'hits' | 'misses'>): number {
  const total = s.hits + s.misses
  return total === 0 ? 0 : s.hits / total
}

/**
 * The sample per-cache summaries. Coherent with the console scenario (M. Reed,
 * account A-771, refund flow) — a warm demo session where repeated,
 * semantically-close questions produce a believable but clearly illustrative
 * hit rate. Guardrail cache never evicts (plain hash key → verdict).
 */
export const SAMPLE_CACHE_STATS: Record<CacheKind, CacheSampleStats> = {
  memory: { kind: 'memory', hits: 34, misses: 21, evicts: 3 },
  retrieval: { kind: 'retrieval', hits: 47, misses: 18, evicts: 5 },
  guardrail: { kind: 'guardrail', hits: 62, misses: 9, evicts: 0 },
}

/** The sample event feed, newest first, anchored to "now" so ages read fresh. */
export const SAMPLE_CACHE_FEED: CacheFeedEvent[] = [
  { kind: 'guardrail', event: 'hit', detail: 'sha256 verdict reused · pass', agoMs: 2_000 },
  { kind: 'retrieval', event: 'hit', detail: 'cache-near · cos 0.991', agoMs: 6_000 },
  { kind: 'memory', event: 'hit', detail: 'recall reused · cos 0.972', agoMs: 11_000 },
  { kind: 'retrieval', event: 'miss', detail: 'below 0.985 → ran retrieval', agoMs: 18_000 },
  { kind: 'guardrail', event: 'hit', detail: 'sha256 verdict reused · pass', agoMs: 24_000 },
  { kind: 'memory', event: 'evict', detail: 'TTL expiry (900s) swept', agoMs: 31_000 },
  { kind: 'retrieval', event: 'hit', detail: 'cache-exact · sha256 key', agoMs: 39_000 },
  { kind: 'memory', event: 'miss', detail: 'new subject query → recomputed', agoMs: 47_000 },
  { kind: 'guardrail', event: 'miss', detail: 'first-seen redacted text', agoMs: 55_000 },
  { kind: 'retrieval', event: 'evict', detail: 'max entries → oldest dropped', agoMs: 63_000 },
]
