'use client'

import { Activity, Gauge, Hash, Timer, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { getLatency } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { LatencyResponse, NodeLatency } from '@/lib/api/platform'

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
 * Per-node p95 bars — one horizontal track per node, each scaled to the slowest
 * node's p95 so the tail latencies read at a glance, with the p95 figure pinned in
 * an aligned tabular-mono right column (never inside the bar, so it stays legible).
 *
 * The bars are `aria-hidden`: the figure in the right column is the reading, and
 * announcing both would say every timing twice.
 */
function NodeP95Bars({ nodes }: { nodes: NodeLatency[] }): ReactElement {
  const maxP95 = Math.max(1, ...nodes.map((n) => n.p95_ms))
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <span className="eyebrow">p95 latency · per node</span>
        <span className="eyebrow">ms</span>
      </div>

      <ul className="flex flex-col gap-1.5">
        {nodes.map((n) => (
          <li key={n.node} className="grid grid-cols-[minmax(0,1fr)_1.9fr_auto] items-center gap-3">
            <Figure className="min-w-0 truncate text-foreground">{n.node}</Figure>
            <div aria-hidden className="relative h-3 overflow-hidden rounded-sm bg-surface-2">
              <div
                className="absolute inset-y-0 left-0 rounded-sm bg-blue-600"
                style={{ width: `${Math.max((n.p95_ms / maxP95) * 100, 1.5)}%` }}
              />
            </div>
            <span className="w-20 text-right">
              <Ms value={n.p95_ms} className="font-semibold text-foreground" />
            </span>
          </li>
        ))}
      </ul>
    </div>
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
        <Absence
          figure="Every percentile on this page"
          why="The latency window is per-process and resets on restart, and no run has completed in this one yet."
          needed="Run a query. Percentiles appear here as soon as the first run finishes — there is no seeded window."
        />
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
 * Latency — the `aegis` `/latency` read-surface. Per-run p50/p95/max summary
 * tiles plus a per-node breakdown (count · p50 · p95 · max · total) with a p95
 * bar visual, all drawn from real samples in a per-process rolling window. When
 * no runs have been recorded the view renders an honest empty state (never fake
 * zeros).
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
      <SectionHeader
        as="h1"
        eyebrow="latency · p50 · p95"
        title="Latency"
        note="Real samples from a per-process rolling window. The window resets on restart, so these are the timings of this process and no other."
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
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard
              label="Run p50"
              value={fmtMs(data.run_p50_ms) ?? 'no reading'}
              icon={Gauge}
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
              icon={Timer}
            />
          </div>

          {/* ── Per-node p95 bars ─────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis · /latency"
              title="Per-node p95"
              actions={
                <Badge tone="neutral" className="font-mono">
                  {data.source}
                </Badge>
              }
            />
            <CardBody>
              <NodeP95Bars nodes={data.per_node} />
            </CardBody>
          </Card>

          {/* ── Per-node table ────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis · /latency"
              title="Per-node breakdown"
              actions={
                <Badge tone="neutral">
                  <Figure>{data.per_node.length}</Figure> nodes
                </Badge>
              }
            />
            <CardBody>
              <NodeTable nodes={data.per_node} />
            </CardBody>
          </Card>

          <Receipt
            origin={data.source}
            detail={`${
              data.window_capacity === null
                ? 'window capacity not reported'
                : `window capacity ${data.window_capacity}`
            } · a per-process rolling buffer that resets on restart`}
          />
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
