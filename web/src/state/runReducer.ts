/**
 * Pure reduction of the {@link StreamEvent} log into renderable run state.
 *
 * Keeping this a pure function (no React, no side effects) means the whole
 * money-shot is deterministic and unit-testable: feed the scripted event list
 * and assert the resulting state. The `useRunStream` hook is the only place
 * that wires this to a live transport.
 */

import type { Signal } from '@/config/signals'
import { signalForEvent } from '@/config/signals'
import type {
  AgentStatus,
  ApprovalQueued,
  ApprovalRequired,
  BudgetExceeded,
  Guardrail,
  GraphEdge,
  GraphNode,
  MemoryEvent,
  NodeFinished,
  NodeStarted,
  Provenance,
  Reflection,
  RoutingEvent,
  ScoredSource,
  StreamEvent,
  StreamEventType,
  SynthesisEvent,
  ToolCall,
  ToolResult,
} from '@/lib/stream'

/** Coarse lifecycle status derived from the event stream. */
export type RunPhase =
  | 'idle'
  | 'streaming'
  | 'awaiting_approval'
  | 'completed'
  | 'blocked'
  | 'rejected'
  | 'error'

/** Aggregated usage for the token/cost dashboard. */
export interface RunUsage {
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  cache_hit: boolean
}

/**
 * The last event's subsystem hue plus its sequence number. Every event updates
 * this, so the UI can drive an "active-beat" pulse keyed on `seq` even when two
 * consecutive events share a hue.
 */
export interface LastSignal {
  signal: Signal
  seq: number
}

/** Everything the six views need, reduced from the raw event log. */
export interface RunState {
  phase: RunPhase
  /**
   * Whether a run is currently active. Driven entirely by the event stream (set
   * on `run_started`, cleared on `run_finished`/`error`) and by an explicit
   * close, so it stays correct even if a transport ends without a terminal
   * event. See {@link runReducer} and the `useRunStream` hook.
   */
  running: boolean
  runId: string | null
  traceId: string | null
  /** The full ordered event log — the trace panel renders this verbatim. */
  events: StreamEvent[]
  /** `node_started` steps, in order (the plan/timeline). */
  steps: NodeStarted[]
  /**
   * `node_finished` payloads, in order — the glass-box ledger of what each node
   * cost (model, tokens, duration, USD). Powers the per-step cost breakdown.
   */
  nodeLedger: NodeFinished[]
  /** Accumulated planner chain-of-thought from `reasoning` chunks. */
  reasoning: string
  /** Guardrail verdicts seen so far (each now carries layer/redactions/masking). */
  guardrails: Guardrail[]
  /** Tool calls the agent issued. */
  toolCalls: ToolCall[]
  /** Tool results received. */
  toolResults: ToolResult[]
  /**
   * Every round of the bounded self-repair loop, in order. The trace renders these
   * beside the node timeline: "round 2 of 3 — the tool failed, replanning" is the
   * agent visibly correcting itself, which no other event in the protocol shows.
   */
  reflections: Reflection[]
  /**
   * The supervisor's routing decision for this run, or null on a run that never
   * routed. Carries `decided_by`, so the trace can always name whether the
   * classifier, the user, the tenant default or the platform cap chose the width.
   */
  routing: RoutingEvent | null
  /**
   * The turn's long-term memory recall, or null. **Null is the honest default and
   * means memory did not run**, which is the case for every run that carries no
   * `session_id` — so a surface must render its absence as "single-shot", never as
   * "recalled nothing".
   */
  memory: MemoryEvent | null
  /**
   * Every sub-agent lifecycle beat of a fan-out, in arrival order. Kept as the raw
   * ordered log rather than a per-agent map: the agent panel allocates one card when
   * a lane first appears and must never reflow, and a reducer that pre-grouped them
   * would decide that layout on the panel's behalf.
   */
  agentStatuses: AgentStatus[]
  /** The fan-out's merge — who contributed and who was omitted — or null. */
  synthesis: SynthesisEvent | null
  /** Accumulated answer text. */
  answer: string
  /** The active approval gate, or null once resolved/terminal. */
  approval: ApprovalRequired | null
  /**
   * The latest durable-inbox enqueue (async approval path), or null. Coexists
   * with {@link approval}: the live gate resolves on-socket, this one is a
   * persisted row an admin resolves from the approvals inbox.
   */
  approvalQueued: ApprovalQueued | null
  /** Whether this run ever paused at the human gate (persists after resolution). */
  awaitedApproval: boolean
  /**
   * Retrieval provenance (origins, fusion, cache status), or null. Drives the
   * provenance chip so a cache hit is never mistaken for a fresh answer.
   */
  provenance: Provenance | null
  /** The latest budget/rate-limit breach, or null. */
  budgetExceeded: BudgetExceeded | null
  /** Cumulative graph nodes touched by retrieval (deduped by id). */
  touchedNodes: GraphNode[]
  /** Cumulative graph edges touched by retrieval (deduped). */
  touchedEdges: GraphEdge[]
  /** Node ids touched by the most recent retrieval event (for pulsing). */
  activeNodeIds: string[]
  /** Reranked sources with scores, from the latest scored retrieval step. */
  retrievalScores: ScoredSource[]
  /** Total retrieval candidates considered. */
  candidates: number
  /** Terminal usage, once the run finishes. */
  usage: RunUsage | null
  /** Terminal status label from `run_finished`. */
  finishedStatus: string | null
  /** Error message, if the run errored. */
  error: string | null
  /**
   * Subsystem hue of the most recent event, keyed by `seq`. Updated on EVERY
   * event so the Phase-2 "active-beat" choreography can pulse the live channel.
   */
  lastSignal: LastSignal | null
}

