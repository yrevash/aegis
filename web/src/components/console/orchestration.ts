/**
 * The orchestration flow-map — topology from the backend, resolution in pure code.
 *
 * This module used to hardcode its own nine-node DAG of the agent, and it drifted:
 * the real graph has fifteen nodes, and it draws the human-approval branch out of
 * the `gate` node on **tool risk** — never out of the ML step, which
 * `aegis.agent.graph` states explicitly never gates. A published picture that
 * contradicts the implementation is worse than no picture, so the node/edge list is
 * no longer written here at all: it comes from `GET /agent/topology`, which
 * `aegis.agent.graph_topology()` reads off the *compiled* LangGraph.
 *
 * What stays here is everything the backend has no opinion about:
 *
 * - {@link NODE_PRESENTATION} — the subsystem hue + compact chip label per node.
 * - {@link layoutFlow} — a deterministic layered DAG layout, so the map keeps
 *   working when the graph grows a node instead of needing new coordinates.
 * - {@link resolveFlow} — the pure live resolution: feed a {@link RunState} and a
 *   {@link FlowMap} and assert which node is live and which branch the run took.
 *
 * {@link OrchestrationMap} is a thin renderer over these.
 */

import graphTopologySnapshot from '@/config/graphTopology.json'
import type { Signal } from '@/config/signals'
import type { AgentTopologyResponse } from '@/lib/api/types'
import type { RunState } from '@/state/runReducer'

/**
 * A stage in the agent graph, identified by its backend node id.
 *
 * Deliberately a plain `string`: the set of stages is served, not enumerated here,
 * so a new graph node shows up in the map without a frontend change.
 */
export type FlowNodeId = string

/**
 * The generated snapshot of the real compiled graph.
 *
 * `src/config/graphTopology.json` is produced from `aegis.agent.graph_topology()`,
 * and `backend/tests/api/test_agent_topology.py` fails if it stops matching the
 * live graph. It is what the map draws from in the moment before `/agent/topology`
 * lands, so the picture is the real topology throughout rather than a second
 * hand-maintained copy.
 */
export const FALLBACK_TOPOLOGY = graphTopologySnapshot as AgentTopologyResponse

/** How a node is presented: its subsystem hue and the compact chip label. */
interface NodePresentation {
  short: string
  signal: Signal
}

/**
 * Per-node presentation. Purely a frontend concern (the backend serves ids and
 * human labels, not colours), and intentionally *partial*: a node with no entry
 * here still renders, falling back to its served label and the neutral hue.
 */
const NODE_PRESENTATION: Record<string, NodePresentation> = {
  guard_input: { short: 'Guard in', signal: 'block' },
  route: { short: 'Route', signal: 'agent' },
  answer_memory: { short: 'Memory QA', signal: 'graph' },
  // The fan-out. These three are the multi-agent story: the supervisor sizing a
  // team, the lanes running concurrently, and the merge that names who was in the
  // answer and who was omitted.
  plan_team: { short: 'Plan team', signal: 'agent' },
  run_team: { short: 'Fan-out', signal: 'agent' },
  synthesize: { short: 'Synthesise', signal: 'graph' },
  recall_memory: { short: 'Recall', signal: 'graph' },
  retrieve: { short: 'Retrieve', signal: 'graph' },
  plan: { short: 'Plan', signal: 'agent' },
  gate: { short: 'Risk gate', signal: 'risk' },
  approval: { short: 'Approve', signal: 'risk' },
  act: { short: 'Act', signal: 'agent' },
  reflect: { short: 'Reflect', signal: 'agent' },
  generate: { short: 'Generate', signal: 'agent' },
  guard_output: { short: 'Guard out', signal: 'block' },
  stream: { short: 'Stream', signal: 'agent' },
  persist_memory: { short: 'Persist', signal: 'graph' },
}

/**
 * Nodes the graph wires *plain* — they emit no `node_started`/`node_finished` at
 * all, by design, so that a run with memory inactive produces a byte-identical
 * event stream. They are real stages and belong on the map, but they can never be
 * lit from the event stream, so the map draws them as unreported rather than
 * pretending they were skipped.
 *
 * `approval` is deliberately absent: it is also wired plain, but the run *phase*
 * (`awaiting_approval` / {@link RunState.awaitedApproval}) reports it exactly.
 */
const SILENT_NODES: ReadonlySet<string> = new Set(['recall_memory', 'persist_memory'])

