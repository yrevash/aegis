'use client'

import {
  Background,
  BackgroundVariant,
  Controls,
  Handle,
  MarkerType,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type FitViewOptions,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import { useReducedMotion } from 'motion/react'
import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactElement,
  type RefObject,
} from 'react'

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
 * sequential stages laid out horizontally are 2,600 flow-units wide against a console
 * column's ~1,100, while the same fourteen laid out vertically are about 950 tall
 * against a panel's ~550 — the transposed spine is the orientation that survives being
 * fitted into the space this tab actually has.
 *
 * `LAYER_Y` is the tightest rhythm the boxes read at, and it is chosen for that fit: at
 * the old 84 the graph could not reach the readability floor in a 900px window.
 */
const LAYER_Y = 58
const ROW_X = 6

/** Node box, in flow units. Positions are centres, so half of each is subtracted. */
const NODE_W = 136
const NODE_H = 40

/**
 * How the graph is fitted into the box it is given.
 *
 * `minZoom` is the readability floor, not a preference: below roughly half size the
 * stage names stop being words, and a picture nobody can read is worse than one they
 * have to pan. So the graph shrinks to fit down to this, and past it the canvas pans
 * **inside its own bounded box** — drag, the zoom controls, or the arrow keys — rather
 * than growing the box and walking over the composer, which is what it used to do.
 */
const FIT: FitViewOptions = { padding: 0.06, minZoom: 0.5, maxZoom: 1 }

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
        'relative flex flex-col justify-center overflow-hidden rounded-lg border px-2.5 py-1 text-center',
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

      {/* Sized for the zoom it is actually read at. The whole graph fitted into a console
          panel lands around 0.6, so the label is set a little larger and a little tighter
          than a 1:1 reading of it would want. */}
      <span
        className={cn(
          'truncate text-[0.82rem] leading-tight font-medium',
          active ? 'text-foreground' : done ? 'text-foreground/80' : 'text-muted-foreground',
        )}
        title={data.label}
      >
        {data.short}
      </span>
      <span className="tabular truncate font-mono text-[0.64rem] leading-tight text-muted-foreground">
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

/**
 * Keep the graph fitted to the box, rather than fitting it once and hoping.
 *
 * React Flow's `fitView` prop fits on the first frame the nodes are measured on, and
 * never again. Three things invalidate that fit: the topology arriving from the backend
 * after the snapshot painted, the run traversing more of the graph, and — the one that
 * actually broke — the container changing size. This refits on all three.
 *
 * Deliberately **not** gated on `useNodesInitialized`: with these nodes it reports false
 * for the lifetime of the canvas even though the graph is measured and drawn, so gating
 * on it meant no refit ever ran and a resized window kept the zoom it was born with.
 * `fitView` is a no-op against unmeasured nodes anyway, and every trigger here fires
 * after a paint.
 */
function FitToBox({
  signature,
  boxRef,
}: {
  signature: string
  boxRef: RefObject<HTMLDivElement | null>
}): null {
  const { fitView } = useReactFlow()

  const fit = useCallback(() => {
    void fitView(FIT)
  }, [fitView])

  useEffect(() => {
    // One frame late: the status change that triggered this can also change a node's
    // measured size, and fitting to the pre-paint size lands slightly off.
    const frame = requestAnimationFrame(fit)
    return () => cancelAnimationFrame(frame)
  }, [fit, signature])

  useEffect(() => {
    const box = boxRef.current
    if (box === null || typeof ResizeObserver === 'undefined') return
    let timer: ReturnType<typeof setTimeout> | undefined
    const observer = new ResizeObserver(() => {
      // Deliberately *after* the current frame. React Flow keeps the pane's dimensions
      // in its own store, fed by its own ResizeObserver on the same element; fitting
      // from inside this callback computes against the size the pane had a moment ago,
      // which is how a resized window kept the zoom it was given at the old height.
      clearTimeout(timer)
      timer = setTimeout(fit, 80)
    })
    observer.observe(box)
    return () => {
      clearTimeout(timer)
      observer.disconnect()
    }
  }, [boxRef, fit])

  return null
}

