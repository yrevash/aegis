/**
 * Pure formatting for a retrieval {@link Provenance} readout — turns the raw
 * origins/fusion/cache fields into the chip's human copy. Kept side-effect free
 * so it is unit-testable (see `provenance.test.ts`); the chip only styles it.
 */

import type { Provenance } from '@/lib/stream'

/** A formatted provenance chip readout. */
export interface ProvenanceLabel {
  /** Headline, e.g. "hybrid retrieval" or "served from cache". */
  headline: string
  /** Detail, e.g. "vector + graph + bm25 · RRF" or "near-exact". */
  detail: string
  /** Whether this result came from the cache (drives the chip hue/icon). */
  cache: boolean
}

/** Human-readable fusion label. */
function fusionLabel(fusion: Provenance['fusion']): string {
  switch (fusion) {
    case 'rrf':
      return 'RRF'
    case 'mix':
      return 'mix'
    case 'none':
      return 'single'
  }
}

/** Describe a provenance record for the chip. */
export function describeProvenance(p: Provenance): ProvenanceLabel {
  if (p.cache_hit) {
    const kind = (p.cache_kind ?? 'exact').replace(/[_-]/g, ' ')
    return { headline: 'served from cache', detail: kind, cache: true }
  }
  const origins = p.origins.join(' + ') || 'none'
  const suffix = p.fusion !== 'none' ? ` · ${fusionLabel(p.fusion)}` : ''
  return { headline: 'hybrid retrieval', detail: `${origins}${suffix}`, cache: false }
}