/**
 * Legacy/alias node names accepted on the wire, mapped to their real node id.
 * Kept so an older backend still lights the right stage.
 */
const NODE_ALIASES: Record<string, string> = {
  stream_answer: 'stream',
}

/** A node in the flow-map: served identity plus frontend presentation. */
export interface FlowNode {
  id: FlowNodeId
  /** Full label, as served — the same string the node's events carry. */
  label: string
  /** Compact label for the node chip. */
  short: string
  /** Subsystem hue that owns this stage (shared with trace/graph/trust). */
  signal: Signal
  /** True when the node reports no events of its own (see {@link SILENT_NODES}). */
  silent: boolean
}

/** A directed edge in the flow-map, as served. */
export interface FlowEdge {
  source: FlowNodeId
  target: FlowNodeId
  /** True when the edge is a branch of a conditional router, not a fixed edge. */
  conditional: boolean
}

/** A laid-out point in the map's SVG viewBox. */
export interface FlowPoint {
  x: number
  y: number
  /** Layer index (0 = entrypoint), used to bow multi-layer edges. */
  layer: number
  /** True when the node's text label should sit above the mark, not below. */
  labelAbove: boolean
}

/** The map's SVG viewBox, sized to the number of layers the graph needs. */
export interface FlowViewBox {
  width: number
  height: number
}

/** Everything the renderer needs: the served topology, laid out. */
export interface FlowMap {
  nodes: readonly FlowNode[]
  edges: readonly FlowEdge[]
  /** Node ids, for wire-name resolution. */
  ids: ReadonlySet<FlowNodeId>
  position: Readonly<Record<FlowNodeId, FlowPoint>>
  viewBox: FlowViewBox
  /** Edge keys (`source->target`) that run backwards, i.e. the self-repair loop. */
  backEdges: ReadonlySet<string>
}

/** Stable key for an edge. */
export function edgeKey(edge: FlowEdge): string {
  return `${edge.source}->${edge.target}`
}

/**
 * Build the renderable flow-map from a served topology.
 *
 * Pure and total: an empty or partial topology yields an empty map rather than
 * throwing, so a malformed response degrades to "no map" instead of a blank
 * console.
 */
export function buildFlowMap(topology: AgentTopologyResponse): FlowMap {
  const nodes: FlowNode[] = (topology.nodes ?? []).map((n) => {
    const presentation = NODE_PRESENTATION[n.id]
    return {
      id: n.id,
      label: n.label,
      short: presentation?.short ?? n.label,
      signal: presentation?.signal ?? 'neutral',
      silent: SILENT_NODES.has(n.id),
    }
  })
  const ids = new Set(nodes.map((n) => n.id))
  // Defensive: never lay out an edge whose endpoints are not both drawn.
  const edges: FlowEdge[] = (topology.edges ?? []).filter(
    (e) => ids.has(e.source) && ids.has(e.target),
  )
  return { nodes, edges, ids, ...layoutFlow(nodes, edges) }
}

// ── Layout ───────────────────────────────────────────────────────────────────

/** Horizontal padding inside the viewBox, so end marks are not clipped. */
const PAD_X = 48
/** Vertical distance between rows when a layer holds more than one node. */
const ROW_GAP = 48
/** Horizontal distance between adjacent layers. */
const LAYER_GAP = 88

/**
 * Lay the graph out as layers: x by longest-path depth from the entrypoint, y by
 * position within the layer (the deepest-reaching node keeps the spine, the rest
 * fan out above and below it). Deterministic — same topology in, same picture out.
 *
 * Cycles (the `reflect → plan` self-repair loop) are handled by classifying the
 * back edges out with a DFS first and layering the remaining DAG, so a loop can
 * never push a node to an absurd depth.
 */
