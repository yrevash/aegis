import { Waypoints } from 'lucide-react'
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import ForceGraph2D from 'react-force-graph-2d'

import { OrchestrationMap } from '@/components/console/OrchestrationMap'
import { prefersReducedMotion, type Beat } from '@/components/console/motion'
import { Badge } from '@/components/ui/badge'
import { Card, CardHeader, CardTitle } from '@/components/ui/card'
import { InfoTip } from '@/components/ui/InfoTip'
import { SIGNALS, colorForKind } from '@/config/signals'
import type { GraphResponse } from '@/types/api'
import type { RunState } from '@/state/runReducer'

import { useElementSize } from './useElementSize'

/** A node once react-force-graph has augmented it with a position. */
type GraphNodeObj = { id: string; label: string; kind: string; x?: number; y?: number }
/** A link with its endpoints resolved to node objects after processing. */
type GraphLinkObj = {
  relation: string
  source: string | GraphNodeObj
  target: string | GraphNodeObj
}

/** A d3 force with tunable parameters (subset used here). */
interface D3Force {
  strength: (value: number) => unknown
  distance?: (value: number) => unknown
}

/** The subset of imperative methods this component calls. */
interface ForceMethods {
  zoomToFit: (durationMs?: number, paddingPx?: number, nodeFilter?: (node: object) => boolean) => void
  d3Force: (name: string) => D3Force | undefined
  d3ReheatSimulation: () => void
}

const edgeKey = (s: string, t: string, r: string): string => `${s}->${t}:${r}`
const nodeId = (n: string | GraphNodeObj): string => (typeof n === 'string' ? n : n.id)

// Light-canvas palette: soft neutrals for the dormant graph, ink hues on touch.
const LINK_IDLE = 'rgba(208,213,221,0.6)'
const NODE_IDLE = 'rgba(152,162,179,0.38)'
const NODE_STROKE_IDLE = 'rgba(208,213,221,0.9)'
const LABEL_ON = 'rgba(52,64,84,0.95)'
const LABEL_OFF = 'rgba(152,162,179,0.75)'

interface KnowledgeGraphProps {
  base: GraphResponse
  state: RunState
  /** Current active-beat, so the live node pulses in the shared hue. */
  beat: Beat | null
  /** True when no run is active — drives the idle attract-loop. */
  idle: boolean
}

/**
 * The knowledge-graph viz — the visual centerpiece, fused with the
 * {@link OrchestrationMap} above it as the "agent trajectory" layer. Nodes and
 * edges light in the retrieval hue as `retrieval` events arrive, directional
 * particles flow along traversed edges, and the active node pulses in the shared
 * active-beat hue. When no run is active the whole viz gently breathes and a
 * dormant scanline sweeps, so the screen stays alive between demos.
 */
