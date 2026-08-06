import { Route } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'
import type { NodeFinished } from '@/types/stream'

import type { Beat } from './motion'
import {
  FLOW_EDGES,
  FLOW_NODES,
  isEdgeActive,
  resolveFlow,
  type FlowEdge,
  type FlowNodeId,
  type FlowResolution,
} from './orchestration'

/** Fixed layout for the flow-map in an 0..960 × 0..140 SVG viewBox. */
const NODE_POS: Record<FlowNodeId, { x: number; y: number }> = {
  guard_input: { x: 50, y: 95 },
  retrieve: { x: 173, y: 95 },
  plan: { x: 296, y: 95 },
  ml: { x: 419, y: 95 },
  approval: { x: 480, y: 42 }, // the conditional detour, raised above the spine
  act: { x: 541, y: 95 },
  generate: { x: 664, y: 95 },
  guard_output: { x: 787, y: 95 },
  stream: { x: 910, y: 95 },
}

/** Neutral (idle) mark colours for the light canvas. */
const IDLE_FILL = '#F2F4F7'
const IDLE_STROKE = '#E4E7EC'
const IDLE_TEXT = '#98A2B3'
const DONE_TEXT = '#475467'

interface OrchestrationMapProps {
  state: RunState
  /** The current active-beat, so the live node pulses in lockstep. */
  beat: Beat | null
}

/**
 * The orchestration flow-map — the agent's trajectory rendered as a node-link
 * DAG. It lights the frontier node in its subsystem hue and draws the
 * conditional branch the run actually took (through the human gate, or the
 * autonomous path), sharing the active-beat pulse with the trace, graph and
 * trust bar. Fused above the knowledge graph as the "agent trajectory" layer.
 */
export function OrchestrationMap({ state, beat }: OrchestrationMapProps): ReactElement {
  const flow = useMemo(() => resolveFlow(state), [state])

  // Per-node glass-box ledger (model / tokens / ms / cost), keyed by flow node.
  const ledger = useMemo(() => ledgerByFlowId(state.nodeLedger), [state.nodeLedger])

  return (
    <div className="border-b border-border px-4 pt-3 pb-2">
      <div className="mb-1 flex items-center gap-2">
        <Route className="size-3.5 text-agent-ink" />
        <span className="eyebrow">Orchestration</span>
        <BranchTag branch={flow.branch} denied={flow.denied} />
      </div>

      <svg
        viewBox="0 0 960 140"
        className="h-[92px] w-full"
        role="img"
        aria-label="Agent orchestration flow map"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Edges first, so nodes sit on top. */}
        {FLOW_EDGES.map((edge) => (
          <FlowEdgeLine key={`${edge.from}-${edge.to}`} edge={edge} flow={flow} />
        ))}
        {/* Nodes. */}
        {FLOW_NODES.map((node) => (
          <FlowNodeMark
            key={node.id}
            id={node.id}
            flow={flow}
            beat={flow.activeId === node.id ? beat : null}
          />
        ))}
      </svg>

      {/* Glass-box cost/latency chips for the nodes that have reported. */}
      <div className="mt-1 flex flex-wrap gap-1">
        {FLOW_NODES.filter((n) => ledger.has(n.id)).map((n) => {
          const fin = ledger.get(n.id)!
          return <LedgerChip key={n.id} label={n.short} fin={fin} />
        })}
      </div>
    </div>
  )
}

