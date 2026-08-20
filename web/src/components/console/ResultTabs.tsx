'use client'

import type { ReactElement, ReactNode } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { cn } from '@/lib/utils'
import type { GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { RunState } from '@/state/runReducer'

import { AnswerPanel } from './AnswerPanel'
import { DecisionStrip } from './DecisionStrip'
import type { Beat } from './motion'
import { readSources } from './sources'
import { SourcesTab } from './SourcesTab'
import { TraceTab } from './TraceTab'

/** What a person opens on purpose, once the answer is already in front of them. */
export type ResultTabId = 'sources' | 'trace'

/**
 * The answer, and what it stands on — always in the flow, never behind a tab.
 *
 * This used to be the first of three tabs, which meant the thing the person actually
 * asked for only existed once the run *settled* and only if they were on the right tab.
 * A console shaped like a conversation streams the answer under the question; the two
 * surfaces that are genuinely secondary — every source, and the full trace — keep their
 * tabs.
 *
 * The trust summary above the answer is the reused {@link DecisionStrip}: guardrails
 * held, sources counted, cost measured. The citation strip below names the sources the
 * answer actually stands on, with each one's page when the run reported one.
 */
export function AnswerBlock({
  state,
  onSeeSources,
}: {
  state: RunState
  onSeeSources: () => void
}): ReactElement {
  const cited = readSources(state.retrievalScores).slice(0, 3)
  const settled = !state.running && state.events.length > 0

  return (
    <div className="flex flex-col gap-3">
      {settled && <DecisionStrip state={state} />}
      <AnswerPanel state={state} />

      {cited.length > 0 && (
        <Card>
          <CardBody className="flex flex-wrap items-center gap-2 pt-4">
            <span className="eyebrow mr-1">Stands on</span>
            {cited.map((source) => (
              <span
                key={source.id}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface-2/50 px-2 py-1"
              >
                <span className="max-w-[16rem] truncate text-[0.74rem] text-foreground">
                  {source.label}
                </span>
                {source.page !== null && <Badge tone="neutral">page {source.page}</Badge>}
                {source.verbatim === 'verified' && <Badge tone="ok">verbatim</Badge>}
                {source.verbatim === 'unverified' && <Badge tone="block">unverified</Badge>}
              </span>
            ))}
            <button
              type="button"
              onClick={onSeeSources}
              className="ml-auto rounded-md px-2 py-1 text-[0.74rem] font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
            >
              See all sources
            </button>
          </CardBody>
        </Card>
      )}
    </div>
  )
}

interface ResultTabsProps {
  state: RunState
  graph: GraphResponse
  metrics: MetricsResponse | null
  beat: Beat | null
  /** Controlled, so the answer's "See all sources" link can open the right one. */
  tab: ResultTabId
  onTab: (tab: ResultTabId) => void
  /**
   * What can be *done* with this settled turn, right-aligned in the tab row.
   *
   * It sits with the tabs rather than under the answer because both are the same kind
   * of thing — what a person opens or does once the answer is already in front of them
   * — and a second row of controls under a settled turn is a row of chrome between one
   * turn and the next.
   */
  actions?: ReactNode
}

/**
 * The two surfaces a person opens on purpose: every source, and the whole trace.
 *
 * The order is the order of the questions — what backs the answer, and how it was
 * produced. They are not numbered, because they are not a sequence anybody walks
 * through. Each tab's own empty state says what was missing; none of them draws an
 * empty chart.
 */
export function ResultTabs({
  state,
  graph,
  metrics,
  beat,
  tab,
  onTab,
  actions,
}: ResultTabsProps): ReactElement {
  const sourceCount = state.retrievalScores.length

  const tabs: { id: ResultTabId; label: string; count: number | null }[] = [
    { id: 'sources', label: 'Sources', count: sourceCount > 0 ? sourceCount : null },
    { id: 'trace', label: 'Trace', count: null },
  ]

  return (
    <div className="flex flex-col gap-3">
      <div role="tablist" aria-label="Result" className="flex flex-wrap items-center gap-1">
        {tabs.map((entry) => {
          const selected = tab === entry.id
          return (
            <button
              key={entry.id}
              type="button"
              role="tab"
              id={`result-tab-${entry.id}`}
              aria-selected={selected}
              aria-controls={`result-panel-${entry.id}`}
              onClick={() => onTab(entry.id)}
              className={cn(
                'inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                'outline-none focus-visible:ring-2 focus-visible:ring-ring',
                selected
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
              )}
            >
              {entry.label}
              {entry.count !== null && (
                <span
                  className={cn(
                    'font-mono text-[0.68rem]',
                    selected ? 'text-primary-foreground/70' : 'text-muted-foreground/70',
                  )}
                >
                  {entry.count}
                </span>
              )}
            </button>
          )
        })}
        {actions != null && <div className="ml-auto flex items-center gap-2">{actions}</div>}
      </div>

      <div
        role="tabpanel"
        id={`result-panel-${tab}`}
        aria-labelledby={`result-tab-${tab}`}
        tabIndex={-1}
      >
        {tab === 'sources' && <SourcesTab state={state} />}
        {tab === 'trace' && <TraceTab state={state} graph={graph} metrics={metrics} beat={beat} />}
      </div>
    </div>
  )
}