export function KnowledgeGraph({ base, state, beat, idle }: KnowledgeGraphProps): ReactElement {
  const [wrapRef, size] = useElementSize<HTMLDivElement>()
  const fgRef = useRef<ForceMethods | null>(null)
  const [reduced] = useState(prefersReducedMotion)
  const [hoveredId, setHoveredId] = useState<string | null>(null)

  // Build the graph data once per base graph so the layout stays stable while
  // highlights change (react-force-graph mutates node objects in place).
  const data = useMemo(
    () => ({
      nodes: base.nodes.map((n) => ({ ...n })) as GraphNodeObj[],
      links: base.edges.map((e) => ({ ...e })) as GraphLinkObj[],
    }),
    [base],
  )

  const touchedNodes = useMemo(
    () => new Set(state.touchedNodes.map((n) => n.id)),
    [state.touchedNodes],
  )
  const activeNodes = useMemo(() => new Set(state.activeNodeIds), [state.activeNodeIds])
  const touchedEdges = useMemo(
    () => new Set(state.touchedEdges.map((e) => edgeKey(e.source, e.target, e.relation))),
    [state.touchedEdges],
  )
  // Distinct entity kinds in this run's evidence, for the colour legend.
  const kindLegend = useMemo(
    () => [...new Set(state.touchedNodes.map((n) => n.kind))].sort(),
    [state.touchedNodes],
  )

  // The active-node halo shares the current beat hue (falls back to retrieval).
  const haloHex = beat?.hex ?? SIGNALS.graph.hex

  // Once a run has touched nodes, scope the *visible* graph to that run's
  // evidence subgraph — the accumulated cross-run nodes are hidden, so the
  // picture reads as "what THIS answer stood on", not every doc ever retrieved.
  // The underlying force layout is left intact (stable), we just stop painting
  // the untouched nodes/edges and fit the view to what's shown.
  const hasRun = state.touchedNodes.length > 0
  const isNodeShown = (id: string): boolean => !hasRun || touchedNodes.has(id)

  const isLinkTouched = (link: GraphLinkObj): boolean =>
    touchedEdges.has(edgeKey(nodeId(link.source), nodeId(link.target), link.relation))
  const isLinkShown = (link: GraphLinkObj): boolean => !hasRun || isLinkTouched(link)

  // Fit the view to the shown subgraph on mount, on a fresh run, and as the
  // evidence nodes arrive (so the camera closes in on this query's subgraph).
  useEffect(() => {
    const timer = setTimeout(
      () => fgRef.current?.zoomToFit(600, 60, (n: object) => isNodeShown((n as GraphNodeObj).id)),
      500,
    )
    return () => clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [size.width, size.height, state.runId, state.touchedNodes.length])

  return (
    <Card className="flex h-full flex-col overflow-hidden">
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Waypoints className="size-4 text-graph-ink" />
        <CardTitle>Orchestration</CardTitle>
        <InfoTip label="About Orchestration">
          Which agent handled each step, drawn over the knowledge graph the run
          traversed. Multi-agent orchestration on LangGraph.
        </InfoTip>
        <Badge variant="graph" className="ml-auto">
          {touchedNodes.size}/{base.nodes.length} traversed
        </Badge>
      </CardHeader>

      {/* Fused agent-trajectory layer above the graph. */}
      <OrchestrationMap state={state} beat={beat} />

      <div ref={wrapRef} className="relative min-h-0 flex-1">
        <div className={idle && !reduced ? 'absolute inset-0 animate-idle-breathe' : 'absolute inset-0'}>
          {size.width > 0 && (
            <ForceGraph2D
              ref={fgRef as never}
              width={size.width}
              height={size.height}
              graphData={data}
              backgroundColor="rgba(0,0,0,0)"
              cooldownTicks={120}
              nodeRelSize={5}
              linkColor={(link: object) => {
                const l = link as GraphLinkObj
                if (!isLinkShown(l)) return 'rgba(0,0,0,0)'
                return isLinkTouched(l) ? SIGNALS.graph.hex : LINK_IDLE
              }}
              linkWidth={(link: object) => {
                const l = link as GraphLinkObj
                if (!isLinkShown(l)) return 0
                return isLinkTouched(l) ? 2 : 1
              }}
              linkDirectionalParticles={(link: object) =>
                isLinkTouched(link as GraphLinkObj) ? 3 : 0
              }
              linkDirectionalParticleWidth={2.5}
              linkDirectionalParticleColor={() => SIGNALS.graph.hex}
              nodeCanvasObject={(node: object, ctx: CanvasRenderingContext2D, scale: number) => {
                const n = node as GraphNodeObj
                // Scope: once a run is active, don't paint the accumulated
                // cross-run nodes — only this query's evidence subgraph.
                if (!isNodeShown(n.id)) return
                const x = n.x ?? 0
                const y = n.y ?? 0
                const touched = touchedNodes.has(n.id)
                const active = activeNodes.has(n.id)
                const hovered = n.id === hoveredId
                const r = active ? 6 : touched ? 5 : 3.5
                const kindColor = colorForKind(n.kind)

                if (active) {
                  // Time-based halo pulse — animates while directional particles
                  // keep the canvas repainting; static under reduced motion.
                  const phase = reduced ? 1 : 0.5 + 0.5 * Math.sin((performance.now() / 1400) * Math.PI * 2)
                  ctx.beginPath()
                  ctx.arc(x, y, r + 4 + phase * 4, 0, 2 * Math.PI)
                  ctx.fillStyle = withAlpha(haloHex, 0.14)
                  ctx.fill()
                }
                ctx.beginPath()
                ctx.arc(x, y, r, 0, 2 * Math.PI)
                ctx.fillStyle = touched ? kindColor : NODE_IDLE
                ctx.fill()
                if (touched) {
                  ctx.lineWidth = 1.5 / scale
                  ctx.strokeStyle = active ? haloHex : NODE_STROKE_IDLE
                  ctx.stroke()
                }

                // Label ONLY the active (frontier) node and whatever is hovered,
                // so filenames never stamp on top of each other. Everything else
                // is readable on hover. (Idle: labels appear when zoomed in.)
                const showLabel = active || hovered || (!hasRun && scale > 1.6)
                if (showLabel) {
                  const fontSize = Math.max(hovered || active ? 11 / scale : 10 / scale, 2.5)
                  ctx.font = `${fontSize}px "JetBrains Mono", monospace`
                  ctx.textAlign = 'center'
                  ctx.textBaseline = 'top'
                  ctx.fillStyle = touched ? LABEL_ON : LABEL_OFF
                  ctx.fillText(truncate(n.label, 26), x, y + r + 1.5)
                }
              }}
              onNodeHover={(node: object | null) =>
                setHoveredId(node ? (node as GraphNodeObj).id : null)
              }
              nodePointerAreaPaint={(node: object, color: string, ctx: CanvasRenderingContext2D) => {
                const n = node as GraphNodeObj
                if (!isNodeShown(n.id)) return
                ctx.fillStyle = color
                ctx.beginPath()
                ctx.arc(n.x ?? 0, n.y ?? 0, 7, 0, 2 * Math.PI)
                ctx.fill()
              }}
            />
          )}
        </div>

        {/* Idle attract-loop: a dormant scanline sweep over the resting graph. */}
        {idle && !reduced && (
          <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
            <div className="animate-scan absolute inset-x-0 h-16 bg-gradient-to-b from-transparent via-graph/10 to-transparent" />
          </div>
        )}

        {/* Caption + entity-kind legend when a run has traversed, else idle hint. */}
        <div className="pointer-events-none absolute bottom-2 left-3 flex max-w-[92%] flex-col gap-1">
          <span className="font-mono text-[0.62rem] text-muted-foreground/70">
            {hasRun
              ? `evidence for this answer · ${state.touchedNodes.length} entit${state.touchedNodes.length === 1 ? 'y' : 'ies'} · hover to read`
              : 'graph idle · run a query to traverse'}
          </span>
          {hasRun && kindLegend.length > 0 && (
            <div className="flex flex-wrap gap-x-2.5 gap-y-1">
              {kindLegend.map((kind) => (
                <span key={kind} className="flex items-center gap-1 font-mono text-[0.58rem] text-muted-foreground">
                  <span
                    aria-hidden
                    className="inline-block size-2 rounded-full"
                    style={{ background: colorForKind(kind) }}
                  />
                  {kind}
                </span>
              ))}
            </div>
          )}
        </div>
      </div>
    </Card>
  )
}

/** Truncate a label with an ellipsis so long filenames never sprawl. */
function truncate(label: string, max: number): string {
  return label.length > max ? `${label.slice(0, max - 1)}…` : label
}

/** Expand a #rrggbb hex to an `rgba()` string at the given alpha. */
function withAlpha(hex: string, alpha: number): string {
  const h = hex.replace('#', '')
  const r = parseInt(h.slice(0, 2), 16)
  const g = parseInt(h.slice(2, 4), 16)
  const b = parseInt(h.slice(4, 6), 16)
  return `rgba(${r},${g},${b},${alpha})`
}
