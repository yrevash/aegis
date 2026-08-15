'use client'

import { Loader2 } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { CountUp } from '@/components/shared'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { OpsEvalRow } from '@/lib/api/ops'

/** The four eval metric families, each mapped to a trust hue. */
const METRICS: { key: string; label: string; signal: Signal }[] = [
  { key: 'answer', label: 'Answer', signal: 'agent' },
  { key: 'retrieval', label: 'Retrieval', signal: 'graph' },
  { key: 'tool', label: 'Tool', signal: 'ml' },
  { key: 'guardrail', label: 'Guardrail', signal: 'ok' },
]

interface Point {
  t: string
  sort: number
  [metric: string]: number | string
}

/** Pivot the flat eval rows into one point per timestamp with a key per metric. */
function pivot(rows: OpsEvalRow[]): Point[] {
  const byTs = new Map<string, Point>()
  for (const r of rows) {
    if (!r.ts) continue
    const sort = new Date(r.ts).getTime()
    const label = new Date(r.ts).toLocaleDateString([], { month: 'short', day: 'numeric' })
    const point = byTs.get(r.ts) ?? { t: label, sort }
    point[r.metric] = r.score
    byTs.set(r.ts, point)
  }
  return [...byTs.values()].sort((a, b) => a.sort - b.sort)
}

/** The latest score for a metric over the window, or null. */
function latestScore(data: Point[], key: string): number | null {
  for (let i = data.length - 1; i >= 0; i -= 1) {
    const v = data[i][key]
    if (typeof v === 'number') return v
  }
  return null
}

interface Props {
  rows: OpsEvalRow[]
  loading: boolean
  error: string | null
}

/**
 * The **Quality trend** hero — answer / retrieval / tool / guardrail scores over
 * time (`GET /ops/evals`). The four tiles read the latest score per family
 * (click to toggle its line); the multi-line chart is the signal the loop
 * watches to decide when to propose a new prompt.
 */
export function EvalTrend({ rows, loading, error }: Props): ReactElement {
  const [hidden, setHidden] = useState<Set<string>>(new Set())
  const data = useMemo(() => pivot(rows), [rows])

  return (
    <Card>
      <CardHeader
        eyebrow="GET /ops/evals"
        title="Quality trend"
        actions={
          <span className="eyebrow flex items-center gap-1.5">
            <span
              className="animate-pip size-1.5 rounded-full"
              style={{ background: 'var(--agent-ink)', ['--pip-color' as string]: 'var(--agent-ink)' }}
            />
            live
          </span>
        }
      />
      <CardBody className="space-y-4">
        {error ? (
          <p className="py-10 text-center text-sm text-danger">{error}</p>
        ) : loading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading quality history…
          </div>
        ) : (
          <>
            {/* Latest score per metric — click to toggle its line. */}
            <div className="grid grid-cols-2 gap-2.5 lg:grid-cols-4">
              {METRICS.map((m) => {
                const score = latestScore(data, m.key)
                const off = hidden.has(m.key)
                const token = SIGNALS[m.signal]
                return (
                  <button
                    key={m.key}
                    type="button"
                    aria-pressed={!off}
                    onClick={() =>
                      setHidden((prev) => {
                        const next = new Set(prev)
                        if (next.has(m.key)) next.delete(m.key)
                        else next.add(m.key)
                        return next
                      })
                    }
                    className={cn(
                      'rounded-xl border p-3 text-left transition-all',
                      off ? 'border-border/60 bg-surface-2/30 opacity-55' : 'border-border bg-card',
                    )}
                  >
                    <div className="flex items-center gap-1.5">
                      <span className="size-2 rounded-full" style={{ background: token.hex }} />
                      <span className="eyebrow text-[0.58rem]">{m.label}</span>
                    </div>
                    {score != null ? (
                      <CountUp
                        value={score}
                        format={(n) => n.toFixed(3)}
                        className="t-metric mt-1 block text-foreground"
                      />
                    ) : (
                      <p className="t-metric mt-1 text-muted-foreground">—</p>
                    )}
                  </button>
                )
              })}
            </div>

            {data.length === 0 ? (
              <div className="flex h-[240px] items-center justify-center text-sm text-muted-foreground">
                No scores yet — they appear here once the harness grades a run.
              </div>
            ) : (
              <div className="overflow-x-auto">
                <ResponsiveContainer width="100%" height={280} minWidth={320}>
                  <LineChart data={data} margin={{ top: 8, right: 8, left: -16, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
                    <XAxis
                      dataKey="t"
                      tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                      axisLine={false}
                      tickLine={false}
                    />
                    <YAxis
                      domain={[0.5, 1]}
                      tick={{ fill: 'var(--muted-foreground)', fontSize: 11, fontFamily: 'var(--font-mono)' }}
                      axisLine={false}
                      tickLine={false}
                      width={40}
                    />
                    <Tooltip
                      cursor={{ stroke: 'var(--border)' }}
                      content={<ChartTooltip valueFormatter={(v) => v.toFixed(3)} />}
                    />
                    {METRICS.filter((m) => !hidden.has(m.key)).map((m) => (
                      <Line
                        key={m.key}
                        type="monotone"
                        dataKey={m.key}
                        name={m.label}
                        stroke={SIGNALS[m.signal].hex}
                        strokeWidth={2}
                        dot={false}
                        activeDot={{ r: 4, stroke: 'var(--background)' }}
                        isAnimationActive
                        animationDuration={700}
                      />
                    ))}
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}
