'use client'

import { BadgeCheck, FileText, ShieldAlert } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { RerankScoreboard } from '@/components/retrieval/RerankScoreboard'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { hasProvenance, hasSpanCheck, readSources, type SourceEvidence } from './sources'

/** One source row: what was retrieved, where it sits, and whether it was checked. */
function SourceRow({ source, rank }: { source: SourceEvidence; rank: number }): ReactElement {
  return (
    <li className="grid grid-cols-[auto_1fr] items-start gap-2 border-b border-border/60 py-2 last:border-b-0">
      <span className="tabular mt-0.5 w-5 text-right font-mono text-[0.68rem] text-muted-foreground">
        {rank}
      </span>
      <div className="min-w-0">
        <p className="text-[0.8rem] leading-snug text-foreground">{source.label}</p>
        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <Badge tone="graph">{source.score.toFixed(2)}</Badge>

          {/* Page is shown only when the run reported one. A default of "page 1" would
              be the single most quotable false statement this screen could make. */}
          {source.page !== null && <Badge tone="neutral">page {source.page}</Badge>}
          {source.located && <Badge tone="neutral">located on page</Badge>}

          {source.verbatim === 'verified' && (
            <Badge tone="ok">
              <BadgeCheck aria-hidden /> verbatim in source
            </Badge>
          )}
          {source.verbatim === 'unverified' && (
            <Badge tone="block">
              <ShieldAlert aria-hidden /> not found in source
              {source.matchedFraction !== null &&
                ` · ${Math.round(source.matchedFraction * 100)}% matched`}
            </Badge>
          )}

          <span className="font-mono text-[0.66rem] text-muted-foreground/70">{source.id}</span>
        </div>
      </div>
    </li>
  )
}

/**
 * The Sources tab — what actually backs the answer.
 *
 * The funnel (recalled → ranked → used) is the reused {@link RerankScoreboard}; the list
 * beneath it is every ranked source with the evidence the run reported about it: its
 * page, whether its span was located on that page, and whether the quoted text was found
 * **verbatim** in the source rather than asserted.
 *
 * Where a run reported none of that, the tab says so in one line. It does not draw an
 * empty column and it does not fill a page number in — an unchecked citation shown as
 * checked is worse than no citation at all.
 */
export function SourcesTab({ state }: { state: RunState }): ReactElement {
  const sources = readSources(state.retrievalScores)
  const provenance = hasProvenance(sources)
  const checked = hasSpanCheck(sources)

  if (sources.length === 0) {
    return (
      <Card>
        <CardBody className="flex min-h-32 flex-col items-center justify-center gap-2 text-center">
          <FileText aria-hidden className="size-6 text-muted-foreground/50" />
          <p className="text-sm text-muted-foreground">
            {state.candidates > 0
              ? `Retrieval recalled ${state.candidates} candidates but ranked none of them.`
              : 'This run retrieved nothing — the answer is not grounded in a document.'}
          </p>
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <RerankScoreboard
        scores={state.retrievalScores}
        candidates={state.candidates}
        provenance={state.provenance}
      />

      <Card>
        <CardHeader
          title={
            <span className="flex items-center gap-2">
              <FileText aria-hidden className="size-4 shrink-0 text-blue-600" />
              Every ranked source
            </span>
          }
          actions={<Badge tone="neutral">{sources.length}</Badge>}
        />
        <CardBody>
          <ol className="flex flex-col">
            {sources.map((source, index) => (
              <SourceRow key={source.id} source={source} rank={index + 1} />
            ))}
          </ol>

          {(!provenance || !checked) && (
            <p
              className={cn(
                'mt-3 rounded-md border border-dashed border-border bg-surface-2/40 px-3 py-2',
                'text-[0.72rem] leading-snug text-muted-foreground',
              )}
            >
              {!provenance && 'This run reported no page or position for its sources. '}
              {!checked && 'No verbatim span check ran, so every quote here is the model’s claim.'}
            </p>
          )}
        </CardBody>
      </Card>
    </div>
  )
}
