'use client'

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { useReducedMotion } from 'motion/react'
import { useMemo, type ReactElement } from 'react'

import '@xyflow/react/dist/style.css'

import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import {
  buildFlowMap,
  edgeKey,
  isEdgeActive,
  isEdgeNotTaken,
  resolveFlow,
  type FlowResolution,
  type FlowStatus,
} from './orchestration'
import { useAgentTopology } from './useAgentTopology'

/**
 * The layered layout in `orchestration.ts` is measured for a dense 112px SVG strip that
 * runs left to right. This canvas **transposes** it: layer becomes Y and the row rank
 * becomes X, so the graph runs top to bottom.
 *
 * That is a presentation choice, not a second layout. The tested part — which layer a
 * stage sits in, which row it takes inside that layer, which edges close a cycle — is
 * `layoutFlow`'s and is read, not recomputed. What changes is only that fourteen
 * sequential stages laid out horizontally are 2,600 flow-units wide and shrink to an
 * illegible ribbon inside a console column, while the same fourteen laid out vertically
 * fit the column's width at full size and scroll in the direction a page already
 * scrolls.
 */
const LAYER_Y = 84
const ROW_X = 3.4

/** Node box, in flow units. Positions are centres, so half of each is subtracted. */
const NODE_W = 136
const NODE_H = 48

/** Idle mark colours for the light canvas — a stage that has not run yet. */
const IDLE_STROKE = '#e5edf5'

interface StageData extends Record<string, unknown> {
  short: string
  label: string
  status: FlowStatus
  hex: string
  visits: number
  silent: boolean
  entry: boolean
  terminal: boolean
  live: boolean
}

/**
 * One stage box.
 *
 * Four handles, not two: the self-repair loop (`reflect → plan`) runs *backwards*, and
 * routing it back through the same top/bottom ports would draw a wire straight through
 * every stage between them. The back pair leaves the left edge and re-enters the right,
 * so the loop bows out beside the spine and reads as the loop it is.
 */
function StageNode({ data }: NodeProps<Node<StageData>>): ReactElement {
  const { status, hex, visits, silent, entry, terminal, live } = data
  const active = status === 'active'
  const done = status === 'done'

  return (
    <div
      className={cn(
        'relative flex flex-col justify-center rounded-lg border px-2.5 py-1.5 text-center',
        'transition-shadow duration-[var(--dur-base)]',
        active ? 'bg-card shadow-hover' : done ? 'bg-card' : 'bg-surface-2/70',
      )}
      style={{
        width: NODE_W,
        height: NODE_H,
        borderColor: active || done ? hex : IDLE_STROKE,
        borderWidth: active ? 2 : 1,
      }}
    >
      <Handle type="target" position={Position.Top} id="in" className="!opacity-0" isConnectable={false} />
      <Handle type="source" position={Position.Bottom} id="out" className="!opacity-0" isConnectable={false} />
      <Handle type="source" position={Position.Left} id="back-out" className="!opacity-0" isConnectable={false} />
      <Handle type="target" position={Position.Right} id="back-in" className="!opacity-0" isConnectable={false} />

      {active && (
        <span
          aria-hidden
          className="animate-pip absolute -top-1 -right-1 size-2 rounded-full"
          style={{ backgroundColor: hex, ['--pip-color' as string]: hex }}
        />
      )}

      <span
        className={cn(
          'truncate text-[0.76rem] font-medium',
          active ? 'text-foreground' : done ? 'text-foreground/80' : 'text-muted-foreground',
        )}
        title={data.label}
      >
        {data.short}
      </span>
      <span className="tabular truncate font-mono text-[0.62rem] text-muted-foreground">
        {/* A stage that emits no events of its own says so, rather than being drawn as
            skipped — `recall_memory` and `persist_memory` are wired plain by design. */}
        {silent && status === 'idle'
          ? 'unreported'
          : visits > 1
            ? `ran ${visits}×`
            : entry
              ? 'entry'
              : terminal
                ? 'terminal'
                : live
                  ? 'running'
                  : done
                    ? 'done'
                    : ''}
      </span>
    </div>
  )
}

const NODE_TYPES = { stage: StageNode }

/**
 * Which branch the run took, in the run's own terms.
 *
 * `denied` is its own word rather than a shade of "autonomous": a run whose action was
 * refused at the tool boundary did *not* act autonomously, and calling it that would be
 * the single most misleading label on this screen.
 */
function BranchTag({ branch, denied }: { branch: FlowResolution['branch']; denied: boolean }): ReactElement | null {
  if (branch === null) return null
  if (denied) {
    return (
      <span className="rounded-md border border-block/60 bg-block/15 px-1.5 py-0.5 font-mono text-[0.62rem] tracking-wide text-block-ink">
        action denied
      </span>
    )
  }
  return (
    <span
      className={cn(
        'rounded-md border px-1.5 py-0.5 font-mono text-[0.62rem] tracking-wide',
        branch === 'gated'
          ? 'border-risk/60 bg-risk/15 text-risk-ink'
          : 'border-ok/60 bg-ok/15 text-ok-ink',
      )}
    >
      {branch === 'gated' ? 'via human approval' : 'autonomous'}
    </span>
  )
}

/** What the three line treatments mean. Colour is never the only carrier. */
function Legend(): ReactElement {
  return (
    <span className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.68rem] text-muted-foreground">
      <span className="flex items-center gap-1.5">
        <span aria-hidden className="h-0.5 w-4 rounded-full" style={{ backgroundColor: SIGNALS.graph.hex }} />
        traversed
      </span>
      <span className="flex items-center gap-1.5">
        <span aria-hidden className="h-px w-4 rounded-full opacity-40" style={{ backgroundColor: IDLE_STROKE }} />
        not taken
      </span>
      <span className="flex items-center gap-1.5">
        <span
          aria-hidden
          className="h-px w-4"
          style={{ backgroundImage: `repeating-linear-gradient(90deg, ${IDLE_STROKE} 0 4px, transparent 4px 7px)` }}
        />
        undecided branch
      </span>
    </span>
  )
}

