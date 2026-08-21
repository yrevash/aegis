'use client'

import { ListFilter } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import type { Provenance, ScoredSource } from '@/lib/stream'

import { ProvenanceChip } from './ProvenanceChip'
import { rankSources } from './rerank'

/** How many top sources to surface on the primary rail. */
const TOP_N = 3

/**
 * How many ranks a decay curve needs before the axis earns its height.
 *
 * One or two bars is not a decay — it is a couple of numbers stranded in 168px of
 * gridlines, which reads as a chart that failed to load. Below this the scores go
 * back on the ranked list, where two of them are read perfectly well.
 */
const MIN_RANKS = 3

/** Rerank scores are `[0,1]`; two decimals, through Intl rather than a template. */
const SCORE = new Intl.NumberFormat('en-US', {
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
})

/** Funnel counts, likewise — and with the locale named, so SSR and CSR agree. */
const COUNT = new Intl.NumberFormat('en-US')

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
 * The retrieval funnel made visible: "N recalled → reranked → top K", over a
 * chart of the reranker's own scores by rank.
 *
 * **It was a list of proportional bars, and now it is a chart with an axis.** The
 * old bars were each scaled to the top score, so a run whose best source scored
 * 0.31 drew the same full-width bar as one that scored 0.98 — the shape said
 * "top match" and never said *how good the top match was*, which is the only
 * question a reranker answers. `retrievalScores[].score` is a real number in
 * `[0,1]`; plotted against a gridline a reader can read the value off, a shallow
 * decay across ranks (every source about as relevant as the next) is visible, and
 * so is a cliff.
 *
 * The labels stay, as a short ranked list under the chart. They are not printed
 * with their scores a second time — the chart is where a score is read.
 *
 * **Below {@link MIN_RANKS} the chart is dropped rather than drawn.** One or two
 * bars is not a decay curve; it is two numbers stranded in 168px of gridlines,
 * which reads as a chart that failed to load. In that case the scores go back on
 * the ranked rows, where two of them are read perfectly well, and the card is
 * only as tall as the facts it holds.
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
  const curve = useMemo(
    () => ranked.map((s) => ({ rank: `#${s.rank}`, score: s.score })),
    [ranked],
  )
  const plotted = curve.length >= MIN_RANKS

  return (
    <Card>
      <CardHeader
        eyebrow="rerank score by rank"
        title={
          <span className="flex items-center gap-2">
            <ListFilter className="size-4 shrink-0 text-blue-600" aria-hidden />
            Sources
          </span>
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <InfoTip label="About Sources">
              Vector, graph and keyword candidates fused, then re-scored by the reranker.
            </InfoTip>
            <ProvenanceChip provenance={provenance} />
          </div>
        }
      />
      <CardBody className="space-y-3 pt-4">
        {kept === 0 ? (
          <Absence
            figure="Rerank scores"
            why="Retrieval ran, but no scored source came back on the stream."
          />
        ) : (
          <>
            {/* Funnel headline: recalled → ranked → used */}
            <div className="flex flex-wrap items-center gap-2 font-mono text-[0.7rem] text-muted-foreground">
              <FunnelStage value={recalled} label="recalled" />
              <span aria-hidden>→</span>
              <FunnelStage value={kept} label="ranked" tone="graph" />
              <span aria-hidden>→</span>
              <FunnelStage value={shown.length} label="used" tone="ok" />
            </div>

            {plotted ? (
              <BarChart
                data={curve}
                index="rank"
                category="score"
                color="graph"
                valueFormatter={(v) => SCORE.format(v)}
                height={168}
              />
            ) : null}

            {/* Which source each of the top ranks is. With a chart above, the
                score is read off it; without one, it belongs on the row. */}
            <ol className="space-y-1">
              {shown.map((s) => (
                <li key={s.id} className="flex items-baseline gap-2">
                  <Figure className="w-5 shrink-0 text-right text-[0.66rem] leading-4 text-muted-foreground">
                    #{s.rank}
                  </Figure>
                  <span
                    className="min-w-0 flex-1 truncate text-[0.76rem] text-foreground"
                    title={s.label}
                  >
                    {s.label}
                  </span>
                  {plotted ? null : (
                    <Figure className="shrink-0 text-[0.76rem] leading-4 font-semibold text-foreground">
                      {SCORE.format(s.score)}
                    </Figure>
                  )}
                </li>
              ))}
              {more > 0 && (
                <li className="text-[0.7rem] text-muted-foreground/80">
                  +{COUNT.format(more)} more ranked
                </li>
              )}
            </ol>

            <Receipt
              origin="/query stream · reranker scores"
              detail={`${COUNT.format(kept)} kept of ${COUNT.format(recalled)} fused candidates`}
            />
          </>
        )}
      </CardBody>
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
      <Figure className={`text-sm leading-5 font-semibold ${toneClass}`}>{COUNT.format(value)}</Figure>
      <span>{label}</span>
    </span>
  )
}