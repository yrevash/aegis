'use client'

import { CircleCheck, CircleX } from 'lucide-react'
import { useMemo, type ReactElement } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { ChartTooltip } from '@/components/charts/ChartTooltip'
import { chartHex } from '@/components/charts/palette'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { OpsEvalRow } from '@/lib/api/ops'
import type { OpsParamsResponse } from '@/lib/api/platform'

/**
 * The x-axis tick label, in the reader's own locale — one formatter for the whole
 * screen rather than a fresh `Intl` instance per row of a 200-row payload.
 */
const TICK_FORMAT = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

/** The metric families the harness scores, in the order the loop reads them. */
const KNOWN: { key: string; label: string }[] = [
  { key: 'answer', label: 'Answer' },
  { key: 'retrieval', label: 'Retrieval' },
  { key: 'tool', label: 'Tool' },
  { key: 'guardrail', label: 'Guardrail' },
]

/** One metric family's own series — the unit of a small multiple. */
interface Series {
  key: string
  label: string
  points: { t: string; sort: number; score: number }[]
  passed: number
}

/**
 * Split the flat eval rows into one series per metric family.
 *
 * The four families used to be four lines on one axis. Two things were wrong with
 * that and neither is taste. `guardrail` was drawn in `--ok`, and DESIGN.md §2
 * reserves the status hues so that a green mark always means a verdict — a green
 * *series* spends that. And the other three resolved to adjacent steps of one blue
 * ramp, which measures ΔE 6.4 against a floor of 15: two of those lines are one
 * line to anyone reading the chart rather than the legend. One hue does not hold
 * four categories, so this is the other answer DESIGN.md gives — small multiples.
 *
 * They also compare better this way. Answer relevancy and tool correctness are
 * different measurements that happen to share a 0–1 range; putting them on one
 * axis invites a comparison that means nothing.
 */
function seriesByMetric(rows: OpsEvalRow[]): Series[] {
  const grouped = new Map<string, Series>()
  for (const row of rows) {
    if (row.ts == null) continue
    const existing =
      grouped.get(row.metric) ??
      ({
        key: row.metric,
        label: KNOWN.find((m) => m.key === row.metric)?.label ?? row.metric,
        points: [],
        passed: 0,
      } satisfies Series)
    existing.points.push({
      t: TICK_FORMAT.format(new Date(row.ts)),
      sort: new Date(row.ts).getTime(),
      score: row.score,
    })
    if (row.passed) existing.passed += 1
    grouped.set(row.metric, existing)
  }
  const order = new Map(KNOWN.map((m, i) => [m.key, i]))
  return [...grouped.values()]
    .map((s) => ({ ...s, points: [...s.points].sort((a, b) => a.sort - b.sort) }))
    .sort(
      (a, b) =>
        (order.get(a.key) ?? Number.MAX_SAFE_INTEGER) -
          (order.get(b.key) ?? Number.MAX_SAFE_INTEGER) || a.key.localeCompare(b.key),
    )
}

/**
 * One metric family, drawn on its own axis.
 *
 * The dashed rule is the bar the next draft has to clear: the gate promotes only
 * when a draft beats the live prompt by `eval_margin`, and the live prompt's score
 * is the most recent point on this line. Both numbers are measured — nothing about
 * the line is inferred.
 */
