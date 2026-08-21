'use client'

import { CircleCheck } from 'lucide-react'
import type { ReactElement } from 'react'
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
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { RedteamRun } from '@/lib/api/redteam'

import { pct } from './redteamReport'

/**
 * The x-axis tick, in the reader's own locale — one formatter for the panel
 * rather than a fresh `Intl` instance per row.
 */
const TICK_FORMAT = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric' })

/** One run placed on the series. Both rates come off the stored run, unmodified. */
interface RunPoint {
  t: string
  sort: number
  blockRate: number
  falsePositiveRate: number
}

/** What the history can and cannot be drawn as. */
interface Plotted {
  points: RunPoint[]
  /** Runs whose `startedAt` was never written, so they have no place on an axis. */
  undated: number
  /** The floor the newest run was judged against. */
  floor: number | null
  /** The false-positive ceiling the newest run was judged against. */
  ceiling: number | null
}

/**
 * Place the stored runs on a time axis, oldest first.
 *
 * A run with no `startedAt` is **counted, not placed**: dropping it silently would
 * make the series claim to be every run, and inventing a position for it would put
 * a measured block rate at a time nobody recorded.
 */
export function plotRuns(history: readonly RedteamRun[]): Plotted {
  const points: RunPoint[] = []
  let undated = 0
  for (const run of history) {
    if (run.startedAt == null) {
      undated += 1
      continue
    }
    const at = new Date(run.startedAt)
    if (Number.isNaN(at.getTime())) {
      undated += 1
      continue
    }
    points.push({
      t: TICK_FORMAT.format(at),
      sort: at.getTime(),
      blockRate: run.blockRate,
      falsePositiveRate: run.falsePositiveRate,
    })
  }
  points.sort((a, b) => a.sort - b.sort)
  const newest = history[0] ?? null
  return {
    points,
    undated,
    floor: newest ? newest.minBlockRate : null,
    ceiling: newest ? newest.maxFalsePositiveRate : null,
  }
}

