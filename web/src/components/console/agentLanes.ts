/**
 * The live run surface, reduced from the event log — pure, so it is testable.
 *
 * Two derivations live here, and the split between them is the honest one:
 *
 * - {@link deriveAgentPanel} groups the events that **carry an agent identity** into one
 *   lane per agent. Lanes are allocated in first-sighting order and never re-ordered,
 *   because a card that moves while it streams is unreadable on a projector.
 * - {@link deriveActivity} collects the events that carry **no** agent identity —
 *   retrieval, the graph delta, guardrail verdicts, routing, reflection, memory. That is
 *   the supervisor's lane, and it is a rail rather than a card because it has no owner.
 *
 * **The degradation is the load-bearing part.** A single-pass run emits no `agent_id` at
 * all (`BaseEvent.agent_id` is optional and absent outside a fan-out), and a run whose
 * fan-out failed to stamp identity looks identical. In both cases this returns exactly
 * one lane — the supervisor — with `attributed: false`. It never fabricates a card per
 * graph node, and it never leaves the panel empty while a run is plainly in progress.
 *
 * No React and no value imports: the module is exercised directly by
 * `web/tests/console/agentLanes.test.mjs`.
 */

import type { Signal } from '@/config/signals'
import type { ToolCall, ToolResult } from '@/lib/stream'
import type { RunState } from '@/state/runReducer'

import { agentIdOf, agentIdOfNode, readSynthesis, type SynthesisView } from './eventViews'

/**
 * A lane's current state.
 *
 * The first seven are the words `AgentStatus.status` uses, kept verbatim rather than
 * re-labelled so the screen and the wire agree. `waiting` and `blocked` are only ever
 * derived for the supervisor lane, which has no `agent_status` of its own.
 */
export type LaneStatus =
  | 'queued'
  | 'started'
  | 'thinking'
  | 'acting'
  | 'done'
  | 'failed'
  | 'timeout'
  | 'waiting'
  | 'blocked'

/** Lane states that will not change again. */
const TERMINAL: ReadonlySet<string> = new Set(['done', 'failed', 'timeout', 'blocked'])

/** Whether a lane has stopped moving. */
export function isTerminal(status: string): boolean {
  return TERMINAL.has(status)
}

/** Whether a lane ended in a designed failure rather than a result. */
export function isFailure(status: string): boolean {
  return status === 'failed' || status === 'timeout'
}

/** The subsystem hue a lane state reads in. */
export function laneSignal(status: string): Signal {
  if (isFailure(status) || status === 'blocked') return 'block'
  if (status === 'done') return 'ok'
  if (status === 'waiting' || status === 'queued') return 'risk'
  return 'agent'
}

/** One tool call, paired with its result when it has returned. */
export interface LaneTool {
  callId: string
  tool: string
  args: Record<string, unknown>
  risk: ToolCall['risk']
  /** `null` while the call is still in flight. */
  ok: boolean | null
  /** The result summary, or `null` while in flight. */
  summary: string | null
}

/** One agent's card. */
export interface AgentLane {
  /** The `agent_id`, or `supervisor` for the unattributed lane. */
  id: string
  label: string
  /** The agent's kind (`research`, `knowledge`, …), or `null` when unreported. */
  role: string | null
  status: LaneStatus
  /** One current-action line — never a scrolling wall. */
  detail: string
  tools: LaneTool[]
  /** Wall-clock across this lane's finished nodes, or `null` when unreported. */
  durationMs: number | null
  /** Cost attributed to this lane's finished nodes, or `null` when unreported. */
  costUsd: number | null
  /** Whether the answer used this lane's findings; `null` before the merge. */
  contributed: boolean | null
  /** Why the merge omitted this lane, when it did. */
  omittedReason: string | null
}

/** Everything the agent panel renders. */
export interface AgentPanelView {
  lanes: AgentLane[]
  /**
   * Whether any event on this run carried an agent identity. `false` means the single
   * lane below is the degraded supervisor view, not a one-agent team.
   */
  attributed: boolean
  /** The fan-out's merge, once it has happened. */
  synthesis: SynthesisView | null
}

/** The lane id used when no event carried an agent identity. */
export const SUPERVISOR_LANE = 'supervisor'

/** Pair tool calls with their results, keeping call order. */
function toolsOf(calls: ToolCall[], results: ToolResult[]): LaneTool[] {
  const byCallId = new Map(results.map((r) => [r.call_id, r]))
  return calls.map((call) => {
    const result = byCallId.get(call.call_id)
    return {
      callId: call.call_id,
      tool: call.tool,
      args: call.args,
      risk: call.risk,
      ok: result === undefined ? null : result.ok,
      summary: result === undefined ? null : result.summary,
    }
  })
}

/** The status the supervisor lane reads, derived from the run's own phase. */
function supervisorStatus(state: RunState): LaneStatus {
  switch (state.phase) {
    case 'idle':
      return 'queued'
    case 'awaiting_approval':
      return 'waiting'
    case 'blocked':
      return 'blocked'
    case 'error':
      return 'failed'
    case 'completed':
      return 'done'
    case 'streaming':
      return state.toolCalls.length > 0 ? 'acting' : 'thinking'
  }
}

