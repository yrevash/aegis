'use client'

import { useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent } from '@/components/primitives/card'
import { cn } from '@/lib/utils'
import type { GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { RunState } from '@/state/runReducer'

import { AnswerPanel } from './AnswerPanel'
import { DecisionStrip } from './DecisionStrip'
import type { Beat } from './motion'
import { readSources } from './sources'
import { SourcesTab } from './SourcesTab'
import { TraceTab } from './TraceTab'

/** The three questions a person asks of a finished run, in the order they ask them. */
type TabId = 'answer' | 'sources' | 'trace'

/**
 * The Answer tab — what was asked for, what it is worth, and what it stands on.
 *
 * The trust summary above the answer is the reused {@link DecisionStrip}: guardrails
 * held, sources counted, cost measured. The citation strip below it names the sources
 * the answer actually stands on, with each one's page when the run reported one.
 */
function AnswerTab({ state, onSeeSources }: { state: RunState; onSeeSources: () => void }): ReactElement {
  const cited = readSources(state.retrievalScores).slice(0, 3)

  return (
    <div className="flex flex-col gap-3">
      <DecisionStrip state={state} />
      <AnswerPanel state={state} />

      {cited.length > 0 && (
        <Card>
          <CardContent className="flex flex-wrap items-center gap-2 pt-4">
            <span className="eyebrow mr-1">Stands on</span>
            {cited.map((source) => (
              <span
                key={source.id}
                className="inline-flex max-w-full items-center gap-1.5 rounded-md border border-border bg-surface-2/50 px-2 py-1"
              >
                <span className="max-w-[16rem] truncate text-[0.74rem] text-foreground">
                  {source.label}
                </span>
                {source.page !== null && (
                  <Badge variant="secondary">page {source.page}</Badge>
                )}
                {source.verbatim === 'verified' && <Badge variant="ok">verbatim</Badge>}
                {source.verbatim === 'unverified' && <Badge variant="block">unverified</Badge>}
              </span>
            ))}
            <button
              type="button"
              onClick={onSeeSources}
              className="ml-auto rounded-md px-2 py-1 text-[0.74rem] font-medium text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-2 focus-visible:ring-ring"
            >
              See all sources
            </button>
          </CardContent>
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
}

/**
 * The result tabs — Answer, Sources, Trace.
 *
 * The split follows one rule: the first tab carries what a person asked for, and
 * anything they would only open on purpose gets its own. Three tabs, and the order is
 * the order of the questions — what is the answer, what backs it, how was it produced.
 * They are not numbered, because they are not a sequence a person walks through.
 *
 * Each tab's own empty state says what was missing. None of them draws an empty chart.
 */
export function ResultTabs({ state, graph, metrics, beat }: ResultTabsProps): ReactElement {
  const [tab, setTab] = useState<TabId>('answer')
  const sourceCount = state.retrievalScores.length

  const tabs: { id: TabId; label: string; count: number | null }[] = [
    { id: 'answer', label: 'Answer', count: null },
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
              onClick={() => setTab(entry.id)}
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
      </div>

      <div
        role="tabpanel"
        id={`result-panel-${tab}`}
        aria-labelledby={`result-tab-${tab}`}
        tabIndex={-1}
      >
        {tab === 'answer' && <AnswerTab state={state} onSeeSources={() => setTab('sources')} />}
        {tab === 'sources' && <SourcesTab state={state} />}
        {tab === 'trace' && (
          <TraceTab state={state} graph={graph} metrics={metrics} beat={beat} />
        )}
      </div>
    </div>
  )
}