/** The empty run state. */
export const initialRunState: RunState = {
  phase: 'idle',
  running: false,
  runId: null,
  traceId: null,
  events: [],
  steps: [],
  nodeLedger: [],
  reasoning: '',
  guardrails: [],
  toolCalls: [],
  toolResults: [],
  reflections: [],
  routing: null,
  memory: null,
  agentStatuses: [],
  synthesis: null,
  answer: '',
  approval: null,
  approvalQueued: null,
  awaitedApproval: false,
  provenance: null,
  budgetExceeded: null,
  touchedNodes: [],
  touchedEdges: [],
  activeNodeIds: [],
  retrievalScores: [],
  candidates: 0,
  usage: null,
  finishedStatus: null,
  error: null,
  lastSignal: null,
}

const edgeKey = (e: GraphEdge): string => `${e.source}->${e.target}:${e.relation}`

/**
 * Subsystem hue for a run event. Defers to the design system's
 * {@link signalForEvent}, overriding only the additive glass-box events it does
 * not yet know about so they still read as agent-reasoning activity.
 */
function signalForRunEvent(type: StreamEventType): Signal {
  if (type === 'node_finished' || type === 'reasoning') return 'agent'
  // The phase-5 additive events: the self-repair loop, the hand-off and the fan-out
  // are all agent reasoning, and memory recall is a retrieval of durable context.
  if (
    type === 'reflection' ||
    type === 'routing' ||
    type === 'agent_status' ||
    type === 'synthesis'
  ) {
    return 'agent'
  }
  if (type === 'memory') return 'graph'
  return signalForEvent(type)
}