export function layoutFlow(
  nodes: readonly FlowNode[],
  edges: readonly FlowEdge[],
): { position: Record<FlowNodeId, FlowPoint>; viewBox: FlowViewBox; backEdges: Set<string> } {
  const backEdges = findBackEdges(nodes, edges)
  const forward = edges.filter((e) => !backEdges.has(edgeKey(e)))
  const layer = longestPathLayers(nodes, forward)
  const reach = forwardReach(nodes, forward)

  const byLayer = new Map<number, FlowNode[]>()
  for (const node of nodes) {
    const l = layer.get(node.id) ?? 0
    const bucket = byLayer.get(l)
    if (bucket) bucket.push(node)
    else byLayer.set(l, [node])
  }

  const maxLayer = Math.max(0, ...layer.values())
  const maxRows = Math.max(1, ...[...byLayer.values()].map((b) => b.length))
  const width = PAD_X * 2 + maxLayer * LAYER_GAP
  // Room for the widest layer plus a label line above and below the outermost row.
  const height = ROW_GAP * (maxRows + 1) + 60
  const spineY = height / 2

  const position: Record<FlowNodeId, FlowPoint> = {}
  for (const [l, bucket] of byLayer) {
    // The node that reaches furthest keeps the spine; the rest alternate above and
    // below it, so a short-circuit branch reads as a detour off the main line.
    const ordered = [...bucket].sort(
      (a, b) => (reach.get(b.id) ?? 0) - (reach.get(a.id) ?? 0) || nodes.indexOf(a) - nodes.indexOf(b),
    )
    ordered.forEach((node, i) => {
      // 0, -1, +1, -2, +2, … in ROW_GAP steps.
      const rank = Math.ceil(i / 2) * (i % 2 === 1 ? -1 : 1)
      const y = spineY + rank * ROW_GAP
      position[node.id] = {
        x: PAD_X + l * LAYER_GAP,
        y,
        layer: l,
        // Off-spine nodes label outwards; spine nodes zigzag so neighbouring
        // labels never collide at this density.
        labelAbove: rank !== 0 ? rank < 0 : l % 2 === 1,
      }
    })
  }
  return { position, viewBox: { width, height }, backEdges }
}

/** Classify the edges that close a cycle, via DFS from the entrypoints. */
function findBackEdges(nodes: readonly FlowNode[], edges: readonly FlowEdge[]): Set<string> {
  const adjacency = new Map<FlowNodeId, FlowEdge[]>()
  for (const edge of edges) {
    const bucket = adjacency.get(edge.source)
    if (bucket) bucket.push(edge)
    else adjacency.set(edge.source, [edge])
  }
  const back = new Set<string>()
  const onStack = new Set<FlowNodeId>()
  const done = new Set<FlowNodeId>()

  const visit = (id: FlowNodeId): void => {
    onStack.add(id)
    for (const edge of adjacency.get(id) ?? []) {
      if (onStack.has(edge.target)) back.add(edgeKey(edge))
      else if (!done.has(edge.target)) visit(edge.target)
    }
    onStack.delete(id)
    done.add(id)
  }
  for (const node of nodes) if (!done.has(node.id)) visit(node.id)
  return back
}

/** Longest-path layer index per node over an acyclic edge set (entrypoints at 0). */
function longestPathLayers(
  nodes: readonly FlowNode[],
  forward: readonly FlowEdge[],
): Map<FlowNodeId, number> {
  const indegree = new Map<FlowNodeId, number>(nodes.map((n) => [n.id, 0]))
  const outgoing = new Map<FlowNodeId, FlowEdge[]>()
  for (const edge of forward) {
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1)
    const bucket = outgoing.get(edge.source)
    if (bucket) bucket.push(edge)
    else outgoing.set(edge.source, [edge])
  }
  const layer = new Map<FlowNodeId, number>(nodes.map((n) => [n.id, 0]))
  const queue = nodes.filter((n) => (indegree.get(n.id) ?? 0) === 0).map((n) => n.id)
  for (let head = 0; head < queue.length; head += 1) {
    const id = queue[head]
    for (const edge of outgoing.get(id) ?? []) {
      layer.set(edge.target, Math.max(layer.get(edge.target) ?? 0, (layer.get(id) ?? 0) + 1))
      const remaining = (indegree.get(edge.target) ?? 0) - 1
      indegree.set(edge.target, remaining)
      if (remaining === 0) queue.push(edge.target)
    }
  }
  return layer
}

/** Longest remaining forward path from each node (used to pick the spine node). */
function forwardReach(
  nodes: readonly FlowNode[],
  forward: readonly FlowEdge[],
): Map<FlowNodeId, number> {
  const outgoing = new Map<FlowNodeId, FlowEdge[]>()
  for (const edge of forward) {
    const bucket = outgoing.get(edge.source)
    if (bucket) bucket.push(edge)
    else outgoing.set(edge.source, [edge])
  }
  const memo = new Map<FlowNodeId, number>()
  const depth = (id: FlowNodeId): number => {
    const seen = memo.get(id)
    if (seen !== undefined) return seen
    memo.set(id, 0) // guards against any residual cycle
    let best = 0
    for (const edge of outgoing.get(id) ?? []) best = Math.max(best, depth(edge.target) + 1)
    memo.set(id, best)
    return best
  }
  for (const node of nodes) depth(node.id)
  return memo
}

