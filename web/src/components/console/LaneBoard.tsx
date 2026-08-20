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
import { reasoningChunksByLane, reasoningSteps } from './laneStream'

/** A lane that has not reasoned yet. One frozen instance, so no render allocates one. */
const EMPTY_THINKING: readonly string[] = []

/** Compact preview of a tool call's arguments — the first value, elided. */
function argPreview(args: Record<string, unknown>): string {
  const first = Object.values(args)[0]
  if (first === undefined) return ''
  const text = typeof first === 'string' ? first : JSON.stringify(first)
  return text.length > 30 ? `${text.slice(0, 29)}…` : text
}

/**
 * One tool call as a discrete chip: what was called, and what came back.
 *
 * ## Why a failed call is not painted as an error
 *
 * A run watched by the owner called `load_skill` twice for a skill that had been
 * removed, and both times the console printed the server's whole sentence — *"No skill
 * named 'Refund Policy' is in force for you…"* — in block red, inline, mid-transcript.
 * It read as the product breaking. It is the opposite: a tool returned nothing, the
 * agent saw that, and the self-repair loop went round again. That is the system working
 * exactly as designed.
 *
 * So an unsuccessful call keeps every word the server said, and says it as an *outcome*:
 * the amber risk tone rather than the red block tone (nothing was blocked and nothing is
 * unsafe), a plain "no result" verdict beside the tool name, and the sentence itself on
 * its own line in ordinary muted ink rather than as a coloured dump. The block tone
 * stays reserved for what it means everywhere else in this product — a rail that
 * refused.
 */
function ToolChip({ tool }: { tool: LaneTool }): ReactElement {
  const pending = tool.ok === null
  const failed = tool.ok === false
  const detail = failed && tool.summary !== null && tool.summary !== '' ? tool.summary : null

  return (
    <li
      className={cn(
        'flex min-w-0 flex-col gap-0.5 rounded-md border px-2 py-1',
        pending
          ? 'border-blue-200/50 bg-blue-200/[0.08]'
          : failed
            ? 'border-risk/40 bg-risk/[0.07]'
            : 'border-border bg-surface-2/60',
      )}
    >
      <div className="flex min-w-0 items-baseline gap-1.5 font-mono text-[0.68rem]">
        <span className="shrink-0 font-medium text-foreground">{tool.tool}</span>
        <span className="truncate text-muted-foreground">({argPreview(tool.args)})</span>
        <span
          className={cn(
            'ml-auto shrink-0 pl-1',
            pending ? 'text-blue-700' : failed ? 'text-risk-ink' : 'text-muted-foreground',
          )}
        >
          {pending ? 'running' : failed ? 'no result' : (tool.summary ?? 'ok')}
        </span>
      </div>
      {detail !== null && (
        <p className="text-[0.68rem] leading-snug text-muted-foreground">{detail}</p>
      )}
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
  chunks,
  live,
  className,
}: {
  chunks: readonly string[]
  live: boolean
  className?: string
}): ReactElement {
  const ref = useRef<HTMLOListElement>(null)
  const steps = reasoningSteps(chunks)

  useEffect(() => {
    const el = ref.current
    if (el !== null) el.scrollTop = el.scrollHeight
  }, [chunks])

  return (
    <ol
      ref={ref}
      className={cn(
        'flex max-h-32 flex-col gap-1 overflow-y-auto rounded-md px-2 py-1.5',
        className ?? 'bg-surface-2/60',
      )}
    >
      {steps.map((step, index) => (
        <li
          key={`${index}-${step.slice(0, 24)}`}
          className="animate-trace-in flex min-w-0 items-start gap-1.5 text-[0.72rem] leading-snug break-words text-foreground/80"
        >
          <span
            aria-hidden
            className="tabular mt-px shrink-0 font-mono text-[0.62rem] text-muted-foreground/70"
          >
            {index + 1}
          </span>
          <span className="min-w-0">
            {step}
            {live && index === steps.length - 1 && (
              <span
                aria-hidden
                className="ml-0.5 inline-block h-3 w-1 translate-y-0.5 animate-pulse bg-blue-700 align-baseline"
              />
            )}
          </span>
        </li>
      ))}
    </ol>
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
  thinking: readonly string[]
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

      {thinking.length > 0 && <LaneThinking chunks={thinking} live={live} />}

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
 * What the lane board is called before any lane has reported.
 *
 * The width is known from `routing` long before the first `agent_status` arrives, and
 * for those seconds this panel used to announce "Single lane" over a run the router had
 * just sized as a team of two. Read the routing event when there is one; fall back to
 * the lane count only when there is not.
 */
function laneHeading(state: RunState, attributed: boolean, count: number): string {
  if (attributed) return `Agents · ${count}`
  const routing = state.routing
  if (routing !== null && routing.depth === 'team') return `Team of ${routing.fanout}`
  return 'Single lane'
}

/**
 * The one line under the heading, when no lane has reported its own identity.
 *
 * Three different facts used to share one sentence — *"This run reported no per-agent
 * identity, so it reads as one lane"* — and only the last of them was true. A run the
 * router sized for one agent is a **decision**, and saying so with `decided_by` beside
 * it is the receipt. A team whose lanes have not checked in yet is simply **early**. The
 * degraded sentence is for what is left: a run that finished without ever stamping an
 * identity, which is the only case where the panel really is showing less than happened.
 */
function laneSentence(state: RunState): string {
  const routing = state.routing
  if (routing === null) {
    return 'This run reported no per-agent identity, so it reads as one lane.'
  }
  if (routing.depth !== 'team') {
    return `Sized for one agent — ${routing.decided_by} chose a single lane for this question, and it ran the whole turn.`
  }
  return state.running
    ? `Sized for a team of ${routing.fanout} — each lane appears here the moment its agent reports in.`
    : `Sized for a team of ${routing.fanout}, but no event carried an agent identity, so this reads as one lane.`
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
  const thinking = reasoningChunksByLane(state)
  const reduced = useReducedMotion() ?? false
  const supervisorThinking = lanes.some((lane) => lane.id === SUPERVISOR_LANE)
    ? []
    : (thinking.get(SUPERVISOR_LANE) ?? [])

  return (
    <section aria-label="Agents" className="flex flex-col gap-2">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="flex items-center gap-2">
          <Users aria-hidden className="size-4 text-muted-foreground" />
          <h3 className="eyebrow">{laneHeading(state, attributed, lanes.length)}</h3>
        </span>
        <RoutingReceipt state={state} />
      </header>

      {!attributed && (
        <p className="text-[0.75rem] text-muted-foreground">{laneSentence(state)}</p>
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
      {supervisorThinking.length > 0 && (
        <div className="flex flex-col gap-1.5 rounded-lg border border-blue-200 bg-blue-50 p-3">
          <span className="flex items-center gap-1.5 text-[0.72rem] font-medium text-blue-700">
            <BrainCircuit aria-hidden className="size-3.5 shrink-0" />
            Supervisor reasoning
          </span>
          <LaneThinking chunks={supervisorThinking} live={state.running} className="bg-surface/70" />
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
              thinking={thinking.get(lane.id) ?? EMPTY_THINKING}
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