interface FlowCanvasProps {
  state: RunState
  /** Floor for the canvas height. The graph's own extent wins when it is taller. */
  height?: number
}

/**
 * The Flow tab — the compiled agent graph, drawn live.
 *
 * Every derivation here is the one already in `orchestration.ts`: `buildFlowMap` lays
 * the served topology out in layers, `resolveFlow` says which stage is the frontier,
 * how many times each ran and which edges the run actually traversed, and
 * `isEdgeNotTaken` ghosts the road not taken once its router has decided. This file
 * only paints them — porting the map to React Flow does not get to fork the logic that
 * `resolveFlow` is tested on.
 *
 * The topology is **served** (`GET /agent/topology`, read off the compiled LangGraph),
 * with the generated snapshot as the initial value, so the picture is the real graph
 * from the first frame and never a hand-drawn diagram that can drift.
 */
export function FlowCanvas({ state, height = 420 }: FlowCanvasProps): ReactElement {
  const topology = useAgentTopology()
  const reduced = useReducedMotion() ?? false
  const map = useMemo(() => buildFlowMap(topology), [topology])
  const flow = useMemo(() => resolveFlow(state, map), [state, map])

  const flags = useMemo(() => {
    const out = new Map<string, { entry: boolean; terminal: boolean }>()
    for (const node of topology.nodes ?? []) {
      out.set(node.id, { entry: node.entry, terminal: node.terminal })
    }
    return out
  }, [topology])

  const nodes = useMemo<Node<StageData>[]>(
    () =>
      map.nodes.map((node) => {
        const point = map.position[node.id] ?? { x: 0, y: 0, layer: 0, labelAbove: false }
        const flag = flags.get(node.id) ?? { entry: false, terminal: false }
        const status = flow.status[node.id] ?? 'idle'
        // `point.y` is the row rank around the spine (`viewBox.height / 2`); transposed,
        // it is how far the stage sits off the vertical spine.
        const rank = point.y - map.viewBox.height / 2
        return {
          id: node.id,
          type: 'stage',
          position: {
            x: rank * ROW_X - NODE_W / 2,
            y: point.layer * LAYER_Y - NODE_H / 2,
          },
          draggable: false,
          selectable: false,
          connectable: false,
          data: {
            short: node.short,
            label: node.label,
            status,
            hex: SIGNALS[node.signal].hex,
            visits: flow.visits[node.id] ?? 0,
            silent: node.silent,
            entry: flag.entry,
            terminal: flag.terminal,
            live: status === 'active',
          },
        }
      }),
    [map, flow, flags],
  )

  const edges = useMemo<Edge[]>(
    () =>
      map.edges.map((edge) => {
        const key = edgeKey(edge)
        const back = map.backEdges.has(key)
        const lit = isEdgeActive(edge, flow)
        const ghost = isEdgeNotTaken(edge, flow)
        const hex = lit ? SIGNALS.graph.hex : IDLE_STROKE
        return {
          id: key,
          source: edge.source,
          target: edge.target,
          sourceHandle: back ? 'back-out' : 'out',
          targetHandle: back ? 'back-in' : 'in',
          type: 'smoothstep',
          // Only a lit edge on a *live* run animates. A finished run's whole path would
          // otherwise crawl forever, which is motion for its own sake.
          animated: lit && state.running && !reduced,
          style: {
            stroke: hex,
            strokeWidth: lit ? 2 : 1,
            strokeDasharray: edge.conditional && !lit ? '4 3' : undefined,
            opacity: ghost ? 0.35 : 1,
          },
          markerEnd: {
            type: MarkerType.ArrowClosed,
            width: 12,
            height: 12,
            color: hex,
          },
        }
      }),
    [map, flow, state.running, reduced],
  )

  // The canvas is as tall as the graph, and the page scrolls.
  //
  // The alternative — a fixed 600px box with `fitView` — is what this looked like first,
  // and it is the failure mode of every embedded graph: fourteen sequential stages fitted
  // into 600px is a zoom of about 0.45, at which the stage names stop being words. A
  // reader cannot act on a picture they cannot read, so the box grows to the graph
  // instead and the whole thing stays at full size. `fitView` is pinned to zoom 1 so it
  // only ever centres.
  const maxLayer = Math.max(0, ...map.nodes.map((n) => map.position[n.id]?.layer ?? 0))
  const canvasHeight = Math.max(height, maxLayer * LAYER_Y + NODE_H + 48)

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <BranchTag branch={flow.branch} denied={flow.denied} />
        <Legend />
      </div>

      <div
        className="w-full overflow-hidden rounded-lg border border-border bg-surface-2/40"
        style={{ height: canvasHeight }}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          // `maxZoom: 1` keeps a short run from being blown up past its natural size;
          // the floor is the readability floor — below it the stage labels stop being
          // words, so the canvas pans instead of shrinking further.
          fitViewOptions={{ padding: 0.03, minZoom: 1, maxZoom: 1 }}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          panOnScroll={false}
          zoomOnScroll={false}
          preventScrolling={false}
          minZoom={0.3}
          maxZoom={1.6}
          panOnDrag
          aria-label="The compiled agent graph, with the stages this run executed"
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#e5edf5" />
          <Controls showInteractive={false} position="bottom-right" />
        </ReactFlow>
      </div>
    </div>
  )
}