// ── Live resolution ──────────────────────────────────────────────────────────

/**
 * Map a backend `node_started` / `node_finished` name to a flow node id.
 *
 * Now that the map *is* the graph, the mapping is identity for every real node;
 * only historical aliases need translating. An unknown name resolves to `null`
 * and is ignored rather than silently dropped into the wrong stage.
 */
export function flowIdForNode(node: string, ids: ReadonlySet<FlowNodeId>): FlowNodeId | null {
  if (ids.has(node)) return node
  const alias = NODE_ALIASES[node]
  return alias !== undefined && ids.has(alias) ? alias : null
}

/** Lifecycle of one flow node for the current run. */
export type FlowStatus = 'idle' | 'active' | 'done'

/** Which conditional branch a run took around the human gate. */
export type FlowBranch = 'gated' | 'direct'

/** The resolved live state of the whole flow-map. */
export interface FlowResolution {
  /** Per-node lifecycle status. */
  status: Record<FlowNodeId, FlowStatus>
  /** How many times each node started (>1 means the self-repair loop ran). */
  visits: Record<FlowNodeId, number>
  /** Keys of the edges the run actually traversed (see {@link isEdgeActive}). */
  traversed: ReadonlySet<string>
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
  visits: Map<FlowNodeId, number>
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
    // The approval node is wired plain (it re-executes on resume, so it emits no
    // node events); the run phase reports it exactly.
    case 'approval':
      if (ctx.awaiting) return 'active'
      return ctx.awaited ? 'done' : 'idle'
    // The output rail also emits a `guardrail` event; accept either signal so the
    // rail still reads as done on a stream that reported only the verdict.
    case 'guard_output':
      if (ctx.done.has(id) || ctx.outputGuarded) return 'done'
      return ctx.visits.has(id) ? 'active' : 'idle'
    // Streaming is the token phase: active while answer text is arriving, done
    // once the run terminates with an answer.
    case 'stream':
      if (ctx.done.has(id) || (ctx.finished && ctx.answering)) return 'done'
      if (ctx.visits.has(id)) return 'active'
      return ctx.streaming && ctx.answering ? 'active' : 'idle'
    default:
      if (ctx.done.has(id)) return 'done'
      return ctx.visits.has(id) ? 'active' : 'idle'
  }
}

/**
 * Resolve the live flow-map from the reduced run state (pure). Highlights the
 * frontier node and reports which conditional branch executed so the map can
 * light the gated (via human approval) or direct (autonomous) path.
 */
export function resolveFlow(state: RunState, map: FlowMap): FlowResolution {
  const visits = new Map<FlowNodeId, number>()
  for (const step of state.steps) {
    const id = flowIdForNode(step.node, map.ids)
    if (id !== null) visits.set(id, (visits.get(id) ?? 0) + 1)
  }
  const done = new Set<FlowNodeId>()
  for (const fin of state.nodeLedger) {
    const id = flowIdForNode(fin.node, map.ids)
    if (id !== null) done.add(id)
  }

  const ctx: FlowContext = {
    visits,
    done,
    awaiting: state.phase === 'awaiting_approval',
    awaited: state.awaitedApproval,
    outputGuarded: state.guardrails.some((g) => g.stage === 'output'),
    answering: state.answer.length > 0,
    finished: state.finishedStatus !== null,
    streaming: state.phase === 'streaming',
  }

  const status: Record<FlowNodeId, FlowStatus> = {}
  const visitCounts: Record<FlowNodeId, number> = {}
  const silent: FlowNode[] = []
  for (const node of map.nodes) {
    visitCounts[node.id] = visits.get(node.id) ?? 0
    if (node.silent) silent.push(node)
    else status[node.id] = statusFor(node.id, ctx)
  }
  // Second pass: a silent node reports nothing of its own, so it is inferred from
  // the neighbours that DO report. Successors first — a silent node whose
  // successor ran was obviously traversed, and one whose successor never ran was
  // obviously bypassed (the memory-specialist branch skips `recall_memory`
  // entirely). A silent *tail* node has no successor to read, so it falls back to
  // its predecessors.
  for (const node of silent) {
    status[node.id] = silentStatus(node.id, map, status, ctx.finished)
  }

  // The frontier is the furthest node still executing — "furthest" by graph depth,
  // not by the order the backend happened to declare its nodes in.
  let activeId: FlowNodeId | null = null
  for (const node of map.nodes) {
    if (status[node.id] !== 'active') continue
    const best = activeId === null ? -1 : (map.position[activeId]?.layer ?? -1)
    if ((map.position[node.id]?.layer ?? 0) >= best) activeId = node.id
  }

  const branch: FlowBranch | null = state.awaitedApproval
    ? 'gated'
    : visits.has('act') || ctx.finished
      ? 'direct'
      : null

  // A denied tool result on the direct path means the action was blocked by
  // policy (e.g. not in the persona's allowlist) — not an autonomous success.
  const denied = branch === 'direct' && state.toolResults.some((r) => !r.ok)

  return {
    status,
    visits: visitCounts,
    traversed: traversedEdges(map, status, visitCounts),
    activeId,
    branch,
    denied,
  }
}