interface FlowCanvasProps {
  state: RunState
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
function FlowCanvasInner({ state }: FlowCanvasProps): ReactElement {
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

  /**
   * What a refit is owed to.
   *
   * The topology can arrive after the snapshot painted, and a live run changes which
   * stages are lit and how far down the spine the frontier has reached — both change
   * what "the graph" is, and a fit from before them is stale. Statuses are folded in as
   * one string so a token arriving without changing anything visible does not refit.
   */
  const signature = useMemo(
    () => `${map.nodes.map((n) => `${n.id}:${flow.status[n.id] ?? 'idle'}`).join('|')}`,
    [map, flow],
  )

  const boxRef = useRef<HTMLDivElement>(null)
  const { fitView, setViewport, getViewport, zoomIn, zoomOut } = useReactFlow()

  /**
   * Keyboard parity with the mouse.
   *
   * The pane is pannable by drag and zoomable by its controls; neither is reachable
   * without a pointer. The box itself is the tab stop, arrows pan it, `+`/`-` zoom and
   * `0` re-fits — so a keyboard user can reach every part of a graph that is larger than
   * its box, which is exactly the case the zoom floor creates.
   */
  const onKeyDown = useCallback(
    (event: ReactKeyboardEvent<HTMLDivElement>) => {
      const step = event.shiftKey ? 160 : 56
      const duration = reduced ? 0 : 120
      const pan = (dx: number, dy: number): void => {
        const viewport = getViewport()
        void setViewport({ ...viewport, x: viewport.x + dx, y: viewport.y + dy }, { duration })
      }
      switch (event.key) {
        case 'ArrowUp':
          pan(0, step)
          break
        case 'ArrowDown':
          pan(0, -step)
          break
        case 'ArrowLeft':
          pan(step, 0)
          break
        case 'ArrowRight':
          pan(-step, 0)
          break
        case '+':
        case '=':
          void zoomIn({ duration })
          break
        case '-':
        case '_':
          void zoomOut({ duration })
          break
        case '0':
          void fitView({ ...FIT, duration })
          break
        default:
          return
      }
      event.preventDefault()
    },
    [fitView, getViewport, reduced, setViewport, zoomIn, zoomOut],
  )

  return (
    // `h-full min-h-0` and a `flex-1` canvas: the box is whatever height the tab panel
    // has, and the graph is fitted into it. It used to be the other way round — the box
    // grew to the graph's own extent inside a fixed-height column — and since React Flow
    // paints its nodes absolutely positioned, the overflow did not just spill, it painted
    // *over* the composer underneath. Bounding the box is the fix; the composer needs no
    // z-index of its own.
    <div className="flex h-full min-h-0 flex-col gap-2">
      <div className="flex shrink-0 flex-wrap items-center gap-x-3 gap-y-1">
        <BranchTag branch={flow.branch} denied={flow.denied} />
        <Legend />
      </div>

      <div
        ref={boxRef}
        tabIndex={0}
        // `application`, not `group`: a screen reader in browse mode eats the arrow keys
        // before the handler below sees them, and arrows are how a keyboard user reaches
        // the part of the graph that is off the box.
        role="application"
        aria-roledescription="Graph canvas"
        aria-label="The compiled agent graph, with the stages this run executed"
        aria-describedby="flow-canvas-keys"
        onKeyDown={onKeyDown}
        className={cn(
          'relative min-h-0 w-full min-w-0 flex-1 overflow-hidden',
          'rounded-lg border border-border bg-surface-2/40',
          'focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/25 focus-visible:outline-none',
        )}
      >
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={NODE_TYPES}
          fitView
          fitViewOptions={FIT}
          proOptions={{ hideAttribution: true }}
          nodesDraggable={false}
          nodesConnectable={false}
          elementsSelectable={false}
          // The wheel keeps belonging to the page: below `lg` the console is in document
          // flow, and a canvas that swallowed the wheel would trap a phone mid-scroll.
          panOnScroll={false}
          zoomOnScroll={false}
          preventScrolling={false}
          minZoom={0.3}
          maxZoom={1.6}
          panOnDrag
        >
          <Background variant={BackgroundVariant.Dots} gap={18} size={1} color="#e5edf5" />
          <Controls showInteractive={false} position="bottom-right" aria-label="Zoom and fit the graph" />
          <FitToBox signature={signature} boxRef={boxRef} />
        </ReactFlow>
      </div>

      <p id="flow-canvas-keys" className="sr-only">
        Arrow keys pan the graph, plus and minus zoom, zero fits it to the box.
      </p>
    </div>
  )
}

/**
 * `ReactFlowProvider` is what lets this component refit the graph itself.
 *
 * `useReactFlow` — the only way to re-run `fitView` after the first frame, which is the
 * whole point here — has to be called under a provider, and the one React Flow mounts
 * internally is *inside* `<ReactFlow>`, out of reach of the component that renders it.
 */
export function FlowCanvas({ state }: FlowCanvasProps): ReactElement {
  return (
    <ReactFlowProvider>
      <FlowCanvasInner state={state} />
    </ReactFlowProvider>
  )
}
