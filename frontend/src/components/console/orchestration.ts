/**
 * The orchestration flow-map — pure topology + live resolution.
 *
 * The agent runs a fixed DAG:
 *
 *   guard_input → retrieve → plan → ml ─┬─(gated)→ approval → act ─┐
 *                                        └─(direct)───────────────→ act → generate → guard_output → stream
 *
 * This module is framework-free so the whole map is deterministic and
 * unit-testable: feed a {@link RunState} and assert which node is live and which
 * conditional branch (gated vs direct) the run actually took. The React map in
 * {@link OrchestrationMap} is a thin renderer over {@link resolveFlow}.
 */

import type { Signal } from '@/config/signals'
import type { RunState } from '@/state/runReducer'

/** A stage in the fixed agent DAG. */
export type FlowNodeId =
  | 'guard_input'
  | 'retrieve'
  | 'plan'
  | 'ml'
  | 'approval'
  | 'act'
  | 'generate'
  | 'guard_output'
  | 'stream'

/** A node in the flow-map, with the subsystem hue it lights in. */
export interface FlowNode {
  id: FlowNodeId
  /** Full label for tooltips / wide layouts. */
  label: string
  /** Compact label for the node chip. */
  short: string
  /** Subsystem hue that owns this stage (shared with trace/graph/trust). */
  signal: Signal
  /** True for nodes on the conditional (human-gate) branch. */
  conditional?: boolean
}

/** The fixed DAG, in topological order. */
export const FLOW_NODES: readonly FlowNode[] = [
  { id: 'guard_input', label: 'Input guardrail', short: 'Guard in', signal: 'block' },
  { id: 'retrieve', label: 'Retrieve context', short: 'Retrieve', signal: 'graph' },
  { id: 'plan', label: 'Plan approach', short: 'Plan', signal: 'agent' },
  { id: 'ml', label: 'ML score & gate', short: 'ML score', signal: 'ml' },
  { id: 'approval', label: 'Human approval', short: 'Approve', signal: 'risk', conditional: true },
  { id: 'act', label: 'Act (tool call)', short: 'Act', signal: 'agent' },
  { id: 'generate', label: 'Generate answer', short: 'Generate', signal: 'agent' },
  { id: 'guard_output', label: 'Output guardrail', short: 'Guard out', signal: 'block' },
  { id: 'stream', label: 'Stream answer', short: 'Stream', signal: 'agent' },
] as const

/** Which conditional branch a directed edge belongs to (undefined = always). */
export type FlowBranch = 'gated' | 'direct'

/** A directed edge in the flow-map. */
export interface FlowEdge {
  from: FlowNodeId
  to: FlowNodeId
  /** Present only on the two conditional edges out of `ml`. */
  branch?: FlowBranch
}

/** The DAG edges, including the gated / direct split around the human gate. */
export const FLOW_EDGES: readonly FlowEdge[] = [
  { from: 'guard_input', to: 'retrieve' },
  { from: 'retrieve', to: 'plan' },
  { from: 'plan', to: 'ml' },
  { from: 'ml', to: 'approval', branch: 'gated' },
  { from: 'ml', to: 'act', branch: 'direct' },
  { from: 'approval', to: 'act' },
  { from: 'act', to: 'generate' },
  { from: 'generate', to: 'guard_output' },
  { from: 'guard_output', to: 'stream' },
] as const

/**
 * Map a backend `node_started` / `node_finished` name to its flow node. The
 * backend emits the ML node as `ml`; everything else matches 1:1. The legacy
 * `score` alias is still accepted for safety. Names with no flow node (there
 * are none today) resolve to `null` and are ignored.
 */
function flowIdForNode(node: string): FlowNodeId | null {
  switch (node) {
    case 'guard_input':
      return 'guard_input'
    case 'retrieve':
      return 'retrieve'
    case 'plan':
      return 'plan'
    case 'ml':
    case 'score': // legacy alias
      return 'ml'
    case 'act':
      return 'act'
    case 'generate':
      return 'generate'
    default:
      return null
  }
}

/** Lifecycle of one flow node for the current run. */
export type FlowStatus = 'idle' | 'active' | 'done'

