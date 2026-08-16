'use client'

import {
  ArrowDownNarrowWide,
  Boxes,
  Check,
  GitMerge,
  Highlighter,
  Minus,
  PencilLine,
  Repeat2,
  Search,
  type LucideIcon,
} from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { RetrievalOrigin } from '@/lib/stream'
import { ORIGIN_LABEL, type RetrievalObservability } from './observability'

/** Whether a stage ran (on), demonstrably did not (off), or isn't measurable. */
type Status = 'on' | 'off' | 'na'

/** Per-origin icon for the recall arms. */
const ARM_ICON: Partial<Record<RetrievalOrigin, LucideIcon>> = {
  vector: Boxes,
  graph: GitMerge,
  bm25: Search,
}

/** The on/off/na pill for one arsenal stage. */
function StatusPill({ status }: { status: Status }): ReactElement {
  if (status === 'na') {
    return (
      <Badge tone="neutral" className="uppercase">
        <Minus className="size-3" /> n/a
      </Badge>
    )
  }
  const on = status === 'on'
  const tone: BadgeTone = on ? 'ok' : 'neutral'
  return (
    <Badge tone={tone} className="uppercase">
      {on ? <Check className="size-3" /> : <Minus className="size-3" />}
      {on ? 'on' : 'off'}
    </Badge>
  )
}

/** One arsenal stage row — icon, title, on/off pill, a right-side metric, detail. */
function StageRow({
  icon: Icon,
  title,
  status,
  metric,
  children,
}: {
  icon: LucideIcon
  title: string
  status: Status
  metric?: ReactNode
  children?: ReactNode
}): ReactElement {
  const dim = status !== 'on'
  return (
    <div className="py-3">
      <div className="flex items-center gap-3">
        <span
          className={cn(
            'flex size-8 shrink-0 items-center justify-center rounded-lg',
            status === 'on' ? 'bg-graph/12 text-graph-ink' : 'bg-surface-2 text-muted-foreground',
          )}
        >
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <p className={cn('text-sm font-medium', dim ? 'text-muted-foreground' : 'text-foreground')}>
            {title}
          </p>
        </div>
        {metric != null && (
          <span className="tabular-nums shrink-0 font-mono text-[0.72rem] text-muted-foreground">
            {metric}
          </span>
        )}
        <StatusPill status={status} />
      </div>
      {children != null && <div className="mt-2 pl-11">{children}</div>}
    </div>
  )
}

/** A candidate-count chip for one recall arm. */
function ArmChip({
  origin,
  candidates,
  fired,
  showCount,
}: {
  origin: RetrievalOrigin
  candidates: number
  fired: boolean
  showCount: boolean
}): ReactElement {
  const Icon = ARM_ICON[origin] ?? Boxes
  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border px-3 py-2',
        fired ? 'border-border bg-surface-2/50' : 'border-dashed border-border bg-transparent',
      )}
    >
      <Icon className={cn('size-4 shrink-0', fired ? 'text-graph-ink' : 'text-muted-foreground/60')} />
      <div className="min-w-0">
        <p className={cn('truncate text-xs font-medium', fired ? 'text-foreground' : 'text-muted-foreground')}>
          {ORIGIN_LABEL[origin]}
        </p>
        <p className="tabular-nums font-mono text-[0.68rem] text-muted-foreground">
          {fired ? (showCount ? `${candidates} candidates` : 'fired · count n/a') : 'no candidates'}
        </p>
      </div>
    </div>
  )
}

export interface ArsenalPanelProps {
  obs: RetrievalObservability
}

/**
 * The retrieval arsenal made observable — which recall arms fired (with per-arm
 * candidate counts), RRF fusion, rerank (kept/input + top scores), spotlighting,
 * query-rewrite, and the bounded Self-RAG loop — each as a clear on/off + count.
 *
 * Every value here is measured from the run stream. The arsenal stages the web
 * `/query` SSE contract does NOT carry (per-arm counts, spotlight, rewrite,
 * Self-RAG) read as `n/a` rather than a fabricated on/off — they are reported only
 * on a full `retrieval_citations` run.
 */
export function ArsenalPanel({ obs }: ArsenalPanelProps): ReactElement {
  const rr = obs.rerank

  const rrfOn = obs.fusion === 'rrf' || obs.fusion === 'mix'

  return (
    <Card>
      <CardHeader
        eyebrow="which methods actually ran"
        title="Retrieval arsenal"
        description="Every arm, fusion, and post-filter that fired this retrieval — with real, measured counts."
        actions={
          <Badge tone="graph" className="uppercase">
            measured · this run
          </Badge>
        }
      />
      <CardBody className="pt-4">
        {/* Recall arms — the wide-recall stage, per arm + candidate counts. */}
        <div>
          <div className="mb-2 flex items-center gap-2">
            <span className="eyebrow">recall arms</span>
            <span className="text-[0.7rem] text-muted-foreground">
              {obs.arms.filter((a) => a.fired).length}/{obs.arms.length} fired
            </span>
          </div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {obs.arms.map((arm) => {
              const origin = arm.origins[0] ?? 'vector'
              return (
                <ArmChip
                  key={origin}
                  origin={origin}
                  candidates={arm.candidates}
                  fired={arm.fired}
                  showCount={arm.candidates > 0}
                />
              )
            })}
          </div>
        </div>

        {/* The rest of the pipeline, top to bottom. */}
        <div className="mt-3 divide-y divide-border/60 border-t border-border/60">
          <StageRow
            icon={GitMerge}
            title="RRF fusion"
            status={rrfOn ? 'on' : 'off'}
            metric={rrfOn ? `${obs.fused_candidates} fused` : undefined}
          >
            <p className="text-xs text-muted-foreground">
              Reciprocal-rank fusion blends the arms into one ranked pool of{' '}
              <span className="tabular-nums font-medium text-foreground">{obs.fused_candidates}</span>{' '}
              candidates before rerank.
            </p>
          </StageRow>

          <StageRow
            icon={ArrowDownNarrowWide}
            title="LLM rerank"
            status={rr.ran ? 'on' : 'off'}
            metric={rr.ran ? `${rr.kept}/${rr.input_candidates} kept` : undefined}
          >
            {rr.top_scores.length > 0 && (
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-[0.7rem] text-muted-foreground">top scores</span>
                {rr.top_scores.map((s, i) => (
                  <span
                    key={i}
                    className="tabular-nums rounded-md bg-graph/12 px-1.5 py-0.5 font-mono text-[0.68rem] text-graph-ink"
                  >
                    {s.toFixed(2)}
                  </span>
                ))}
              </div>
            )}
          </StageRow>

          <StageRow icon={Highlighter} title="Spotlighting" status="na">
            <p className="text-xs text-muted-foreground">
              Not carried on the /query stream — visible on a full retrieval_citations run.
            </p>
          </StageRow>

          <StageRow icon={PencilLine} title="Query rewrite" status="na">
            <p className="text-xs text-muted-foreground">
              Not carried on the /query stream — set by the agentic layer on a full run.
            </p>
          </StageRow>

          <StageRow icon={Repeat2} title="Self-RAG loop" status="na">
            <p className="text-xs text-muted-foreground">
              Not carried on the /query stream — the bounded loop reports rounds on a full run.
            </p>
          </StageRow>
        </div>
      </CardBody>
    </Card>
  )
}
