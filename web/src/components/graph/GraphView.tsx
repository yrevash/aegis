'use client'

import { ArrowRight } from 'lucide-react'
import dynamic from 'next/dynamic'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { beatFromSignal } from '@/components/console/motion'
import { QueryBar } from '@/components/console/QueryBar'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { BackendGate } from '@/components/shared/BackendGate'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { colorForKind } from '@/config/signals'
import { degreeDistribution, kindCounts } from './graphStats'
import { personasForRole } from '@/config/personas'
import { getGraph } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
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
    // The title matches the mounted card's, so the header does not change word
    // under the reader when the canvas arrives.
    loading: () => (
      <Card className="flex h-full flex-col overflow-hidden" role="status" aria-live="polite">
        <CardHeader title="Orchestration" />
        <span className="sr-only">Loading the knowledge graph…</span>
        <div aria-hidden className="min-h-0 flex-1 animate-pulse bg-surface-2/30" />
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

/**
 * How many bars a distribution needs before a plotted axis earns its height.
 *
 * A histogram of one or two bars is not a distribution — it is two numbers drawn
 * inside 200px of empty gridlines, which is the single loudest way a panel can
 * look broken. Below this, the same counts are stated as figures and the card
 * shrinks to fit them.
 */
const MIN_BARS = 3

/** A category and its count, for the compact path below {@link MIN_BARS}. */
interface CountItem {
  label: string
  value: number
}

/** Too few categories to plot: the counts themselves, at figure size. */
function CountStrip({ items, label }: { items: CountItem[]; label: string }): ReactElement {
  return (
    <ul className="flex flex-wrap gap-2" aria-label={label}>
      {items.map((item) => (
        <li
          key={item.label}
          className="flex min-w-0 flex-col gap-0.5 rounded-md border border-border bg-surface-2/40 px-3 py-2"
        >
          <span className="eyebrow truncate" title={item.label}>
            {item.label}
          </span>
          <Figure size="stat">{item.value}</Figure>
        </li>
      ))}
    </ul>
  )
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
  // Whether `/graph` has answered. Until it has, an empty graph is unknown
  // rather than absent, and the canvas keeps its place instead of flashing an
  // empty state that the very next render replaces.
  const [graphLoaded, setGraphLoaded] = useState(false)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    void getGraph(token)
      .then((g) => {
        if (alive) {
          setGraph(g)
          setGraphLoaded(true)
        }
      })
      // Fall back to the honest empty graph — the view renders its empty state
      // rather than waiting on data that will never arrive.
      .catch(() => {
        if (alive) {
          setGraph(EMPTY_GRAPH)
          setGraphLoaded(true)
        }
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
  // Both charts are computed here, from the returned shape — `GraphResponse`
  // carries no numeric field at all — so both receipts name the derivation.
  const degrees = useMemo(() => degreeDistribution(rows), [rows])
  const kindBars = useMemo(() => kindCounts(view.nodes.map((n) => n.kind)), [view.nodes])
  const derivedFrom = view.scoped
    ? '/query stream · the touched subgraph'
    : 'GET /graph · counted in the browser'
  // Past this many cells the off-screen ones stop being laid out at all.
  const longRelationList = view.edges.length > 50
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
          {graphLoaded && view.nodes.length === 0 ? (
            // An empty force graph is 680px of nothing — the largest blank
            // region this portal can produce. It says the same thing in a
            // panel the size of the sentence instead.
            <Card className="min-w-0">
              <CardHeader eyebrow="entities · relations" title="Orchestration" />
              <CardBody>
                {/*
                  What this view is actually filled by, stated instead of an
                  instruction. `GET /graph` returns the durable Neo4j graph the
                  ingest pipeline's `graph` stage projects into (its own stage
                  facts report `projected_entities` / `projected_relations`),
                  unioned with whatever the current run's retrieval touched. A
                  query cannot add an entity to it — `viewOf` only ever *narrows*
                  to `state.touchedNodes`, which are a subset of what is already
                  there — so the old "run a query to build an evidence subgraph"
                  told an operator to take an action that could not work. The
                  second half is the other true reading of an empty payload: the
                  route falls back to the in-process slice when the durable store
                  cannot be read, and Neo4j holding entities the API cannot reach
                  arrives here as exactly these zero nodes.
                */}
                <SceneState name="curious" size="md">
                  <EmptyState
                    title="No entity in the graph"
                    body="Document ingestion writes this graph; a query only highlights what it already holds. Empty means nothing ingested — or the graph store is unreadable."
                    className="text-left"
                  />
                </SceneState>
              </CardBody>
            </Card>
          ) : (
            /* The canvas sizes itself from this box, so the box must shrink with
               the viewport — a fixed 560px tall graph is most of a phone screen. */
            <div className="h-[360px] min-w-0 sm:h-[480px] lg:h-[600px] xl:h-[680px]">
              <KnowledgeGraph base={graph} state={state} beat={beat} idle={idle} />
            </div>
          )}
        </div>

        {/* Entity list + kind legend. `DataPanel` owns the scroll: a bare table
            here widened the document rather than the panel at 390. */}
        <DataPanel
          className="min-w-0 xl:col-span-1"
          title="Entities in view"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <InfoTip label="About Entities in view">
                Degree is how many relations touch an entity; a run narrows the list to its own
                evidence subgraph.
              </InfoTip>
              <Badge tone="graph">
                <Figure className="text-xs leading-4">{view.nodes.length}</Figure>
                {view.nodes.length === 1 ? 'entity' : 'entities'}
              </Badge>
            </div>
          }
          toolbar={kinds.length > 0 ? kinds.map((kind) => <KindChip key={kind} kind={kind} />) : undefined}
          maxHeight={rows.length === 0 ? undefined : 520}
        >
          {rows.length === 0 ? (
            <SceneState name="empty" className="py-4">
              <p className="text-sm font-medium text-foreground">No entities in view</p>
              {/* Same correction as the canvas beside it: this list is `viewOf(graph,
                  state)`, so a run can only narrow it. Nothing a query does adds a row. */}
              <p className="mt-1 text-sm text-muted-foreground">
                Document ingestion fills the graph. A query narrows this list, never adds to it.
              </p>
            </SceneState>
          ) : (
            <Table>
              <THead>
                <TH>Entity</TH>
                <TH>Kind</TH>
                <TH className="text-right">Degree</TH>
              </THead>
              <TBody>
                {rows.map(({ node, degree }) => (
                  <TR key={node.id}>
                    <TD className="max-w-[14rem] font-medium">
                      <span className="block truncate" title={node.label}>
                        {node.label}
                      </span>
                    </TD>
                    <TD>
                      <KindChip kind={node.kind} />
                    </TD>
                    <TD className="text-right">
                      <Figure className="text-muted-foreground">{degree}</Figure>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          )}
        </DataPanel>
      </div>

      {/* Two distributions, both computed from the returned graph — the API
          carries no numeric field, and each receipt says so. */}
      {view.nodes.length > 0 && (
        // `items-start`, so a card that took the compact path is not stretched to
        // its neighbour's chart height — which would put the dead space back
        // inside the card instead of removing it.
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-2">
          <Card className="min-w-0">
            <CardHeader eyebrow="relations per entity" title="How connected the graph is" />
            <CardBody className="space-y-3 pt-4">
              {degrees.length < MIN_BARS ? (
                <CountStrip
                  label="Entities per degree"
                  items={degrees.map((d) => ({
                    label: `${d.degree} relation${d.degree === '1' ? '' : 's'}`,
                    value: d.entities,
                  }))}
                />
              ) : (
                <BarChart
                  allowDecimals={false}
                  data={degrees}
                  index="degree"
                  category="entities"
                  color="graph"
                  valueFormatter={(v) => `${v} ${v === 1 ? 'entity' : 'entities'}`}
                  height={196}
                />
              )}
              <Receipt
                origin={derivedFrom}
                detail={`degree counted over ${view.edges.length} edges`}
              />
            </CardBody>
          </Card>

          <Card className="min-w-0">
            <CardHeader eyebrow="entities per kind" title="What is in the graph" />
            <CardBody className="space-y-3 pt-4">
              {kindBars.length < MIN_BARS ? (
                <CountStrip
                  label="Entities per kind"
                  items={kindBars.map((k) => ({ label: k.kind, value: k.entities }))}
                />
              ) : (
                <BarChart
                  allowDecimals={false}
                  data={kindBars}
                  index="kind"
                  category="entities"
                  color="graph"
                  valueFormatter={(v) => `${v} ${v === 1 ? 'entity' : 'entities'}`}
                  height={196}
                />
              )}
              <Receipt
                origin={derivedFrom}
                detail={`node.kind tallied over ${view.nodes.length} entities`}
              />
            </CardBody>
          </Card>
        </div>
      )}

      {/* Relations — the real relation phrases between entities in view. */}
      <Card>
        <CardHeader
          title="Relations"
          actions={
            <div className="flex flex-wrap items-center gap-2">
              <InfoTip label="About Relations">
                The directed edges the retriever can traverse, each with its real relation phrase.
              </InfoTip>
              <Badge tone="graph">
                <Figure className="text-xs leading-4">{view.edges.length}</Figure>
                {view.edges.length === 1 ? 'relation' : 'relations'}
              </Badge>
            </div>
          }
        />
        <CardBody>
          {view.edges.length === 0 ? (
            <SceneState name="curious" size="md" className="py-4">
              <p className="text-sm font-medium text-foreground">No relations traversed yet</p>
              <p className="mt-1 text-sm text-muted-foreground">Ask a question to walk the edges.</p>
            </SceneState>
          ) : (
            <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-3">
              {view.edges.map((e, i) => (
                <li
                  key={`${nodeId(e.source)}->${nodeId(e.target)}:${e.relation}:${i}`}
                  className={cn(
                    'flex min-w-0 items-center gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-sm',
                    // Past ~50 rows the browser skips rendering what is off-screen.
                    // The intrinsic size is the row's real height, so the scrollbar
                    // does not jump as cells enter and leave.
                    longRelationList && '[contain-intrinsic-size:auto_2.5rem] [content-visibility:auto]',
                  )}
                >
                  <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                    {nodeLabel(nodeId(e.source))}
                  </span>
                  {/* The relation phrase is extracted text, not a code — it can be a
                      whole sentence ("enters after fifteen minutes without a request"),
                      and this span was `shrink-0` with no `truncate`. So it set the row's
                      min-content width, ran 361px past its own card and 290px past a
                      1440 viewport, and took the target label out with it — every
                      ancestor being `overflow-x: visible`, nothing clipped it. It shrinks
                      and ellipsises now, and the full phrase is on the row's `title`. */}
                  <span
                    className="flex min-w-0 shrink items-center gap-1 font-mono text-[0.68rem] text-blue-600"
                    title={e.relation}
                  >
                    <ArrowRight className="size-3 shrink-0" aria-hidden />
                    <span className="min-w-0 truncate">{e.relation}</span>
                  </span>
                  <span className="min-w-0 flex-1 truncate text-right font-medium text-foreground">
                    {nodeLabel(nodeId(e.target))}
                  </span>
                </li>
              ))}
            </ul>
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
