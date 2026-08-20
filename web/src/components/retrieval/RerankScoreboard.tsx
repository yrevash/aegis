'use client'

import { ListFilter } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { Provenance, ScoredSource } from '@/lib/stream'

import { ProvenanceChip } from './ProvenanceChip'
import { rankSources } from './rerank'

/** How many top sources to surface on the primary rail. */
const TOP_N = 3

export interface RerankScoreboardProps {
  /** Reranked sources (any order); rendered highest-first. */
  scores: ScoredSource[]
  /**
   * Total candidates recalled before reranking, for the funnel headline. When
   * omitted it falls back to the number of scored sources.
   */
  candidates?: number
  /** How many top sources to show; defaults to all provided. */
  topK?: number
  /** Retrieval provenance (origins/fusion/cache), rendered as a chip. */
  provenance?: Provenance | null
}

/**
 * The retrieval funnel made visible: "N recalled → reranked → top K". Each
 * surviving source gets a horizontal bar scaled to the strongest score, so the
 * jury reads at a glance that the system did not just dump candidates — it
 * ranked them and kept the best. Compact and light for the console rail.
 */
export function RerankScoreboard({
  scores,
  candidates,
  topK,
  provenance = null,
}: RerankScoreboardProps): ReactElement {
  const ranked = useMemo(() => rankSources(scores, topK), [scores, topK])
  const recalled = candidates ?? scores.length
  const kept = ranked.length
  const shown = ranked.slice(0, TOP_N)
  const more = kept - shown.length

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <ListFilter className="size-4 text-blue-600" />
        <CardTitle>Sources</CardTitle>
        <InfoTip label="About Sources">
          Hybrid search then rerank — vector, graph, and keyword candidates fused
          and re-scored. Bars show relevance relative to the top match.
        </InfoTip>
        <ProvenanceChip provenance={provenance} className="ml-auto" />
      </CardHeader>
      <CardContent className="space-y-3">
        {kept === 0 ? (
          <div className="flex min-h-20 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
            <ListFilter className="size-6 text-muted-foreground/50" />
            <p>Sources appear here once retrieval runs.</p>
          </div>
        ) : (
          <>
            {/* Funnel headline: recalled → ranked → used */}
            <div className="flex items-center gap-2 font-mono text-[0.7rem] text-muted-foreground">
              <FunnelStage value={recalled} label="recalled" />
              <span aria-hidden>→</span>
              <FunnelStage value={kept} label="ranked" tone="graph" />
              <span aria-hidden>→</span>
              <FunnelStage value={shown.length} label="used" tone="ok" />
            </div>

            {/* Score bars — top matches only */}
            <ol className="space-y-1.5">
              {shown.map((s) => (
                <li key={s.id} className="grid grid-cols-[auto_1fr_auto] items-center gap-2">
                  <span className="tabular w-4 text-right font-mono text-[0.66rem] text-muted-foreground">
                    {s.rank}
                  </span>
                  <div className="min-w-0">
                    <span className="block truncate text-[0.76rem] text-foreground">{s.label}</span>
                    <div className="mt-0.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                      <div
                        className="h-full rounded-full bg-blue-400"
                        style={{ width: `${Math.max(2, s.relative * 100)}%` }}
                      />
                    </div>
                  </div>
                  <span className="tabular font-mono text-[0.72rem] text-blue-600">
                    {s.score.toFixed(2)}
                  </span>
                </li>
              ))}
            </ol>
            {more > 0 && (
              <p className="text-[0.7rem] text-muted-foreground/80">+{more} more ranked</p>
            )}
          </>
        )}
      </CardContent>
    </Card>
  )
}

/** One labelled stage in the funnel headline. */
function FunnelStage({
  value,
  label,
  tone = 'neutral',
}: {
  value: number
  label: string
  tone?: 'neutral' | 'graph' | 'ok'
}): ReactElement {
  const toneClass =
    tone === 'graph' ? 'text-blue-600' : tone === 'ok' ? 'text-ok-ink' : 'text-foreground'
  return (
    <span className="inline-flex items-baseline gap-1">
      <span className={`tabular text-sm font-semibold ${toneClass}`}>{value}</span>
      <span>{label}</span>
    </span>
  )
}