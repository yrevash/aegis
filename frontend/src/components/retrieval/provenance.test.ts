import { describe, expect, it } from 'vitest'

import type { Provenance } from '@/types/stream'

import { describeProvenance } from './provenance'

/** Build a provenance event with sensible defaults. */
function prov(overrides: Partial<Provenance>): Provenance {
  return {
    type: 'provenance',
    run_id: 'r',
    seq: 1,
    origins: [],
    fusion: 'none',
    cache_hit: false,
    cache_kind: null,
    original_query: null,
    cached_at: null,
    ...overrides,
  }
}

describe('describeProvenance', () => {
  it('describes hybrid retrieval fused by RRF', () => {
    const label = describeProvenance(prov({ origins: ['vector', 'graph', 'bm25'], fusion: 'rrf' }))
    expect(label.cache).toBe(false)
    expect(label.headline).toBe('hybrid retrieval')
    expect(label.detail).toBe('vector + graph + bm25 · RRF')
  })

  it('describes a cache hit with a normalised kind', () => {
    const label = describeProvenance(
      prov({ cache_hit: true, cache_kind: 'near-exact', origins: ['cache'] }),
    )
    expect(label.cache).toBe(true)
    expect(label.headline).toBe('served from cache')
    expect(label.detail).toBe('near exact')
  })

  it('omits the fusion suffix for a single-origin result', () => {
    expect(describeProvenance(prov({ origins: ['vector'], fusion: 'none' })).detail).toBe('vector')
  })
})