/** A mutable lane under construction. */
interface Draft {
  id: string
  label: string
  role: string | null
  status: LaneStatus | null
  detail: string
  calls: ToolCall[]
  results: ToolResult[]
  durationMs: number
  costUsd: number
  measured: boolean
}

function draft(id: string): Draft {
  return {
    id,
    label: id,
    role: null,
    status: null,
    detail: '',
    calls: [],
    results: [],
    durationMs: 0,
    costUsd: 0,
    measured: false,
  }
}

/**
 * Group a run's events into one lane per agent, in allocation order.
 *
 * @param state - The reduced run state for one turn.
 * @returns The lanes, whether they were attributed, and the merge if it happened.
 */
export function deriveAgentPanel(state: RunState): AgentPanelView {
  // Insertion-ordered: a Map preserves first-sighting order, which is the allocation
  // order the cards keep for the whole run.
  const drafts = new Map<string, Draft>()
  const at = (id: string): Draft => {
    const existing = drafts.get(id)
    if (existing !== undefined) return existing
    const created = draft(id)
    drafts.set(id, created)
    return created
  }

  let synthesis: SynthesisView | null = null

  for (const event of state.events) {
    if (event.type === 'agent_status') {
      const id = agentIdOf(event)
      if (id === null) continue
      const lane = at(id)
      if (event.label !== '') lane.label = event.label
      if (event.role !== '') lane.role = event.role
      lane.status = event.status as LaneStatus
      if (event.detail !== '') lane.detail = event.detail
      continue
    }

    const merged = readSynthesis(event)
    if (merged !== null) {
      synthesis = merged
      // The merge names agents that may never have emitted a beat of their own; they
      // are still real lanes of this run, so allocate them here rather than lose them.
      for (const member of [...merged.contributing, ...merged.omitted]) {
        const lane = at(member.agentId)
        if (lane.label === lane.id) lane.label = member.label
        if (lane.role === null && member.role !== '') lane.role = member.role
      }
      continue
    }

    const owner = agentIdOf(event)
    if (owner === null) {
      // A node named `agent:<id>` attributes itself even when the envelope did not.
      if (event.type === 'node_finished') {
        const viaNode = agentIdOfNode(event.node)
        if (viaNode !== null) {
          const lane = at(viaNode)
          lane.durationMs += event.duration_ms
          lane.costUsd += event.cost_usd
          lane.measured = true
        }
      } else if (event.type === 'node_started') {
        const viaNode = agentIdOfNode(event.node)
        if (viaNode !== null) at(viaNode).detail = event.label
      }
      continue
    }

    const lane = at(owner)
    switch (event.type) {
      case 'node_started':
        lane.detail = event.label
        break
      case 'node_finished':
        lane.durationMs += event.duration_ms
        lane.costUsd += event.cost_usd
        lane.measured = true
        break
      case 'tool_call':
        lane.calls.push(event)
        break
      case 'tool_result':
        lane.results.push(event)
        break
      default:
        break
    }
  }

  // No identity anywhere on the wire: one supervisor lane, and say so.
  if (drafts.size === 0) {
    const measured = state.nodeLedger.length > 0
    return {
      lanes: [
        {
          id: SUPERVISOR_LANE,
          label: 'Supervisor',
          role: null,
          status: supervisorStatus(state),
          detail: state.steps.at(-1)?.label ?? '',
          tools: toolsOf(state.toolCalls, state.toolResults),
          durationMs: measured
            ? state.nodeLedger.reduce((sum, n) => sum + n.duration_ms, 0)
            : null,
          costUsd: measured ? state.nodeLedger.reduce((sum, n) => sum + n.cost_usd, 0) : null,
          contributed: null,
          omittedReason: null,
        },
      ],
      attributed: false,
      synthesis: null,
    }
  }

  const contributed = new Set(synthesis?.contributing.map((m) => m.agentId) ?? [])
  const omitted = new Map((synthesis?.omitted ?? []).map((m) => [m.agentId, m]))
  const runOver = !state.running && state.phase !== 'idle'

  const lanes = [...drafts.values()].map((d): AgentLane => {
    const dropped = omitted.get(d.id)
    // A lane the merge dropped wears the merge's terminal state, which is the honest
    // one: the beat that would have said `timeout` is exactly the beat that never came.
    const status: LaneStatus =
      dropped !== undefined && dropped.status !== ''
        ? (dropped.status as LaneStatus)
        : (d.status ?? (runOver ? 'done' : 'started'))
    return {
      id: d.id,
      label: d.label,
      role: d.role,
      status,
      detail:
        dropped !== undefined && dropped.reason !== '' ? dropped.reason : d.detail,
      tools: toolsOf(d.calls, d.results),
      durationMs: d.measured ? d.durationMs : null,
      costUsd: d.measured ? d.costUsd : null,
      contributed: synthesis === null ? null : contributed.has(d.id),
      omittedReason: dropped?.reason ?? null,
    }
  })

  return { lanes, attributed: true, synthesis }
}