/** Reduce one event into the run state (pure). */
export function runReducer(state: RunState, event: StreamEvent): RunState {
  const next: RunState = {
    ...state,
    events: [...state.events, event],
    // Every event advances the active-beat, keyed on its own seq.
    lastSignal: { signal: signalForRunEvent(event.type), seq: event.seq },
  }

  switch (event.type) {
    case 'run_started':
      return {
        ...next,
        phase: 'streaming',
        running: true,
        runId: event.run_id,
        traceId: event.trace_id,
        awaitedApproval: false,
        approvalQueued: null,
        provenance: null,
        budgetExceeded: null,
        reflections: [],
        routing: null,
        memory: null,
        agentStatuses: [],
        synthesis: null,
      }

    case 'node_started':
      return { ...next, phase: 'streaming', steps: [...state.steps, event] }

    case 'node_finished':
      return { ...next, phase: 'streaming', nodeLedger: [...state.nodeLedger, event] }

    case 'reasoning':
      return { ...next, phase: 'streaming', reasoning: state.reasoning + event.text }

    case 'guardrail':
      return {
        ...next,
        guardrails: [...state.guardrails, event],
        phase: event.verdict === 'block' ? 'blocked' : next.phase,
      }

    case 'retrieval': {
      const nodeIds = new Set(state.touchedNodes.map((n) => n.id))
      const mergedNodes = [...state.touchedNodes]
      for (const n of event.touched_nodes) {
        if (!nodeIds.has(n.id)) {
          nodeIds.add(n.id)
          mergedNodes.push(n)
        }
      }
      const edgeKeys = new Set(state.touchedEdges.map(edgeKey))
      const mergedEdges = [...state.touchedEdges]
      for (const e of event.touched_edges) {
        if (!edgeKeys.has(edgeKey(e))) {
          edgeKeys.add(edgeKey(e))
          mergedEdges.push(e)
        }
      }
      return {
        ...next,
        touchedNodes: mergedNodes,
        touchedEdges: mergedEdges,
        activeNodeIds: event.touched_nodes.map((n) => n.id),
        // Latest scored ranking wins (the 'reranked' step carries scores);
        // steps without scores (started/done) leave the prior ranking intact.
        retrievalScores:
          event.scored_sources.length > 0 ? event.scored_sources : state.retrievalScores,
        candidates: Math.max(state.candidates, event.num_candidates),
      }
    }

    case 'tool_call':
      return { ...next, toolCalls: [...state.toolCalls, event] }

    case 'tool_result':
      return { ...next, toolResults: [...state.toolResults, event] }

    case 'reflection':
      return {
        ...next,
        phase: 'streaming',
        reflections: [...state.reflections, event],
      }

    case 'routing':
      return { ...next, phase: 'streaming', routing: event }

    case 'memory':
      return { ...next, memory: event }

    case 'agent_status':
      return {
        ...next,
        phase: 'streaming',
        agentStatuses: [...state.agentStatuses, event],
      }

    case 'synthesis':
      return { ...next, synthesis: event }

    case 'approval_required':
      return { ...next, phase: 'awaiting_approval', approval: event, awaitedApproval: true }

    case 'approval_queued':
      return {
        ...next,
        phase: 'awaiting_approval',
        approvalQueued: event,
        awaitedApproval: true,
      }

    case 'provenance':
      return { ...next, provenance: event }

    case 'budget_exceeded':
      return { ...next, phase: 'blocked', budgetExceeded: event }

    case 'token':
      return { ...next, phase: 'streaming', answer: state.answer + event.text, approval: null }

    case 'run_finished':
      return {
        ...next,
        // Not `completed ? completed : blocked`. That ternary rendered a run a human
        // *refused* as a guardrail block — folding a person's decision into the
        // machine-refusal count, which is exactly the conflation `rejected` exists to
        // prevent. Any status this build does not know still falls to `blocked`,
        // because an unrecognised terminal is safer read as a refusal than as success.
        phase:
          event.status === 'completed'
            ? 'completed'
            : event.status === 'rejected'
              ? 'rejected'
              : event.status === 'error'
                ? 'error'
                : 'blocked',
        running: false,
        approval: null,
        finishedStatus: event.status,
        usage: {
          prompt_tokens: event.prompt_tokens,
          completion_tokens: event.completion_tokens,
          cost_usd: event.cost_usd,
          cache_hit: event.cache_hit,
        },
      }

    case 'error':
      return { ...next, phase: 'error', running: false, error: event.message, approval: null }

    default: {
      // Compile-time exhaustiveness. `event` narrows to `never` here only while every
      // member of the union above has a `case`; add a variant to `StreamEvent` without a
      // branch and this assignment is a type error rather than a silent discard. That
      // silent discard is exactly what dropped `reflection`, `routing` and `memory` on
      // the floor while they were live on the wire.
      //
      // The runtime fall-through stays, because a backend that ships a variant before
      // this client does must degrade to "logged but not reduced", never to a crash
      // mid-stream. `web/tests/state/runReducerCoverage.test.mjs` is the half of this
      // guard that runs under `npm test`.
      const unhandled: never = event
      void unhandled
      return next
    }
  }
}
