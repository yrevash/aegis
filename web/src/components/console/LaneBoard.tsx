'use client'

import { BrainCircuit, Route, Users } from 'lucide-react'
import { motion, useReducedMotion } from 'motion/react'
import { useEffect, useRef, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import {
  SUPERVISOR_LANE,
  deriveAgentPanel,
  isFailure,
  isTerminal,
  laneSignal,
  type AgentLane,
  type LaneTool,
} from './agentLanes'
import { reasoningByLane } from './laneStream'

/** Compact preview of a tool call's arguments — the first value, elided. */
function argPreview(args: Record<string, unknown>): string {
  const first = Object.values(args)[0]
  if (first === undefined) return ''
  const text = typeof first === 'string' ? first : JSON.stringify(first)
  return text.length > 30 ? `${text.slice(0, 29)}…` : text
}

/** One tool call as a discrete chip: what was called, and what came back. */
function ToolChip({ tool }: { tool: LaneTool }): ReactElement {
  const pending = tool.ok === null
  return (
    <li
      className={cn(
        'flex min-w-0 items-baseline gap-1.5 rounded-md border px-2 py-1 font-mono text-[0.68rem]',
        pending
          ? 'border-blue-200/50 bg-blue-200/[0.08] text-blue-700'
          : tool.ok
            ? 'border-border bg-surface-2/60 text-muted-foreground'
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

/**
 * The lane's own thinking, streaming inside the lane's own card.
 *
 * Scrolls itself to the newest chunk and caps its height, because a lane card that grows
 * without bound pushes every *other* lane off screen — which destroys the one thing this
 * layout exists to show, that four agents are working at once.
 */
function LaneThinking({
  text,
  live,
  className,
}: {
  text: string
  live: boolean
  className?: string
}): ReactElement {
  const ref = useRef<HTMLParagraphElement>(null)

  useEffect(() => {
    const el = ref.current
    if (el !== null) el.scrollTop = el.scrollHeight
  }, [text])

  return (
    <p
      ref={ref}
      className={cn(
        'max-h-24 overflow-y-auto rounded-md px-2 py-1.5 font-mono text-[0.7rem] leading-relaxed break-words text-foreground/75',
        className ?? 'bg-surface-2/60',
      )}
    >
      {text}
      {live && (
        <span
          aria-hidden
          className="ml-0.5 inline-block h-3 w-1 translate-y-0.5 animate-pulse bg-blue-700 align-baseline"
        />
      )}
    </p>
  )
}

/** One agent's live card: status, its own reasoning, its own tools, its own bill. */
function LaneCard({
  lane,
  thinking,
  index,
  reduced,
}: {
  lane: AgentLane
  thinking: string
  index: number
  reduced: boolean
}): ReactElement {
  const token = SIGNALS[laneSignal(lane.status)]
  const finished = isTerminal(lane.status)
  const failed = isFailure(lane.status)
  const live = !finished
  // `AgentStatus.label` is a plain string on the wire and the shipped agents send it
  // empty, which left a card whose name was a zero-width gap between two badges. The
  // role is the next most specific true thing, and the id is always there.
  const name = lane.label !== '' ? lane.label : (lane.role !== null && lane.role !== '' ? lane.role : lane.id)
  const showRole = lane.role !== null && lane.role !== '' && lane.role !== name

  return (
    <motion.li
      layout={!reduced}
      initial={reduced ? false : { opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: reduced ? 0 : 0.2, delay: reduced ? 0 : Math.min(index, 5) * 0.04 }}
      className={cn(
        '@container/lane flex min-w-0 flex-col gap-2 rounded-lg border bg-card p-3',
        'transition-shadow duration-[var(--dur-base)]',
        failed ? 'border-block/50' : live ? token.border : 'border-border',
        live && 'shadow-card',
        finished && !failed && 'bg-surface-2/30',
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        <span
          aria-hidden
          className={cn('size-2 shrink-0 rounded-full', live && 'animate-pip')}
          style={{ backgroundColor: token.hex, ['--pip-color' as string]: token.hex }}
        />
        <span
          className={cn(
            'min-w-0 flex-1 truncate text-sm font-medium',
            finished && !failed ? 'text-muted-foreground' : 'text-foreground',
          )}
          title={name}
        >
          {name}
        </span>
        {showRole && (
          <Badge tone="neutral" className="@[15rem]/lane:inline-flex hidden shrink-0">
            {lane.role}
          </Badge>
        )}
        <Badge tone={failed ? 'block' : finished ? 'neutral' : 'agent'} className="shrink-0">
          {lane.status}
        </Badge>
      </div>

      <p
        className={cn(
          'line-clamp-2 text-[0.76rem] leading-snug',
          failed ? 'text-block-ink' : 'text-muted-foreground',
        )}
      >
        {lane.detail === '' ? (finished ? 'No detail reported.' : 'Working…') : lane.detail}
      </p>

      {thinking !== '' && <LaneThinking text={thinking} live={live} />}

      {lane.tools.length > 0 && (
        <ul className="flex flex-col gap-1">
          {lane.tools.map((tool) => (
            <ToolChip key={tool.callId} tool={tool} />
          ))}
        </ul>
      )}

      <div className="mt-auto flex flex-wrap items-center gap-x-2 gap-y-1 pt-0.5">
        {(lane.durationMs !== null || lane.costUsd !== null) && (
          <span className="tabular font-mono text-[0.68rem] text-muted-foreground">
            {lane.durationMs !== null && `${Math.round(lane.durationMs)} ms`}
            {lane.durationMs !== null && lane.costUsd !== null && ' · '}
            {lane.costUsd !== null && `$${lane.costUsd.toFixed(4)}`}
          </span>
        )}
        {/* The merge's verdict on this lane, kept on the lane rather than only in the
            summary: an omitted agent that is not named on its own card reads as a lane
            that simply stopped. */}
        {lane.contributed === true && (
          <Badge tone="ok" className="ml-auto">
            in the answer
          </Badge>
        )}
        {lane.contributed === false && (
          <span className="ml-auto min-w-0" title={lane.omittedReason ?? undefined}>
            <Badge tone="risk">
              {lane.omittedReason !== null && lane.omittedReason !== ''
                ? `omitted · ${lane.omittedReason}`
                : 'omitted'}
            </Badge>
          </span>
        )}
      </div>
    </motion.li>
  )
}

/**
 * The routing receipt — the width this turn ran at, and who chose it.
 *
 * Never the number alone. `decided_by` is what separates "the classifier sized this
 * team" from "the platform cap clamped what you asked for", and a width with no
 * explanation is exactly the figure an audience stops trusting.
 */
function RoutingReceipt({ state }: { state: RunState }): ReactElement | null {
  const routing = state.routing
  if (routing === null) return null
  const width = routing.depth === 'team' ? `team of ${routing.fanout}` : 'single lane'
  return (
    <span className="inline-flex min-w-0 items-center gap-1.5 text-[0.72rem] text-muted-foreground">
      <Route aria-hidden className="size-3.5 shrink-0" />
      <span className="truncate">
        {routing.role} · {width} · chosen by {routing.decided_by}
        {routing.used_llm ? ' (llm tiebreak)' : ''}
      </span>
    </span>
  )
}

/**
 * The live agent lanes — one card per agent, side by side, each streaming its own work.
 *
 * The cards are laid out in a grid rather than a list for one reason: a fan-out is
 * *concurrent*, and a vertical stack renders four simultaneous agents as a queue. Each
 * card carries its own reasoning, its own tool calls and its own bill, so "four agents
 * are working right now" is something the screen shows rather than something a caption
 * claims.
 *
 * A lane is allocated the moment its agent first appears on the wire and is never
 * re-ordered, so nothing moves under the eye mid-run.
 *
 * When a run reports no agent identity at all — every single-pass run, since `agent_id`
 * is absent outside a fan-out — {@link deriveAgentPanel} returns one supervisor lane and
 * this says so in a line, rather than dressing a one-lane run up as a team.
 */
export function LaneBoard({ state }: { state: RunState }): ReactElement {
  const { lanes, attributed, synthesis } = deriveAgentPanel(state)
  const thinking = reasoningByLane(state)
  const reduced = useReducedMotion() ?? false
  const supervisorThinking = lanes.some((lane) => lane.id === SUPERVISOR_LANE)
    ? ''
    : (thinking.get(SUPERVISOR_LANE) ?? '')

  return (
    <section aria-label="Agents" className="flex flex-col gap-2">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-muted-foreground" />
          <h3 className="eyebrow">{attributed ? `Agents · ${lanes.length}` : 'Agent'}</h3>
        </span>
        <RoutingReceipt state={state} />
      </header>

      {!attributed && (
        <p className="text-[0.75rem] text-muted-foreground">
          This run reported no per-agent identity, so it reads as one lane.
        </p>
      )}

      {/* Container queries, not viewport breakpoints. The lane board lives in a column
          whose width has nothing to do with the window's: at 1440px with both rails out
          it is about 530px, so `sm:grid-cols-2` — which fires at a 640px *window* — put
          two cards in 165px each and wrapped every lane's reasoning at three words. The
          grid answers to the box it is actually in. */}
      {/* The supervisor's own thinking, when no card claims it.
          On a fan-out `deriveAgentPanel` returns one card per sub-agent and none for the
          supervisor, so the planner's chain-of-thought — every `reasoning` chunk that
          arrived with no `agent_id` — belonged to nobody and was dropped on the floor.
          It is the run deciding how to answer, which is worth a strip of its own. */}
      {supervisorThinking !== '' && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-blue-200 bg-blue-50 p-3">
          <span className="flex items-center gap-1.5 text-[0.72rem] font-medium text-blue-700">
            <BrainCircuit aria-hidden className="size-3.5 shrink-0" />
            Supervisor reasoning
          </span>
          <LaneThinking text={supervisorThinking} live={state.running} className="bg-surface/70" />
        </div>
      )}

      <div className="@container/lanes">
        <ul
          className={cn(
            'grid gap-2',
            lanes.length > 1 && '@[30rem]/lanes:grid-cols-2',
            lanes.length > 4 && '@[48rem]/lanes:grid-cols-3',
          )}
        >
          {lanes.map((lane, index) => (
            <LaneCard
              key={lane.id}
              lane={lane}
              thinking={thinking.get(lane.id) ?? ''}
              index={index}
              reduced={reduced}
            />
          ))}
        </ul>
      </div>

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
