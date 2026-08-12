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
  Abstained,
  ApprovalQueued,
  ApprovalRequired,
  AutonomyBandKind,
  BudgetExceeded,
  Guardrail,
  GraphEdge,
  GraphNode,
  MLExplanation,
  NodeFinished,
  NodeStarted,
  Provenance,
  ScoredSource,
  StreamEvent,
  StreamEventType,
  ToolCall,
  ToolResult,
} from '@/lib/stream'

/** Coarse lifecycle status derived from the event stream. */
export type RunPhase =
  | 'idle'
  | 'streaming'
  | 'awaiting_approval'
  | 'abstained'
  | 'completed'
  | 'blocked'
  | 'error'

/** Aggregated usage for the token/cost dashboard. */
export interface RunUsage {
  prompt_tokens: number
  completion_tokens: number
  cost_usd: number
  cache_hit: boolean
}

/**
 * Bounded-autonomy gate readout, projected (camelCase) from the latest
 * {@link MLExplanation}. `gated === true` ⇒ the model was too uncertain to act
 * alone and the run must route to the human gate.
 */
export interface MLGate {
  gated: boolean | null
  /**
   * The graded autonomy band the policy assigned (§2.3), or null. Authoritative
   * over {@link gated} when present — drives the {@link AutonomyBand} badge.
   */
  band: AutonomyBandKind | null
  gateReason: string | null
  minConfidence: number | null
  maxRelWidth: number | null
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
  /** Accumulated answer text. */
  answer: string
  /** The latest ML explanation, if any. */
  ml: MLExplanation | null
  /** The latest bounded-autonomy gate readout (camelCase), or null. */
  mlGate: MLGate | null
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
  /** The abstain terminal outcome (insufficient confidence), or null. */
  abstained: Abstained | null
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
  answer: '',
  ml: null,
  mlGate: null,
  approval: null,
  approvalQueued: null,
  awaitedApproval: false,
  provenance: null,
  abstained: null,
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
        abstained: null,
        budgetExceeded: null,
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

    case 'ml_explanation':
      return {
        ...next,
        ml: event,
        mlGate: {
          gated: event.gated,
          band: event.band,
          gateReason: event.gate_reason,
          minConfidence: event.min_confidence,
          maxRelWidth: event.max_rel_width,
        },
      }

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

    case 'abstained':
      return { ...next, phase: 'abstained', abstained: event, approval: null }

    case 'budget_exceeded':
      return { ...next, phase: 'blocked', budgetExceeded: event }

    case 'token':
      return { ...next, phase: 'streaming', answer: state.answer + event.text, approval: null }

    case 'run_finished':
      return {
        ...next,
        // An abstain outcome is terminal in its own right; keep that phase even
        // if a `run_finished` envelope follows to carry usage.
        phase: state.abstained
          ? 'abstained'
          : event.status === 'completed'
            ? 'completed'
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

    default:
      return next
  }
}