/** Infer a silent node's status from the neighbours that do report events. */
function silentStatus(
  id: FlowNodeId,
  map: FlowMap,
  status: Record<FlowNodeId, FlowStatus>,
  finished: boolean,
): FlowStatus {
  const forward = map.edges.filter((e) => !map.backEdges.has(edgeKey(e)))
  const successors = forward.filter((e) => e.source === id)
  if (successors.length > 0) {
    return successors.some((e) => status[e.target] !== 'idle') ? 'done' : 'idle'
  }
  const predecessors = forward.filter((e) => e.target === id)
  if (!predecessors.some((e) => status[e.source] === 'done')) return 'idle'
  return finished ? 'done' : 'active'
}

/**
 * Decide which edges the run actually traversed.
 *
 * The base signal is "source finished and target was reached", which already
 * ghosts the untaken arm of most routers for free. Two cases need more than that:
 *
 * - A **back edge** (the `reflect → plan` self-repair loop) is only real if its
 *   target ran a second time; otherwise every completed run would look like it
 *   looped.
 * - A router whose arms **reconverge** — `approval` goes to `act` when approved
 *   and straight to `generate` when rejected, but an approved run reaches
 *   `generate` too, via `act`. Where several arms were reached, only the nearest
 *   one (lowest layer) was the branch actually taken; the rest were reached the
 *   long way round.
 */
function traversedEdges(
  map: FlowMap,
  status: Record<FlowNodeId, FlowStatus>,
  visits: Record<FlowNodeId, number>,
): Set<string> {
  const reached = (edge: FlowEdge): boolean =>
    status[edge.source] === 'done' &&
    (status[edge.target] === 'active' || status[edge.target] === 'done')

  const traversed = new Set<string>()
  const arms = new Map<FlowNodeId, FlowEdge[]>()
  for (const edge of map.edges) {
    if (!reached(edge)) continue
    if (map.backEdges.has(edgeKey(edge))) {
      if ((visits[edge.target] ?? 0) > 1) traversed.add(edgeKey(edge))
      continue
    }
    if (!edge.conditional) {
      traversed.add(edgeKey(edge))
      continue
    }
    const bucket = arms.get(edge.source)
    if (bucket) bucket.push(edge)
    else arms.set(edge.source, [edge])
  }
  for (const bucket of arms.values()) {
    const nearest = Math.min(...bucket.map((e) => map.position[e.target]?.layer ?? 0))
    for (const edge of bucket) {
      if ((map.position[edge.target]?.layer ?? 0) === nearest) traversed.add(edgeKey(edge))
    }
  }
  return traversed
}

/** Whether an edge is lit, i.e. the run actually traversed it. */
export function isEdgeActive(edge: FlowEdge, resolution: FlowResolution): boolean {
  return resolution.traversed.has(edgeKey(edge))
}

/**
 * Whether a conditional edge is the road *not* taken, so the map can ghost it.
 * Only meaningful once the router has run — before that every arm is unknown.
 */
export function isEdgeNotTaken(edge: FlowEdge, resolution: FlowResolution): boolean {
  if (!edge.conditional) return false
  if (resolution.status[edge.source] !== 'done') return false
  return !resolution.traversed.has(edgeKey(edge))
}