/**
 * The lane the panel should expand: the first one still moving, else the first that
 * failed, else the first. Exactly one card is expanded and streaming at a time.
 */
export function focusLane(lanes: AgentLane[]): string | null {
  const live = lanes.find((l) => !isTerminal(l.status))
  if (live !== undefined) return live.id
  const failed = lanes.find((l) => isFailure(l.status))
  if (failed !== undefined) return failed.id
  return lanes[0]?.id ?? null
}

/** How each rail names itself on the activity rail. */
const GUARD_STAGE: Record<string, string> = {
  input: 'Input',
  output: 'Output',
  tool_result: 'Tool result',
}

/** The verdict word, in the tense a person reads. */
const GUARD_VERDICT: Record<string, string> = {
  pass: 'passed',
  block: 'blocked',
  redact: 'redacted',
}

/** One line on the supervisor-level activity rail. */
export interface ActivityItem {
  /** The driving event's sequence number — unique within a run, and the React key. */
  seq: number
  signal: Signal
  /** What happened, in the fewest words that stay true. */
  title: string
  /** The measured detail, or `''` when there is none to show. */
  detail: string
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

/**
 * Collect the run's supervisor-level events — everything with no agent identity that a
 * person would want to watch while the answer forms.
 *
 * Deliberately excludes `reasoning`, `token` and `node_*`: the reasoning lane, the
 * answer and the trace already render those, and repeating them here turns a rail into
 * a log. Events that belong to an agent are excluded too — they are that lane's.
 */
export function deriveActivity(state: RunState): ActivityItem[] {
  const items: ActivityItem[] = []

  for (const event of state.events) {
    if (agentIdOf(event) !== null) continue

    switch (event.type) {
      case 'routing':
        items.push({
          seq: event.seq,
          signal: 'agent',
          title: `Routed to ${event.role} — ${
            event.depth === 'team' ? `team of ${event.fanout}` : 'single lane'
          }`,
          detail:
            event.decided_by === 'user'
              ? `${event.reason} (you chose this width)`
              : `${event.reason} (width chosen by ${event.decided_by.replace('_', ' ')})`,
        })
        break

      case 'reflection':
        items.push({
          seq: event.seq,
          signal: event.done ? 'ok' : 'agent',
          title: `Self-check ${event.iteration} of ${event.max_iterations} — ${
            event.will_retry ? 'retrying' : 'finished'
          }`,
          detail: event.reason,
        })
        break

      case 'memory':
        items.push({
          seq: event.seq,
          signal: 'graph',
          title: 'Memory',
          detail:
            event.recalled_fact_count === 0 && event.recalled_message_count === 0
              ? 'Nothing recalled from earlier turns'
              : `${plural(event.recalled_fact_count, 'fact')} and ` +
                `${plural(event.recalled_message_count, 'earlier turn')} recalled · ` +
                `${event.tokens_used} tokens`,
        })
        break

      case 'retrieval':
        if (event.status === 'started') {
          items.push({ seq: event.seq, signal: 'graph', title: 'Retrieval started', detail: '' })
        } else if (event.status === 'candidates') {
          items.push({
            seq: event.seq,
            signal: 'graph',
            title: 'Recalled candidates',
            detail: plural(event.num_candidates, 'chunk'),
          })
        } else if (event.status === 'reranked') {
          items.push({
            seq: event.seq,
            signal: 'graph',
            title: 'Reranked',
            detail: `${event.scored_sources.length} kept`,
          })
        } else if (event.touched_nodes.length > 0 || event.touched_edges.length > 0) {
          items.push({
            seq: event.seq,
            signal: 'graph',
            title: 'Graph traversed',
            detail: `${plural(event.touched_nodes.length, 'node')} · ${plural(
              event.touched_edges.length,
              'edge',
            )}`,
          })
        }
        break

      case 'guardrail':
        items.push({
          seq: event.seq,
          signal: event.verdict === 'pass' ? 'ok' : 'block',
          title: `${GUARD_STAGE[event.stage]} rail — ${GUARD_VERDICT[event.verdict]}`,
          detail: event.reason,
        })
        break

      case 'provenance':
        items.push({
          seq: event.seq,
          signal: 'graph',
          title: event.cache_hit ? 'Served from cache' : 'Context assembled',
          detail:
            event.origins.length > 0
              ? `${event.origins.join(' + ')} · fused by ${event.fusion}`
              : '',
        })
        break

      case 'approval_required':
        items.push({
          seq: event.seq,
          signal: 'risk',
          title: 'Paused for your approval',
          detail: event.rationale,
        })
        break

      case 'approval_queued':
        items.push({
          seq: event.seq,
          signal: 'risk',
          title: 'Sent to the approvals inbox',
          detail: event.action,
        })
        break

      case 'budget_exceeded':
        items.push({ seq: event.seq, signal: 'block', title: 'Budget stop', detail: event.message })
        break

      case 'error':
        items.push({ seq: event.seq, signal: 'block', title: 'Run failed', detail: event.message })
        break

      default:
        break
    }
  }

  return items
}
