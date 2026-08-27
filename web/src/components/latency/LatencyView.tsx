'use client'

import { Activity, Gauge as GaugeIcon, Hash, Timer, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { Figure } from '@/components/primitives/Figure'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { SceneState } from '@/components/illustration/Scene'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { getLatency } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { LatencyResponse, NodeLatency } from '@/lib/api/platform'

import { NodeRangeBars } from './NodeRangeBars'

/**
 * Format a millisecond figure, or say plainly that there is no reading.
 *
 * It returned an em dash, which in a column of timings is indistinguishable from
 * a zero and from a broken cell. DESIGN.md §1: a figure that cannot be sourced is
 * a stated absence in the slot the number would have occupied.
 */
function fmtMs(ms: number | null | undefined): string | null {
  if (ms == null) return null
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(1)}ms`
}

/** A timing cell: the figure, or the words for its absence. */
function Ms({ value, className }: { value: number | null | undefined; className?: string }): ReactElement {
  const text = fmtMs(value)
  if (text === null) return <span className="text-xs text-muted-foreground italic">no reading</span>
  return <Figure className={className}>{text}</Figure>
}

/**
 * How much of the rolling window is filled — the one bounded ratio on this page.
 *
 * DESIGN.md §2 allows a radial gauge for exactly this job: *one* value whose
 * position inside a bounded range is the point. `run_count` against
 * `window_capacity` is that value, and until now the two halves sat in different
 * places — the count on a tile, the capacity buried in a receipt — so nobody
 * could see that a "p95" was computed over four samples in a 200-slot buffer.
 *
 * A capacity the server did not report is an absence, never a denominator of 100.
 */
function WindowFill({ data }: { data: LatencyResponse }): ReactElement {
  const capacity = data.window_capacity
  const full = capacity != null && capacity > 0 && data.run_count >= capacity

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader eyebrow="rolling window" title="Window fill" />
      <CardBody className="flex min-w-0 flex-1 flex-col items-center gap-3">
        {capacity == null || capacity <= 0 ? (
          <Absence
            className="w-full text-left"
            figure="Window fill"
            why="This process did not report a window capacity, so the runs recorded have no denominator."
            needed="a capacity on GET /latency — the ratio is that count over that capacity"
          />
        ) : (
          <>
            <Gauge
              value={data.run_count / capacity}
              label="of the window filled"
              color="graph"
              size={148}
            />
            <p className="text-center text-xs text-muted-foreground">
              <Figure className="text-foreground">{data.run_count}</Figure> of{' '}
              <Figure className="text-foreground">{capacity}</Figure> slots
              {full ? ' · full, so the oldest samples are being dropped' : ''}
            </p>
          </>
        )}
        <Receipt
          className="mt-auto w-full"
          origin={data.source}
          detail="a per-process rolling buffer that resets on restart"
        />
      </CardBody>
    </Card>
  )
}

/**
 * Where the run's time actually goes — `total_ms` per node, as a composition.
 *
 * `total_ms` was a table column and nothing else, which makes "which node owns
 * the run's wall clock?" a subtraction the reader has to perform across six rows.
 * A percentile answers a different question: a node can have the worst p95 and
 * still be a rounding error in the total, because it ran twice.
 *
 * Four slices is the ordinal ramp's measured ceiling, so a fifth node folds into
 * a named `others` band rather than reaching for a fifth colour.
 */
function TimeComposition({ nodes, source }: { nodes: NodeLatency[]; source: string }): ReactElement {
  const timed = nodes
    .filter((n): n is NodeLatency & { total_ms: number } => n.total_ms != null && n.total_ms > 0)
    .sort((a, b) => b.total_ms - a.total_ms)
  const total = timed.reduce((sum, n) => sum + n.total_ms, 0)

  const folded =
    timed.length <= 4
      ? timed.map((n) => ({ name: n.node, value: n.total_ms }))
      : [
          ...timed.slice(0, 3).map((n) => ({ name: n.node, value: n.total_ms })),
          {
            name: `${timed.length - 3} others`,
            value: timed.slice(3).reduce((sum, n) => sum + n.total_ms, 0),
          },
        ]
  const slices: DonutDatum[] = folded.map((d, i) => ({
    name: d.name,
    value: d.value,
    color: 'graph',
    hex: rampHex(i, folded.length),
  }))

  const leader = timed[0] ?? null

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="aegis · /latency"
        title="Where the time goes"
        actions={
          <Badge tone="neutral" className="font-mono">
            {timed.length} timed
          </Badge>
        }
      />
      <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
        {timed.length === 0 ? (
          <Absence
            className="text-left"
            figure="Share of run time by node"
            why="No node in this window reported a total, so there is nothing to divide up."
            needed="a completed run — each node writes its own cumulative total"
          />
        ) : timed.length < 2 ? (
          /* One node holds all of it. A donut of a single 100% slice is a circle,
             so this states the count instead of drawing one. */
          <p className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-sm text-muted-foreground">
            <Figure size="stat" className="text-foreground">
              {fmtMs(total) ?? 'no reading'}
            </Figure>
            <span>all of it in</span>
            <Figure truncate className="min-w-0 text-foreground">{leader?.node}</Figure>
            <span>— one timed node, so there is no split to draw.</span>
          </p>
        ) : (
          <DonutChart
            data={slices}
            centerLabel={fmtMs(total) ?? '—'}
            centerSub="total"
            valueFormatter={(v) => fmtMs(v) ?? '—'}
            height={188}
          />
        )}
        <Receipt
          className="mt-auto"
          origin={`${source} · per_node.total_ms`}
          detail={
            leader == null
              ? 'no node reported a total'
              : `${leader.node} owns the largest share of the window's wall clock`
          }
        />
      </CardBody>
    </Card>
  )
}

