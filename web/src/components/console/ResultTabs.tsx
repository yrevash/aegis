'use client'

import { useId, useRef, type KeyboardEvent, type ReactElement, type ReactNode } from 'react'

import { cn } from '@/lib/utils'
import type { GraphResponse, MetricsResponse } from '@/lib/api/types'
import type { RunState } from '@/state/runReducer'

import { AnswerPanel } from './AnswerPanel'
import { DecisionStrip } from './DecisionStrip'
import type { Beat } from './motion'
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
 * held, sources counted, cost measured. The citation strip naming what the answer stands
 * on now closes {@link AnswerPanel} itself — it was a `Card` directly under a `Card`,
 * which DESIGN.md §1 rules out, and it was talking about the panel above it.
 */
export function AnswerBlock({
  state,
  onSeeSources,
}: {
  state: RunState
  onSeeSources: () => void
}): ReactElement {
  const settled = !state.running && state.events.length > 0

  return (
    /* `data-answer` is the console's scroll target once a run settles. The thread used
       to scroll to the *end* of the finished turn, which parked the reader below the
       answer on the tail of the ranked-source list — the one thing on the turn nobody
       asked for. See the follow-the-newest-turn effect in `ChatConsole`. */
    <div data-answer="" className="flex scroll-mt-2 flex-col gap-3">
      {settled && <DecisionStrip state={state} />}
      <AnswerPanel state={state} onSeeSources={onSeeSources} />
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
  const buttons = useRef<Partial<Record<ResultTabId, HTMLButtonElement | null>>>({})
  // A thread renders one of these per settled turn, so `result-tab-sources` was a
  // duplicate id from the second turn on and every `aria-controls` resolved to the
  // first turn's panel. The prefix is per instance.
  const uid = useId()

  const tabs: { id: ResultTabId; label: string; count: number | null }[] = [
    { id: 'sources', label: 'Sources', count: sourceCount > 0 ? sourceCount : null },
    { id: 'trace', label: 'Trace', count: null },
  ]

  /*
   * The WAI tablist pattern: one tab stop for the whole set, and the arrows move
   * between them.
   *
   * Every tab used to be its own tab stop, so a keyboard user tabbing through a settled
   * turn walked *into* the tab row and then had to walk out of it again — and with more
   * turns in the thread that cost grows with the transcript. `Home`/`End` jump to the
   * ends. Selection follows focus, which is correct here because both panels are already
   * mounted-on-demand and cheap to swap.
   */
  const move = (event: KeyboardEvent<HTMLButtonElement>): void => {
    const from = tabs.findIndex((entry) => entry.id === tab)
    let to = -1
    if (event.key === 'ArrowRight') to = (from + 1) % tabs.length
    else if (event.key === 'ArrowLeft') to = (from - 1 + tabs.length) % tabs.length
    else if (event.key === 'Home') to = 0
    else if (event.key === 'End') to = tabs.length - 1
    else return
    event.preventDefault()
    const next = tabs[to]
    if (next === undefined) return
    onTab(next.id)
    buttons.current[next.id]?.focus()
  }

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
              id={`${uid}-tab-${entry.id}`}
              ref={(node) => {
                buttons.current[entry.id] = node
              }}
              aria-selected={selected}
              /* Only the selected panel is mounted — the Flow graph measures its own
                 container on mount and a hidden one measures 0×0 — so an unselected
                 tab points at nothing rather than at a missing id. */
              aria-controls={selected ? `${uid}-panel-${entry.id}` : undefined}
              tabIndex={selected ? 0 : -1}
              onKeyDown={move}
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
        id={`${uid}-panel-${tab}`}
        aria-labelledby={`${uid}-tab-${tab}`}
        tabIndex={-1}
      >
        {tab === 'sources' && <SourcesTab state={state} />}
        {tab === 'trace' && <TraceTab state={state} graph={graph} metrics={metrics} beat={beat} />}
      </div>
    </div>
  )
}