/** One rate on its own axis, with the threshold it is judged against drawn on it. */
function RatePanel({
  title,
  name,
  dataKey,
  points,
  domain,
  threshold,
  thresholdLabel,
}: {
  title: string
  name: string
  dataKey: 'blockRate' | 'falsePositiveRate'
  points: RunPoint[]
  domain: [number, number]
  threshold: number | null
  thresholdLabel: string
}): ReactElement {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-lg border border-border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
        <span className="eyebrow">{title}</span>
        {threshold == null ? null : (
          <Badge tone="neutral" className="font-mono">
            {thresholdLabel} {pct(threshold)}
          </Badge>
        )}
      </div>
      <div className="min-w-0">
        <ResponsiveContainer width="100%" height={168}>
          {/* `left: 0`, not the usual negative pull. The gutter has to hold
              `100%`, and a negative left margin shifts the axis text out of the
              SVG viewport — at 390 it clipped the leading digit and rendered
              `00%`, which reads as a broken chart rather than a full one. */}
          <LineChart data={points} margin={{ top: 6, right: 6, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" vertical={false} />
            <XAxis
              dataKey="t"
              tick={{
                fill: 'var(--muted-foreground)',
                fontSize: 10,
                fontFamily: 'var(--font-mono)',
              }}
              axisLine={false}
              tickLine={false}
              minTickGap={16}
            />
            <YAxis
              domain={domain}
              tick={{
                fill: 'var(--muted-foreground)',
                fontSize: 10,
                fontFamily: 'var(--font-mono)',
              }}
              axisLine={false}
              tickLine={false}
              width={44}
              tickFormatter={(v: number) => pct(v)}
            />
            <Tooltip
              cursor={{ stroke: 'var(--border)' }}
              content={<ChartTooltip valueFormatter={(v) => pct(v)} />}
            />
            {threshold == null ? null : (
              <ReferenceLine
                y={threshold}
                stroke="var(--muted-foreground)"
                strokeDasharray="4 4"
                ifOverflow="extendDomain"
              />
            )}
            <Line
              type="monotone"
              dataKey={dataKey}
              name={name}
              stroke={chartHex('graph')}
              strokeWidth={2}
              dot={{ r: 2.5, fill: chartHex('graph'), stroke: 'var(--card)', strokeWidth: 1 }}
              activeDot={{ r: 4, stroke: 'var(--card)', strokeWidth: 2 }}
              isAnimationActive={false}
            />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

/** Whether a series is zero on every stored run — one fact, not a plot. */
function flatZero(points: readonly RunPoint[], key: 'blockRate' | 'falsePositiveRate'): boolean {
  return points.every((p) => p[key] === 0)
}

/**
 * **Block rate over runs** — the one genuine time-series this screen owns.
 *
 * `getRedteamHistory` returns every stored run with its own `startedAt`,
 * `blockRate` and `falsePositiveRate`, and the empty state below has always
 * promised "a trend rather than a snapshot" while nothing drew one. This is that
 * promise, kept.
 *
 * Two panels rather than two lines: a block rate and a false-positive rate share
 * a 0–1 range and measure different things, so one axis would invite a comparison
 * that means nothing (the same reason `EvalTrend` is four small multiples). Each
 * carries the threshold its run was judged against as a dashed rule.
 *
 * **A single run is stated, not drawn**, and so is a rate that is zero on every
 * run. One point is not a trend; a flat line on the axis floor is one fact
 * wearing a plot, and its axis ticks round to nonsense (`5% · 3% · 2%`) because
 * the domain is a rounding error wide. Either way the panel is dropped and the
 * fact keeps its chip, which is what stops a "no false positives ever" history —
 * the good outcome, and the common one — from occupying half this card with
 * whitespace.
 */
export function BlockRateTrend({ history }: { history: RedteamRun[] }): ReactElement | null {
  const { points, undated, floor, ceiling } = plotRuns(history)
  if (points.length === 0 && undated === 0) return null

  const lowest = points.reduce((m, p) => Math.min(m, p.blockRate), 1)
  const blockFloor = Math.max(0, Math.min(lowest, floor ?? 1) - 0.08)
  const worstFp = points.reduce((m, p) => Math.max(m, p.falsePositiveRate), 0)
  const fpTop = Math.min(1, Math.max(worstFp, ceiling ?? 0) + 0.05) || 0.1

  const latest = points[points.length - 1] ?? null

  // A series that never left zero is stated as a chip, so a single surviving
  // panel takes the full width instead of sitting beside an empty box.
  const panels = [
    {
      title: 'Attacks blocked',
      name: 'Block rate',
      dataKey: 'blockRate' as const,
      domain: [blockFloor, 1] as [number, number],
      threshold: floor,
      thresholdLabel: 'floor',
    },
    {
      title: 'Benign controls wrongly blocked',
      name: 'False-positive rate',
      dataKey: 'falsePositiveRate' as const,
      domain: [0, fpTop] as [number, number],
      threshold: ceiling,
      thresholdLabel: 'ceiling',
    },
  ]
  const drawn = panels.filter((p) => !flatZero(points, p.dataKey))
  const stated = panels.filter((p) => flatZero(points, p.dataKey))

  return (
    <Card>
      <CardHeader
        eyebrow="GET /redteam/runs"
        title="Block rate over runs"
        actions={
          <Badge tone="neutral" className="font-mono">
            {points.length} plotted
          </Badge>
        }
      />
      <CardBody className="flex flex-col gap-4">
        {points.length < 2 ? (
          /* Below two points there is no trend, so this states the counts compactly
             rather than drawing a plot with one dot in it. */
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
            <span className="flex items-baseline gap-2">
              <span className="eyebrow">block rate</span>
              <Figure size="stat" className="text-foreground">
                {latest ? pct(latest.blockRate) : 'no dated run'}
              </Figure>
            </span>
            <span className="flex items-baseline gap-2">
              <span className="eyebrow">false positives</span>
              <Figure size="stat" className="text-foreground">
                {latest ? pct(latest.falsePositiveRate) : '—'}
              </Figure>
            </span>
            <span className="text-xs text-muted-foreground">
              One run recorded. A second makes this a trend.
            </span>
          </div>
        ) : (
          <>
            <div
              className={`grid min-w-0 gap-3 ${drawn.length > 1 ? 'sm:grid-cols-2' : ''}`}
            >
              {drawn.map((panel) => (
                <RatePanel key={panel.dataKey} {...panel} points={points} />
              ))}
            </div>
            {stated.length === 0 ? null : (
              <p className="flex flex-wrap items-center gap-2">
                {stated.map((panel) => (
                  <Badge key={panel.dataKey} tone="ok" className="gap-1.5">
                    <CircleCheck className="size-3 shrink-0" aria-hidden />
                    {panel.title.toLowerCase()}: 0% on all {points.length} runs
                  </Badge>
                ))}
              </p>
            )}
          </>
        )}
        <Receipt
          origin="redteam runs · blockRate and falsePositiveRate by startedAt"
          detail={
            undated === 0
              ? `${points.length} runs plotted · dashed rule is the stored threshold`
              : `${points.length} plotted · ${undated} carry no start time, so they are counted and not placed`
          }
        />
      </CardBody>
    </Card>
  )
}