/** The full per-node table: node, count, p50, p95, max, total — all tabular-mono. */
function NodeTable({ nodes }: { nodes: NodeLatency[] }): ReactElement {
  return (
    <Table>
      <THead>
        <TH className="text-left">Node</TH>
        <TH className="text-right">Count</TH>
        <TH className="text-right">p50</TH>
        <TH className="text-right">p95</TH>
        <TH className="text-right">Max</TH>
        <TH className="text-right">Total</TH>
      </THead>
      <TBody>
        {nodes.map((n) => (
          <TR key={n.node}>
            <TD>
              <Figure className="font-medium">{n.node}</Figure>
            </TD>
            <TD className="text-right">
              <Figure className="text-muted-foreground">{n.count}</Figure>
            </TD>
            <TD className="text-right">
              <Ms value={n.p50_ms} />
            </TD>
            <TD className="text-right">
              <Ms value={n.p95_ms} className="font-semibold text-foreground" />
            </TD>
            <TD className="text-right">
              <Ms value={n.max_ms} />
            </TD>
            <TD className="text-right">
              <Ms value={n.total_ms} className="text-muted-foreground" />
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}

/**
 * The honest empty state — no runs have been recorded in the per-process window
 * yet. It shows the real `source` + `window_capacity` provenance and what would
 * fill the window, NOT fabricated zeros dressed as measurements.
 */
function LatencyEmpty({ data }: { data: LatencyResponse }): ReactElement {
  return (
    <Card>
      <CardBody className="space-y-4">
        {/* A screen that correctly says "nothing measured yet" reads as broken unless
            something says the emptiness is deliberate. The scene does that; the
            `Absence` under it is still what carries the fact, and still what a screen
            reader gets. */}
        <SceneState name="empty" size="md">
          <Absence
            figure="Every percentile on this page"
            why="The latency window is per-process and resets on restart, and no run has completed in this one yet."
            needed="Run a query. Percentiles appear here as soon as the first run finishes — there is no seeded window."
          />
        </SceneState>
        <Receipt
          origin={data.source}
          detail={
            data.window_capacity === null
              ? 'window capacity not reported'
              : `window capacity ${data.window_capacity}`
          }
        />
      </CardBody>
    </Card>
  )
}

/**
 * Latency — the `aegis` `/latency` read-surface, drawn from real samples in a
 * per-process rolling window.
 *
 * Three marks, and each answers a different question off the same payload: the
 * {@link WindowFill} gauge is `run_count` inside `window_capacity` — how much of
 * this reading is even a sample; {@link NodeRangeBars} is the p50 → p95 span,
 * which node's tail will produce the timeout; and {@link TimeComposition} is
 * `total_ms` by node, which node owns the wall clock. The last two disagree
 * often, and that disagreement is the point.
 *
 * **There is no time-series here and none is drawn.** The payload carries no
 * timestamp of any kind — it is a rolling window of aggregates — so every mark on
 * this page is a snapshot composition or comparison. When no runs have been
 * recorded the view renders an honest empty state, never fake zeros.
 */
function LatencyView(): ReactElement {
  // Live session token — a constant `null` would 401 on a reload and, being
  // constant in the dependency array, never retry once the session was restored.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [data, setData] = useState<LatencyResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    let alive = true
    getLatency(token)
      .then((d) => {
        if (alive) {
          setData(d)
          setError(null)
        }
      })
      .catch(() => {
        if (alive) setError('Could not load latency. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token])

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    return load()
  }, [hydrated, load])

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="latency · p50 · p95"
        title="Latency"
        actions={
          <InfoTip label="What window these timings come from">
            Real samples from a per-process rolling window that resets on restart, so these are
            this process&rsquo;s timings and no other&rsquo;s.
          </InfoTip>
        }
      />

      {error ? (
        <ErrorState error={error} retry={load} />
      ) : data == null ? (
        <Card>
          <CardBody>
            <LoadingState rows={4} label="Reading the latency window…" />
          </CardBody>
        </Card>
      ) : data.empty ? (
        <LatencyEmpty data={data} />
      ) : (
        <>
          {/* ── Run-latency summary tiles ─────────────────────────────────────── */}
          {/* Three across at desktop, not five. Four of these tiles hold a duration and
              the fifth holds a **node name**, and a five-up row at 1440 leaves each tile
              143px of inner width — narrower than `guard_input` set in mono at any
              headline step. The row that fits its own widest value is the row that never
              paints over its own border. Five-up returns at 2xl, where there is room. */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-5 [&>*]:min-w-0">
            <StatCard
              label="Run p50"
              value={fmtMs(data.run_p50_ms) ?? 'no reading'}
              icon={GaugeIcon}
              tone="graph"
            />
            <StatCard
              label="Run p95"
              value={fmtMs(data.run_p95_ms) ?? 'no reading'}
              icon={TrendingUp}
              tone="graph"
            />
            <StatCard
              label="Run max"
              value={fmtMs(data.run_max_ms) ?? 'no reading'}
              icon={Activity}
              tone="risk"
            />
            <StatCard label="Runs recorded" value={String(data.run_count)} icon={Hash} />
            <StatCard
              label="Slowest node"
              value={data.slowest_node ?? 'no node timed'}
              // An identifier, not a figure — set at the stat step so the name reads
              // whole. See {@link StatCard}'s `valueSize`.
              valueSize="stat"
              icon={Timer}
            />
          </div>

          {/*
            Two short cards paired in one row rather than two full-width bands.
            `items-start` so the span card — which is only as tall as its node
            count, and with two or three nodes that is short — does not stretch to
            match the gauge beside it and turn into a strip of whitespace.
          */}
          <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[15rem_minmax(0,1fr)] [&>*]:min-w-0">
            <WindowFill data={data} />

            {/* ── Per-node latency spans ──────────────────────────────────── */}
            <Card className="flex min-w-0 flex-col">
              <CardHeader
                eyebrow="aegis · /latency"
                title="How wide each node's tail is"
                actions={
                  <Badge tone="neutral" className="font-mono">
                    {data.per_node.length} nodes
                  </Badge>
                }
              />
              <CardBody className="flex min-w-0 flex-1 flex-col gap-3">
                {data.per_node.length === 0 ? (
                  <Absence
                    className="text-left"
                    figure="Per-node percentiles"
                    why="Runs were recorded but no node reported a timing in this window."
                    needed="a run that reaches at least one instrumented node"
                  />
                ) : (
                  <NodeRangeBars nodes={data.per_node} />
                )}
                <Receipt
                  className="mt-auto"
                  origin={`${data.source} · per_node p50 · p95 · max`}
                  detail={
                    data.slowest_node == null
                      ? 'no node was named slowest'
                      : `slowest node ${data.slowest_node}`
                  }
                />
              </CardBody>
            </Card>
          </div>

          {/* ── Composition and the full table ────────────────────────────────── */}
          <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[22rem_minmax(0,1fr)] [&>*]:min-w-0">
            <TimeComposition nodes={data.per_node} source={data.source} />

            <DataPanel
              eyebrow="aegis · /latency"
              title="Per-node breakdown"
              collapsible
              maxHeight={480}
              actions={
                <Badge tone="neutral" className="font-mono">
                  {data.per_node.length} nodes
                </Badge>
              }
              footer={
                <Receipt
                  className="border-none pt-0"
                  origin={data.source}
                  detail={
                    data.window_capacity === null
                      ? 'window capacity not reported'
                      : `window capacity ${data.window_capacity}`
                  }
                />
              }
            >
              <NodeTable nodes={data.per_node} />
            </DataPanel>
          </div>
        </>
      )}
    </div>
  )
}

/** Client entry for the Latency section — gated on a reachable backend. */
export function LatencyMount(): ReactElement {
  return (
    <BackendGate>
      <LatencyView />
    </BackendGate>
  )
}