/** The resolved live state of the whole flow-map. */
export interface FlowResolution {
  /** Per-node lifecycle status. */
  status: Record<FlowNodeId, FlowStatus>
  /** The single frontier node currently executing, or null when idle. */
  activeId: FlowNodeId | null
  /** Which conditional branch the run took, or null while undetermined. */
  branch: FlowBranch | null
  /**
   * True when the run took the direct (autonomous) branch but its action was
   * denied by policy (a failed tool result). Such a run is NOT "autonomous" — it
   * was blocked at the tool boundary, so the readout must say denied, not
   * autonomous. (A human-gate rejection is `branch === 'gated'`, not this.)
   */
  denied: boolean
}

/** Inputs derived once from {@link RunState}, shared by every node. */
interface FlowContext {
  started: Set<FlowNodeId>
  done: Set<FlowNodeId>
  awaiting: boolean
  awaited: boolean
  outputGuarded: boolean
  answering: boolean
  finished: boolean
  streaming: boolean
}

/** Resolve a single node's status from the shared context. */
function statusFor(id: FlowNodeId, ctx: FlowContext): FlowStatus {
  switch (id) {
    // The human gate is not a graph node; it is derived from the run phase.
    case 'approval':
      if (ctx.awaiting) return 'active'
      return ctx.awaited ? 'done' : 'idle'
    // The output rail fires as a `guardrail` event, not a node — treat its
    // presence as completion (it is effectively instantaneous).
    case 'guard_output':
      return ctx.outputGuarded ? 'done' : 'idle'
    // Streaming is the token phase: active while answer text is arriving, done
    // once the run terminates with an answer.
    case 'stream':
      if (ctx.finished && ctx.answering) return 'done'
      return ctx.streaming && ctx.answering ? 'active' : 'idle'
    default:
      if (ctx.done.has(id)) return 'done'
      return ctx.started.has(id) ? 'active' : 'idle'
  }
}

/**
 * Resolve the live flow-map from the reduced run state (pure). Highlights the
 * frontier node and reports which conditional branch executed so the map can
 * light the gated (via human approval) or direct (autonomous) path.
 */
export function resolveFlow(state: RunState): FlowResolution {
  const started = new Set<FlowNodeId>()
  for (const step of state.steps) {
    const id = flowIdForNode(step.node)
    if (id !== null) started.add(id)
  }
  const done = new Set<FlowNodeId>()
  for (const fin of state.nodeLedger) {
    const id = flowIdForNode(fin.node)
    if (id !== null) done.add(id)
  }

  const ctx: FlowContext = {
    started,
    done,
    awaiting: state.phase === 'awaiting_approval',
    awaited: state.awaitedApproval,
    outputGuarded: state.guardrails.some((g) => g.stage === 'output'),
    answering: state.answer.length > 0,
    finished: state.finishedStatus !== null,
    streaming: state.phase === 'streaming',
  }

  const status = {} as Record<FlowNodeId, FlowStatus>
  let activeId: FlowNodeId | null = null
  for (const node of FLOW_NODES) {
    const s = statusFor(node.id, ctx)
    status[node.id] = s
    // The frontier is the furthest node still executing.
    if (s === 'active') activeId = node.id
  }

  const branch: FlowBranch | null = state.awaitedApproval
    ? 'gated'
    : started.has('act') || ctx.finished
      ? 'direct'
      : null

  // A denied tool result on the direct path means the action was blocked by
  // policy (e.g. not in the persona's allowlist) — not an autonomous success.
  const denied = branch === 'direct' && state.toolResults.some((r) => !r.ok)

  return { status, activeId, branch, denied }
}

/** Whether an edge is lit given the resolved branch (for edge highlighting). */
export function isEdgeActive(edge: FlowEdge, resolution: FlowResolution): boolean {
  if (edge.branch !== undefined && edge.branch !== resolution.branch) return false
  const from = resolution.status[edge.from]
  const to = resolution.status[edge.to]
  // An edge is "lit" once its source is done and its target has been reached.
  return from === 'done' && (to === 'active' || to === 'done')
}
