'use client'

import { Activity, Gauge, Hash, Loader2, Timer, TrendingUp } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { getLatency } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { LatencyResponse, NodeLatency } from '@/lib/api/platform'

/** Format a millisecond figure honestly — `null` (no reading) reads as an em dash. */
function fmtMs(ms: number | null | undefined): string {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${ms.toFixed(1)}ms`
}

/**
 * Per-node p95 bars — a NodeGantt-style horizontal track per node, each bar
 * scaled to the slowest node's p95 so the tail latencies read at a glance, with
 * the p95 figure pinned in an aligned tabular-mono right column (never inside
 * the bar, so it stays legible).
 */
function NodeP95Bars({ nodes }: { nodes: NodeLatency[] }): ReactElement {
  const maxP95 = Math.max(1, ...nodes.map((n) => n.p95_ms))
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between">
        <span className="eyebrow flex items-center gap-1.5">
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: 'var(--blue-600)' }}
          />
          p95 latency · per node
        </span>
        <span className="tabular font-mono text-[0.62rem] text-muted-foreground">ms</span>
      </div>

      <div className="flex flex-col gap-1.5">
        {nodes.map((n) => (
          <div
            key={n.node}
            className="grid grid-cols-[minmax(0,1fr)_1.9fr_auto] items-center gap-3"
          >
            <span className="min-w-0 truncate font-mono text-[0.74rem] text-foreground">
              {n.node}
            </span>
            <div className="relative h-4 rounded-sm bg-muted/50">
              <div
                className="absolute inset-y-0 rounded-sm"
                style={{
                  left: 0,
                  width: `${Math.max((n.p95_ms / maxP95) * 100, 1.5)}%`,
                  background: 'var(--blue-600)',
                }}
              />
            </div>
            <span className="tabular w-20 text-right font-mono text-[0.76rem] font-semibold text-foreground">
              {fmtMs(n.p95_ms)}
            </span>
          </div>
        ))}
      </div>
    </div>
  )
}

/** The full per-node table: node, count, p50, p95, max, total — all tabular-mono. */
function NodeTable({ nodes }: { nodes: NodeLatency[] }): ReactElement {
  return (
    <div className="overflow-hidden rounded-xl border border-border">
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
              <TD className="font-mono text-[0.8rem] font-medium">{n.node}</TD>
              <TD className="tabular text-right font-mono text-[0.8rem] text-muted-foreground">
                {n.count}
              </TD>
              <TD className="tabular text-right font-mono text-[0.8rem]">{fmtMs(n.p50_ms)}</TD>
              <TD className="tabular text-right font-mono text-[0.8rem] font-semibold text-foreground">
                {fmtMs(n.p95_ms)}
              </TD>
              <TD className="tabular text-right font-mono text-[0.8rem]">{fmtMs(n.max_ms)}</TD>
              <TD className="tabular text-right font-mono text-[0.8rem] text-muted-foreground">
                {fmtMs(n.total_ms)}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </div>
  )
}

/**
 * The honest empty state — no runs have been recorded in the per-process window
 * yet. We show the real `source` + `window_capacity` provenance and a clear call
 * to run a query, NOT fabricated zeros dressed as measurements.
 */
function LatencyEmpty({ data }: { data: LatencyResponse }): ReactElement {
  return (
    <Card>
      <CardBody>
        <div className="flex flex-col items-center gap-3 py-12 text-center">
          <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-surface-2 text-muted-foreground">
            <Timer className="size-6" />
          </div>
          <p className="text-base font-medium text-foreground">No runs recorded yet</p>
          <p className="max-w-md text-sm text-muted-foreground">
            The latency window is per-process and resets on restart. Run a query to populate the
            window — percentiles appear here as soon as the first run completes.
          </p>
          <div className="mt-1 flex flex-wrap items-center justify-center gap-2">
            <Badge tone="neutral" className="font-mono">
              source · {data.source}
            </Badge>
            <Badge tone="neutral" className="font-mono">
              window capacity · {data.window_capacity ?? '—'}
            </Badge>
          </div>
        </div>
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

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
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
  }, [token, hydrated])


  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">latency · p50 · p95</p>
        <h1 className="t-hero text-foreground">Latency</h1>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : data == null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading latency…
            </div>
          </CardBody>
        </Card>
      ) : data.empty ? (
        <LatencyEmpty data={data} />
      ) : (
        <>
          {/* ── Run-latency summary tiles ─────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard label="Run p50" value={fmtMs(data.run_p50_ms)} icon={Gauge} tone="graph" />
            <StatCard label="Run p95" value={fmtMs(data.run_p95_ms)} icon={TrendingUp} tone="ml" />
            <StatCard label="Run max" value={fmtMs(data.run_max_ms)} icon={Activity} tone="risk" />
            <StatCard label="Runs recorded" value={String(data.run_count)} icon={Hash} tone="neutral" />
            <StatCard
              label="Slowest node"
              value={data.slowest_node ?? '—'}
              icon={Timer}
              tone="block"
            />
          </div>

          {/* ── Per-node p95 bars ─────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis · /latency"
              title="Per-node p95"
              description="Tail latency by graph node — each bar scaled to the slowest node's p95."
              actions={
                data ? (
                  <Badge tone="neutral" className="gap-1.5 font-mono">
                    {data.source}
                  </Badge>
                ) : null
              }
            />
            <CardBody className="pt-0">
              <NodeP95Bars nodes={data.per_node} />
            </CardBody>
          </Card>

          {/* ── Per-node table ────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis · /latency"
              title="Per-node breakdown"
              description="One row per node — count, p50, p95, max and total, from real samples."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  {data.per_node.length} nodes
                </Badge>
              }
            />
            <CardBody className="pt-0">
              <NodeTable nodes={data.per_node} />
            </CardBody>
          </Card>

          {/* ── Honest provenance note ────────────────────────────────────────── */}
          <p className="text-[0.72rem] leading-snug text-muted-foreground">
            Source <span className="font-mono text-foreground">{data.source}</span> · window capacity{' '}
            <span className="font-mono text-foreground">{data.window_capacity ?? '—'}</span>. The
            window is a per-process rolling buffer that resets on restart.
          </p>
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