function MetricPanel({ series, margin }: { series: Series; margin: number | null }): ReactElement {
  const latest = series.points[series.points.length - 1]?.score ?? null
  const bar = latest != null && margin != null ? Math.min(1, latest + margin) : null
  const low = series.points.reduce((m, p) => Math.min(m, p.score), 1)
  const floor = Math.max(0, Math.min(low, bar ?? 1) - 0.05)
  const total = series.points.length
  const clean = series.passed === total

  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="eyebrow">{series.label}</span>
        <Badge tone={clean ? 'ok' : 'block'} className="gap-1.5">
          {clean ? (
            <CircleCheck className="size-3 shrink-0" aria-hidden />
          ) : (
            <CircleX className="size-3 shrink-0" aria-hidden />
          )}
          {series.passed}/{total} passed
        </Badge>
      </div>
      <Figure size="stat" className="text-foreground">
        {latest == null ? 'not scored' : latest.toFixed(3)}
      </Figure>
      {bar == null ? null : (
        <span className="eyebrow">bar to clear {bar.toFixed(3)}</span>
      )}
      <div className="mt-1 min-w-0">
        <ResponsiveContainer width="100%" height={132}>
          <LineChart data={series.points} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="t"
              tick={{ fill: 'var(--muted-foreground)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
              axisLine={false}
              tickLine={false}
              minTickGap={16}
            />
            <YAxis
              domain={[floor, 1]}
              tick={{ fill: 'var(--muted-foreground)', fontSize: 10, fontFamily: 'var(--font-mono)' }}
              axisLine={false}
              tickLine={false}
              width={44}
              tickFormatter={(v: number) => v.toFixed(2)}
            />
            <Tooltip
              cursor={{ stroke: 'var(--border)' }}
              content={<ChartTooltip valueFormatter={(v) => v.toFixed(3)} />}
            />
            {bar == null ? null : (
              <ReferenceLine
                y={bar}
                stroke="var(--muted-foreground)"
                strokeDasharray="4 4"
                ifOverflow="extendDomain"
              />
            )}
            <Line
              type="monotone"
              dataKey="score"
              name={series.label}
              stroke={chartHex('graph')}
              strokeWidth={2}
              dot={false}
              activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

interface Props {
  rows: OpsEvalRow[]
  loading: boolean
  error: string | null
  /** The gate's tunable knobs; `eval_margin` is the bar drawn on each panel. */
  params: OpsParamsResponse | null
}

/**
 * The **Quality trend** hero — the one real time-series in this portal
 * (`OpsEvalRow.score` by `ts`), drawn as one small multiple per metric family.
 */
export function EvalTrend({ rows, loading, error, params }: Props): ReactElement {
  const series = useMemo(() => seriesByMetric(rows), [rows])
  const margin = params?.eval_margin ?? null
  const plotted = series.reduce((sum, s) => sum + s.points.length, 0)

  return (
    <Card className="flex min-w-0 flex-col">
      <CardHeader
        eyebrow="GET /ops/evals"
        title="Quality trend"
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="neutral" className="font-mono">
              {plotted} scores · {series.length} metrics
            </Badge>
            <InfoTip label="What the dashed rule is">
              The bar a draft must clear to auto-ship: the live prompt&rsquo;s latest score plus
              the gate&rsquo;s eval margin.
            </InfoTip>
          </div>
        }
      />
      <CardBody className="@container flex min-h-0 flex-1 flex-col gap-4">
        {error ? (
          <ErrorState error={error} />
        ) : loading ? (
          <LoadingState rows={4} label="Loading quality history…" />
        ) : series.length === 0 ? (
          /* "A chart with nothing to draw yet" — which is exactly the state. */
          <SceneState name="noChart" size="md">
            <Absence
              className="text-left"
              figure="Quality trend"
              why={
                rows.length === 0
                  ? 'The harness has not graded a run yet.'
                  : 'The stored scores carry no timestamp, so they cannot be placed on a series.'
              }
              needed="A graded run — every score it writes lands here."
            />
          </SceneState>
        ) : (
          <div className="grid min-w-0 gap-3 @md:grid-cols-2">
            {series.map((s) => (
              <MetricPanel key={s.key} series={s} margin={margin} />
            ))}
          </div>
        )}
        <Receipt
          className="mt-auto"
          origin="ops.evals · score by ts"
          detail={
            margin == null ? 'eval margin not read' : `bar = latest + eval_margin ${margin.toFixed(3)}`
          }
        />
      </CardBody>
    </Card>
  )
}
