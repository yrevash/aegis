'use client'

import { Route } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { SIGNALS } from '@/config/signals'
import { getAgentTopology } from '@/lib/api/client'
import type { AgentTopologyResponse } from '@/lib/api/types'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'
import type { NodeFinished } from '@/lib/stream'

import type { Beat } from './motion'
import {
  FALLBACK_TOPOLOGY,
  buildFlowMap,
  edgeKey,
  flowIdForNode,
  isEdgeActive,
  isEdgeNotTaken,
  resolveFlow,
  type FlowEdge,
  type FlowMap,
  type FlowNodeId,
  type FlowResolution,
} from './orchestration'

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
 *
 * The topology is **served**, not assumed: it comes from `GET /agent/topology`,
 * which reads it off the compiled LangGraph. Until that lands — and for as long
 * as it fails, which includes mock mode with no backend — the map renders the
 * generated snapshot of the same graph, so it is never blank and never wrong.
 */
export function OrchestrationMap({ state, beat }: OrchestrationMapProps): ReactElement {
  const topology = useAgentTopology()
  const map = useMemo(() => buildFlowMap(topology), [topology])
  const flow = useMemo(() => resolveFlow(state, map), [state, map])

  // Per-node glass-box ledger (model / tokens / ms / cost), keyed by flow node.
  const ledger = useMemo(() => ledgerByFlowId(state.nodeLedger, map), [state.nodeLedger, map])

  return (
    <div className="border-b border-border px-4 pt-3 pb-2">
      <div className="mb-1 flex items-center gap-2">
        <Route className="size-3.5 text-agent-ink" />
        <span className="eyebrow">Orchestration</span>
        <BranchTag branch={flow.branch} denied={flow.denied} />
      </div>

      <svg
        viewBox={`0 0 ${map.viewBox.width} ${map.viewBox.height}`}
        className="h-[112px] w-full"
        role="img"
        aria-label="Agent orchestration flow map"
        preserveAspectRatio="xMidYMid meet"
      >
        {/* Edges first, so nodes sit on top. */}
        {map.edges.map((edge) => (
          <FlowEdgeLine key={edgeKey(edge)} edge={edge} flow={flow} map={map} />
        ))}
        {/* Nodes. */}
        {map.nodes.map((node) => (
          <FlowNodeMark
            key={node.id}
            id={node.id}
            map={map}
            flow={flow}
            beat={flow.activeId === node.id ? beat : null}
          />
        ))}
      </svg>

      {/* Glass-box cost/latency chips for the nodes that have reported. */}
      <div className="mt-1 flex flex-wrap gap-1">
        {map.nodes
          .filter((n) => ledger.has(n.id))
          .map((n) => (
            <LedgerChip key={n.id} label={n.short} fin={ledger.get(n.id)!} />
          ))}
      </div>
    </div>
  )
}

/**
 * Fetch the real graph topology once, falling back to the generated snapshot.
 *
 * The fallback is the initial value, so the map paints immediately and a failed
 * or unauthenticated fetch simply leaves the correct offline picture in place.
 */
function useAgentTopology(): AgentTopologyResponse {
  const [topology, setTopology] = useState<AgentTopologyResponse>(FALLBACK_TOPOLOGY)
  useEffect(() => {
    let live = true
    getAgentTopology(null)
      .then((served) => {
        if (live && served.nodes.length > 0) setTopology(served)
      })
      .catch(() => {
        /* backend unreachable — the snapshot fallback already renders. */
      })
    return () => {
      live = false
    }
  }, [])
  return topology
}

/** A single flow node: circle mark + short label, coloured by lifecycle. */
function FlowNodeMark({
  id,
  map,
  flow,
  beat,
}: {
  id: FlowNodeId
  map: FlowMap
  flow: FlowResolution
  beat: Beat | null
}): ReactElement | null {
  const node = map.nodes.find((n) => n.id === id)
  const pos = map.position[id]
  if (node === undefined || pos === undefined) return null
  const status = flow.status[id]
  const hue = SIGNALS[node.signal].hex

  const filled = status === 'active' || status === 'done'
  const fill = filled ? hue : IDLE_FILL
  const stroke = filled ? hue : IDLE_STROKE
  const labelY = pos.labelAbove ? pos.y - 15 : pos.y + 25

  return (
    <g>
      <title>{node.label}</title>
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
        // Silent stages report no events, so an unlit mark means "never reported",
        // not "skipped" — drawn dashed rather than faked as done.
        strokeDasharray={node.silent && !filled ? '2 3' : undefined}
        opacity={status === 'idle' ? 0.9 : 1}
      />
      <text
        x={pos.x}
        y={labelY}
        textAnchor="middle"
        fontSize={13}
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
function FlowEdgeLine({
  edge,
  flow,
  map,
}: {
  edge: FlowEdge
  flow: FlowResolution
  map: FlowMap
}): ReactElement | null {
  const a = map.position[edge.source]
  const b = map.position[edge.target]
  if (a === undefined || b === undefined) return null
  const active = isEdgeActive(edge, flow)
  const notTaken = isEdgeNotTaken(edge, flow)
  const source = map.nodes.find((n) => n.id === edge.source)
  const hue = SIGNALS[source?.signal ?? 'neutral'].hex

  return (
    <path
      d={edgePath(a, b, map.backEdges.has(edgeKey(edge)))}
      fill="none"
      stroke={active ? hue : IDLE_STROKE}
      strokeWidth={active ? 2.5 : 1.5}
      strokeDasharray={notTaken ? '3 4' : undefined}
      opacity={notTaken ? 0.5 : active ? 1 : 0.8}
      strokeLinecap="round"
    />
  )
}

/**
 * Path for one edge. Adjacent-layer edges are straight; anything that spans more
 * than one layer (a router arm that skips a stage, or the self-repair loop back
 * to `plan`) is bowed clear of the spine so it cannot hide behind the nodes it
 * flies over. Loops bow upward, skips downward — unless the source already sits
 * off-spine, in which case the arc stays on its own side.
 */
function edgePath(
  a: { x: number; y: number; layer: number },
  b: { x: number; y: number; layer: number },
  isBack: boolean,
): string {
  const span = Math.abs(b.layer - a.layer)
  if (span <= 1 && !isBack) return `M ${a.x} ${a.y} L ${b.x} ${b.y}`
  const lift = Math.min(46, 14 + span * 4)
  const direction = isBack || a.y < b.y ? -1 : 1
  const midX = (a.x + b.x) / 2
  const midY = (a.y + b.y) / 2 + direction * lift
  return `M ${a.x} ${a.y} Q ${midX} ${midY} ${b.x} ${b.y}`
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

/**
 * Index the latest `node_finished` per flow node (last write wins). Uses the same
 * served node ids as the map, so a chip can never appear for a stage the map does
 * not draw.
 */
function ledgerByFlowId(nodeLedger: NodeFinished[], map: FlowMap): Map<FlowNodeId, NodeFinished> {
  const index = new Map<FlowNodeId, NodeFinished>()
  for (const fin of nodeLedger) {
    const id = flowIdForNode(fin.node, map.ids)
    if (id !== null) index.set(id, fin)
  }
  return index
}
