'use client'

import { DatabaseZap, GitMerge } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { Provenance } from '@/lib/stream'

import { describeProvenance } from './provenance'

export interface ProvenanceChipProps {
  provenance: Provenance | null
  className?: string
}

/**
 * A compact chip that makes retrieval provenance visible: either "hybrid
 * retrieval · vector + graph + bm25 · RRF" or "served from cache · near-exact"
 * (with the original query + when it was cached). Turns the cache from an opaque
 * shortcut into an honest, auditable efficiency signal.
 */
export function ProvenanceChip({ provenance, className }: ProvenanceChipProps): ReactElement | null {
  if (provenance === null) return null
  const { detail, cache } = describeProvenance(provenance)
  const Icon = cache ? DatabaseZap : GitMerge

  return (
    <div className={cn('flex flex-wrap items-center gap-1', className)}>
      <Badge variant={cache ? 'ml' : 'graph'} className="gap-1">
        <Icon className="size-3" aria-hidden />
        {cache ? 'cached' : 'hybrid'}
      </Badge>
      <InfoTip label="Retrieval detail">
        {cache ? 'Served from cache' : 'Hybrid search'} — {detail}.
        {cache && provenance.original_query ? ` Matched “${provenance.original_query}”.` : ''}
      </InfoTip>
    </div>
  )
}