/** A single flow node: circle mark + short label, coloured by lifecycle. */
function FlowNodeMark({
  id,
  flow,
  beat,
}: {
  id: FlowNodeId
  flow: FlowResolution
  beat: Beat | null
}): ReactElement {
  const node = FLOW_NODES.find((n) => n.id === id)!
  const pos = NODE_POS[id]
  const status = flow.status[id]
  const hue = SIGNALS[node.signal].hex

  const filled = status === 'active' || status === 'done'
  const fill = filled ? hue : IDLE_FILL
  const stroke = filled ? hue : IDLE_STROKE
  const labelY = id === 'approval' ? pos.y - 16 : pos.y + 22

  return (
    <g>
      {/* Active halo, re-keyed on the beat seq so it re-fires each event. */}
      {status === 'active' && beat !== null && (
        <circle
          key={beat.seq}
          cx={pos.x}
          cy={pos.y}
          r={11}
          fill="none"
          stroke={hue}
          strokeWidth={2}
          className="animate-flow-pulse"
        />
      )}
      <circle
        cx={pos.x}
        cy={pos.y}
        r={status === 'active' ? 10 : 8}
        fill={fill}
        stroke={stroke}
        strokeWidth={1.75}
        opacity={status === 'idle' ? 0.9 : 1}
      />
      <text
        x={pos.x}
        y={labelY}
        textAnchor="middle"
        fontSize={12}
        fontWeight={status === 'active' ? 600 : 500}
        fill={filled ? DONE_TEXT : IDLE_TEXT}
        className="font-sans"
      >
        {node.short}
      </text>
    </g>
  )
}

/** A directed edge; lit when traversed, faint-dashed when it is the road not taken. */
function FlowEdgeLine({ edge, flow }: { edge: FlowEdge; flow: FlowResolution }): ReactElement {
  const a = NODE_POS[edge.from]
  const b = NODE_POS[edge.to]
  const active = isEdgeActive(edge, flow)
  // A conditional edge whose branch was not taken reads as a faint dashed ghost.
  const notTaken = edge.branch !== undefined && flow.branch !== null && edge.branch !== flow.branch
  const hue = SIGNALS[FLOW_NODES.find((n) => n.id === edge.from)!.signal].hex

  return (
    <line
      x1={a.x}
      y1={a.y}
      x2={b.x}
      y2={b.y}
      stroke={active ? hue : IDLE_STROKE}
      strokeWidth={active ? 2.5 : 1.5}
      strokeDasharray={notTaken ? '3 4' : undefined}
      opacity={notTaken ? 0.5 : active ? 1 : 0.8}
      strokeLinecap="round"
    />
  )
}

/** Branch readout pill: via human gate, action denied (policy block), or autonomous. */
function BranchTag({
  branch,
  denied,
}: {
  branch: FlowResolution['branch']
  denied: boolean
}): ReactElement | null {
  if (branch === null) return null
  // A policy-denied action on the direct path is a block, not an autonomous run.
  if (denied) {
    return (
      <span className="ml-auto rounded-md border border-block/60 bg-block/15 px-1.5 py-0.5 font-mono text-[0.6rem] tracking-wide text-block-ink">
        action denied
      </span>
    )
  }
  const gated = branch === 'gated'
  return (
    <span
      className={cn(
        'ml-auto rounded-md border px-1.5 py-0.5 font-mono text-[0.6rem] tracking-wide',
        gated ? 'border-risk/60 bg-risk/15 text-risk-ink' : 'border-ok/60 bg-ok/15 text-ok-ink',
      )}
    >
      {gated ? 'via human gate' : 'autonomous path'}
    </span>
  )
}

/** A compact per-node cost/latency chip from the glass-box ledger. */
function LedgerChip({ label, fin }: { label: string; fin: NodeFinished }): ReactElement {
  return (
    <span className="tabular inline-flex items-center gap-1 rounded-md border border-border bg-surface-2/70 px-1.5 py-0.5 font-mono text-[0.6rem] text-muted-foreground">
      <span className="font-medium text-foreground">{label}</span>
      <span aria-hidden>·</span>
      {fin.duration_ms}ms
      {fin.cost_usd > 0 && (
        <>
          <span aria-hidden>·</span>${fin.cost_usd.toFixed(4)}
        </>
      )}
    </span>
  )
}

/** Index the latest `node_finished` per flow node (last write wins). */
function ledgerByFlowId(nodeLedger: NodeFinished[]): Map<FlowNodeId, NodeFinished> {
  const map = new Map<FlowNodeId, NodeFinished>()
  for (const fin of nodeLedger) {
    const id = flowIdForLedger(fin.node)
    if (id !== null) map.set(id, fin)
  }
  return map
}

/** Mirror of the reducer's node→flow mapping for ledger chips (backend node is `ml`). */
function flowIdForLedger(node: string): FlowNodeId | null {
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
