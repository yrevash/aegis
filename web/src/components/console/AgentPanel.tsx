'use client'

import { ChevronRight, Users } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { Badge } from '@/components/primitives/badge'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import {
  deriveAgentPanel,
  focusLane,
  isFailure,
  isTerminal,
  laneSignal,
  type AgentLane,
  type LaneTool,
} from './agentLanes'

/** Compact preview of a tool call's arguments — the first value, elided. */
function argPreview(args: Record<string, unknown>): string {
  const first = Object.values(args)[0]
  if (first === undefined) return ''
  const text = typeof first === 'string' ? first : JSON.stringify(first)
  return text.length > 34 ? `${text.slice(0, 33)}…` : text
}

/** One tool call as a discrete chip: what was called, and what came back. */
function ToolChip({ tool }: { tool: LaneTool }): ReactElement {
  const pending = tool.ok === null
  return (
    <li
      className={cn(
        'flex min-w-0 items-baseline gap-1.5 rounded-md border px-2 py-1 font-mono text-[0.7rem]',
        pending
          ? 'border-agent/40 bg-agent/[0.06] text-agent-ink'
          : tool.ok
            ? 'border-border bg-surface-2/50 text-muted-foreground'
            : 'border-block/50 bg-block/10 text-block-ink',
      )}
    >
      <span className="shrink-0 font-medium text-foreground">{tool.tool}</span>
      <span className="truncate">({argPreview(tool.args)})</span>
      <span className="ml-auto shrink-0 pl-1">
        {pending ? 'running' : `→ ${tool.summary ?? (tool.ok ? 'ok' : 'failed')}`}
      </span>
    </li>
  )
}

/** One agent's card: a status word, one current-action line, and its tool chips. */
function LaneCard({
  lane,
  expanded,
  onToggle,
}: {
  lane: AgentLane
  expanded: boolean
  onToggle: () => void
}): ReactElement {
  const signal = laneSignal(lane.status)
  const token = SIGNALS[signal]
  const finished = isTerminal(lane.status)
  const failed = isFailure(lane.status)

  return (
    <li
      className={cn(
        'rounded-lg border bg-card transition-colors',
        failed ? 'border-block/50' : finished ? 'border-border' : token.border,
        finished && !failed && 'bg-surface-2/30',
      )}
    >
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={expanded}
        className="flex w-full items-center gap-2 rounded-lg px-3 py-2.5 text-left outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
      >
        <span
          aria-hidden
          className={cn('size-2 shrink-0 rounded-full', !finished && 'animate-pip')}
          style={{ backgroundColor: token.hex }}
        />
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-sm font-medium',
            finished && !failed ? 'text-muted-foreground' : 'text-foreground',
          )}
        >
          {lane.label}
        </span>
        {lane.role !== null && lane.role !== '' && (
          <Badge variant="outline" className="hidden sm:inline-flex">
            {lane.role}
          </Badge>
        )}
        <Badge variant={failed ? 'block' : finished ? 'secondary' : 'agent'}>{lane.status}</Badge>
        <ChevronRight
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform',
            expanded && 'rotate-90',
          )}
        />
      </button>

      <div className="px-3 pb-2.5 -mt-1">
        <p
          className={cn(
            'truncate text-[0.78rem]',
            failed ? 'text-block-ink' : 'text-muted-foreground',
          )}
        >
          {lane.detail === '' ? (finished ? 'No detail reported.' : 'Working…') : lane.detail}
        </p>

        {(lane.durationMs !== null || lane.costUsd !== null) && (
          <p className="mt-1 font-mono text-[0.7rem] text-muted-foreground">
            {lane.durationMs !== null && `${Math.round(lane.durationMs)} ms`}
            {lane.durationMs !== null && lane.costUsd !== null && ' · '}
            {lane.costUsd !== null && `$${lane.costUsd.toFixed(4)}`}
          </p>
        )}

        {expanded && lane.tools.length > 0 && (
          <ul className="mt-2 flex flex-col gap-1">
            {lane.tools.map((tool) => (
              <ToolChip key={tool.callId} tool={tool} />
            ))}
          </ul>
        )}
        {expanded && lane.tools.length === 0 && (
          <p className="mt-2 text-[0.72rem] text-muted-foreground/80">No tools called.</p>
        )}
      </div>
    </li>
  )
}

/**
 * The agent panel — one card per agent, allocated the moment that agent first appears
 * on the wire and never re-ordered afterwards.
 *
 * When a run reports no agent identity at all — which is every single-pass run, since
 * `agent_id` is absent outside a fan-out — {@link deriveAgentPanel} returns one
 * supervisor lane and this says so in a line, rather than inventing a card per graph
 * node. That degradation is the difference between a panel that is honest about a
 * one-lane run and one that dresses it up as a team.
 */
export function AgentPanel({ state }: { state: RunState }): ReactElement {
  const { lanes, attributed, synthesis } = deriveAgentPanel(state)
  const [chosen, setChosen] = useState<string | null>(null)
  // The live lane is expanded by default; an explicit click wins from then on.
  const expandedId = chosen ?? focusLane(lanes)

  return (
    <section aria-label="Agents" className="flex flex-col gap-2">
      <header className="flex items-center gap-2">
        <Users aria-hidden className="size-4 text-muted-foreground" />
        <h3 className="eyebrow">{attributed ? `Agents · ${lanes.length}` : 'Agent'}</h3>
      </header>

      {!attributed && (
        <p className="text-[0.75rem] text-muted-foreground">
          This run reported no per-agent identity, so it reads as one lane.
        </p>
      )}

      <ul className="flex flex-col gap-2">
        {lanes.map((lane) => (
          <LaneCard
            key={lane.id}
            lane={lane}
            expanded={expandedId === lane.id}
            // '' collapses everything: a chosen-but-empty id still wins over the default.
            onToggle={() => setChosen(expandedId === lane.id ? '' : lane.id)}
          />
        ))}
      </ul>

      {synthesis !== null && synthesis.summary !== '' && (
        <p
          className={cn(
            'rounded-lg border px-3 py-2 text-[0.78rem]',
            synthesis.omitted.length > 0
              ? 'border-risk/50 bg-risk/10 text-risk-ink'
              : 'border-border bg-surface-2/40 text-muted-foreground',
          )}
        >
          {synthesis.summary}
        </p>
      )}
    </section>
  )
}
