'use client'

import { ArrowRight, Waypoints } from 'lucide-react'
import dynamic from 'next/dynamic'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { beatFromSignal } from '@/components/console/motion'
import { QueryBar } from '@/components/console/QueryBar'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { ScrollArea } from '@/components/primitives/scroll-area'
import { BackendGate } from '@/components/shared/BackendGate'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { colorForKind } from '@/config/signals'
import { personasForRole } from '@/config/personas'
import { getGraph } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { GraphResponse } from '@/lib/api/types'
import type { GraphEdge, GraphNode, Role } from '@/lib/stream'
import type { RunState } from '@/state/runReducer'
import { useRunStream } from '@/state/useRunStream'

// `KnowledgeGraph` mounts `react-force-graph-2d` (canvas + d3, `window` at
// module scope), so load it client-only — same guard the Console uses.
const KnowledgeGraph = dynamic(
  () => import('@/components/graph/KnowledgeGraph').then((m) => m.KnowledgeGraph),
  {
    ssr: false,
    loading: () => (
      <Card className="flex h-full flex-col overflow-hidden">
        <CardHeader title="Knowledge graph" />
        <div className="min-h-0 flex-1 animate-pulse bg-surface-2/30" />
      </Card>
    ),
  },
)

const EMPTY_GRAPH: GraphResponse = { nodes: [], edges: [] }

/** One entity row in the list: the node plus its computed degree in view. */
interface EntityRow {
  node: GraphNode
  degree: number
}

const nodeId = (n: string | { id: string }): string => (typeof n === 'string' ? n : n.id)

/**
 * The entities + relations currently painted by the graph, mirroring the
 * component's own scoping: once a run has touched evidence the view narrows to
 * that subgraph, otherwise it shows the accumulated base graph from `/graph`.
 */
function viewOf(base: GraphResponse, state: RunState): {
  nodes: GraphNode[]
  edges: GraphEdge[]
  scoped: boolean
} {
  const scoped = state.touchedNodes.length > 0
  if (scoped) return { nodes: state.touchedNodes, edges: state.touchedEdges, scoped }
  return { nodes: base.nodes, edges: base.edges, scoped }
}

/** Entity rows sorted by degree (most-connected first), then label. */
function entityRows(nodes: GraphNode[], edges: GraphEdge[]): EntityRow[] {
  const degree = new Map<string, number>()
  for (const e of edges) {
    degree.set(e.source, (degree.get(e.source) ?? 0) + 1)
    degree.set(e.target, (degree.get(e.target) ?? 0) + 1)
  }
  return nodes
    .map((node) => ({ node, degree: degree.get(node.id) ?? 0 }))
    .sort((a, b) => b.degree - a.degree || a.node.label.localeCompare(b.node.label))
}

/** Distinct entity kinds present in view, for the colour legend. */
function kindsOf(nodes: GraphNode[]): string[] {
  return [...new Set(nodes.map((n) => n.kind))].sort()
}

/** A coloured kind chip — the entity type dot + label. */
function KindChip({ kind }: { kind: string }): ReactElement {
  return (
    <span className="inline-flex items-center gap-1.5 whitespace-nowrap">
      <span
        aria-hidden
        className="inline-block size-2.5 rounded-full"
        style={{ background: colorForKind(kind) }}
      />
      <span className="text-xs text-foreground">{kind}</span>
    </span>
  )
}

/**
 * Graph (§ knowledge graph) — a dedicated, large view of the typed entity graph.
 * The base graph is fetched from `/graph`; run a query and the graph highlights
 * the evidence subgraph the answer stood on, with the entity list + relations
 * narrowing to match. Offline every value is a clearly-labelled sample; live,
 * the nodes/edges are real and measured from the run stream.
 */
function GraphView({ role }: { role: Role }): ReactElement {
  // Live session token — a constant `null` here would fetch (and stream runs)
  // with no bearer on a reload, and never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const { state, running, start, reset } = useRunStream()
  const [personaId, setPersonaId] = useState(personasForRole(role)[0]?.id ?? '')
  const [graph, setGraph] = useState<GraphResponse>(EMPTY_GRAPH)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    void getGraph(token)
      .then((g) => {
        if (alive) setGraph(g)
      })
      // Fall back to the honest empty graph — the view renders its empty state
      // rather than waiting on data that will never arrive.
      .catch(() => {
        if (alive) setGraph(EMPTY_GRAPH)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const beat = beatFromSignal(state.lastSignal)
  const idle = !running && state.events.length === 0
  // Unlock the bar when a run parks at the approval gate (no modal here).
  const barBusy = running && state.phase !== 'awaiting_approval'

  const view = useMemo(() => viewOf(graph, state), [graph, state])
  const rows = useMemo(() => entityRows(view.nodes, view.edges), [view])
  const kinds = useMemo(() => kindsOf(view.nodes), [view.nodes])
  const nodeLabel = useMemo(() => {
    const m = new Map<string, string>()
    for (const n of view.nodes) m.set(n.id, n.label)
    return (id: string): string => m.get(id) ?? id
  }, [view.nodes])

  return (
    <div className="space-y-6">
      <PageHeader eyebrow="entities · relations" title="Graph" />

      <QueryBar
        role={role}
        personaId={personaId}
        onPersonaChange={setPersonaId}
        onRun={(q) => start(q, personaId, token)}
        onReset={reset}
        running={barBusy}
      />

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-3">
        {/* The graph — the large centrepiece. */}
        <div className="min-w-0 xl:col-span-2">
          <div className="h-[560px] lg:h-[680px]">
            <KnowledgeGraph base={graph} state={state} beat={beat} idle={idle} />
          </div>
        </div>

        {/* Entity list + kind legend. */}
        <Card className="flex min-w-0 flex-col overflow-hidden xl:col-span-1">
          <CardHeader
            title={
              <span className="flex items-center gap-2">
                <Waypoints className="size-4 shrink-0 text-blue-600" aria-hidden />
                Entities in view
              </span>
            }
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <InfoTip label="About Entities in view">
                  Every typed entity node the graph is painting, with its kind and its degree — how
                  many relations touch it. Once a run traverses, the list narrows to that
                  answer&apos;s evidence subgraph.
                </InfoTip>
                <Badge tone="graph">
                  {view.nodes.length} {view.nodes.length === 1 ? 'entity' : 'entities'}
                </Badge>
              </div>
            }
          />

          {/* Entity-kind colour legend. */}
          {kinds.length > 0 && (
            <div className="flex flex-wrap gap-x-3 gap-y-1.5 px-5 pt-3 pb-3">
              {kinds.map((kind) => (
                <KindChip key={kind} kind={kind} />
              ))}
            </div>
          )}

          <div className="min-h-0 flex-1">
            {rows.length === 0 ? (
              <div className="flex h-full min-h-40 items-center justify-center px-5 text-center text-sm text-muted-foreground">
                No entities yet — run a query to populate the graph.
              </div>
            ) : (
              <ScrollArea className="h-[360px] xl:h-[520px]">
                <Table>
                  <THead>
                    <TH>Entity</TH>
                    <TH>Kind</TH>
                    <TH className="text-right">Degree</TH>
                  </THead>
                  <TBody>
                    {rows.map(({ node, degree }) => (
                      <TR key={node.id}>
                        <TD className="max-w-[14rem] truncate font-medium">{node.label}</TD>
                        <TD>
                          <KindChip kind={node.kind} />
                        </TD>
                        <TD className="text-right font-mono tabular-nums text-muted-foreground">
                          {degree}
                        </TD>
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </ScrollArea>
            )}
          </div>
        </Card>
      </div>

      {/* Relations — the real relation phrases between entities in view. */}
      <Card>
        <CardHeader
          title="Relations"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <InfoTip label="About Relations">
                The directed edges between entities, each carrying its real relation phrase. These
                are the paths the retriever can traverse to reason over connected evidence.
              </InfoTip>
              <Badge tone="graph">
                {view.edges.length} {view.edges.length === 1 ? 'relation' : 'relations'}
              </Badge>
            </div>
          }
        />
        <CardBody>
          {view.edges.length === 0 ? (
            <p className="text-sm text-muted-foreground">
              No relations yet — run a query to traverse the graph.
            </p>
          ) : (
            <div className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {view.edges.map((e, i) => (
                <div
                  key={`${nodeId(e.source)}->${nodeId(e.target)}:${e.relation}:${i}`}
                  className="flex min-w-0 items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-sm"
                >
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                    {nodeLabel(nodeId(e.source))}
                  </span>
                  <span className="flex shrink-0 items-center gap-1 font-mono text-[0.68rem] text-blue-600">
                    <ArrowRight className="size-3" aria-hidden />
                    {e.relation}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-right font-medium text-foreground">
                    {nodeLabel(nodeId(e.target))}
                  </span>
                </div>
              ))}
            </div>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/** Client entry for the Graph section — gated on a reachable backend. */
export function GraphMount({ role }: { role: Role }): ReactElement {
  return (
    <BackendGate>
        <GraphView role={role} />
    </BackendGate>
  )
}
