'use client'

import { Check, Loader2, PiggyBank, TriangleAlert } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getSavings } from '@/lib/api/client'
import { BarChart } from '@/components/charts/BarChart'
import { formatUsd } from '@/components/dashboard/roi'
import { KpiHero } from '@/components/shared/KpiHero'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { SIGNALS } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { SavingsResponse } from '@/lib/api/types'

import { breakdownData, reconcile, savedFraction } from './savingsCalc'

/**
 * Client — Savings.
 *
 * The value story, told with its derivation: a money hero (saved USD + % of
 * baseline), the baseline-vs-actual split, and the **breakdown by source**
 * (small-model routing / semantic cache / trimming) so the headline is
 * reconciled against its parts rather than asserted. Pure math lives in the
 * recharts-free `savingsCalc.ts` (unit-tested); this file fetches and renders.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: SavingsResponse }

/** Whole-dollar USD for the headline figures. */
function usd0(n: number): string {
  return formatUsd(n, 0)
}

/** A note is a demo sample when it says so — kept honest, never hidden. */
function isSample(note: string): boolean {
  return /sample/i.test(note)
}

/** Format an ISO timestamp for the "computed" caption. */
function formatWhen(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export function SavingsView({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getSavings(token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch(
        (e: unknown) =>
          alive &&
          setLoad({
            status: 'error',
            message: e instanceof Error ? e.message : 'Failed to load savings',
          }),
      )
    return () => {
      alive = false
    }
  }, [token])

  if (load.status === 'loading') {
    return (
      <Card>
        <CardContent className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading savings…
        </CardContent>
      </Card>
    )
  }
  if (load.status === 'error') {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-block-ink">
          Could not load savings. {load.message}
        </CardContent>
      </Card>
    )
  }

  const d = load.data
  const savedPct = Math.round((d.saved_pct || savedFraction(d.baseline_cost_usd, d.actual_cost_usd)) * 100)
  const rows = breakdownData(d.breakdown)
  const rec = reconcile(d)
  const sample = isSample(d.note)
  const bars = rows.map((r) => ({ source: r.source, saved: r.saved }))

  return (
    <div className="flex flex-col gap-4">
      {/* Intro + honest provenance. */}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <PiggyBank className="size-4 shrink-0 text-ok" />
          <p className="t-body max-w-2xl text-muted-foreground">
            What the same workload would have cost on the frontier model versus what it actually
            cost — with every dollar of the gap traced to its source.
          </p>
          <InfoTip label="Why this matters">
            Why this matters: savings are shown with their source, not as a bare number. The headline
            is reconciled against an itemised breakdown — routing, caching and trimming — so you can
            see where the money comes from.
          </InfoTip>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant="secondary">value</Badge>
          {sample && (
            <span className="eyebrow rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.58rem] text-muted-foreground">
              sample
            </span>
          )}
        </div>
      </div>

      {/* Money hero + baseline-vs-actual split. */}
      <div className="grid grid-cols-12 gap-4">
        <KpiHero
          className="col-span-12 lg:col-span-5"
          label="Saved vs frontier"
          value={d.saved_usd}
          format={usd0}
          delta={{ value: savedPct, direction: 'up', tone: 'good', suffix: '% of baseline' }}
          signal="ok"
          sample={sample}
          info="Baseline is the same workload priced on the frontier model; actual is what this stack spent. The gap is the saving."
        />
        <BaselineSplit
          className="col-span-12 lg:col-span-7"
          baseline={d.baseline_cost_usd}
          actual={d.actual_cost_usd}
          saved={d.saved_usd}
        />
      </div>

      {/* Breakdown by source — the derivation. */}
      <Card>
        <CardContent className="pt-5">
          <div className="mb-3 flex flex-wrap items-center gap-2">
            <span className="t-title text-foreground">Where the savings come from</span>
            <InfoTip label="About the breakdown">
              Each source&apos;s contribution to the headline saving. The bars and the rows below
              share the same figures; together they add up to the total.
            </InfoTip>
            <ReconcileChip reconciles={rec.reconciles} delta={rec.deltaUsd} />
          </div>

          {bars.length > 0 ? (
            <BarChart
              data={bars}
              index="source"
              category="saved"
              color="ok"
              valueFormatter={(v) => formatUsd(v, 0)}
              height={200}
            />
          ) : (
            <div className="flex h-[200px] items-center justify-center text-sm text-muted-foreground">
              No savings breakdown recorded yet.
            </div>
          )}

          <ul className="mt-4 flex flex-col divide-y divide-border/60">
            {rows.map((row) => (
              <li key={row.source} className="flex flex-col gap-1.5 py-3 first:pt-0 last:pb-0">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="t-label text-foreground">{row.source}</span>
                  <span className="tabular ml-auto font-mono text-sm font-medium text-ok-ink">
                    {formatUsd(row.saved, 0)}
                  </span>
                  <span className="tabular font-mono text-[0.68rem] text-muted-foreground">
                    {Math.round(row.share * 100)}%
                  </span>
                </div>
                <ShareBar share={row.share} color={row.color} />
                <p className="t-body text-muted-foreground">{row.explanation}</p>
              </li>
            ))}
          </ul>
        </CardContent>
      </Card>

      <p className="font-mono text-[0.68rem] leading-relaxed text-muted-foreground">
        {d.note} · Computed {formatWhen(d.generated_at)}.
      </p>
    </div>
  )
}

/** The baseline-vs-actual split as two figures over a single proportion bar. */
function BaselineSplit({
  baseline,
  actual,
  saved,
  className,
}: {
  baseline: number
  actual: number
  saved: number
  className?: string
}): ReactElement {
  const savedFrac = savedFraction(baseline, actual)
  const actualPct = Math.max(0, Math.min(100, 100 - savedFrac * 100))
  const savedPct = 100 - actualPct
  return (
    <Card className={cn('gap-0 p-6', className)}>
      <div className="flex items-center gap-2">
        <span className="eyebrow">Baseline vs actual</span>
        <InfoTip label="About baseline vs actual">
          The full bar is the frontier-model baseline. The filled portion is what this stack actually
          spent; the remainder is the saving.
        </InfoTip>
      </div>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-4">
        <Figure label="Frontier baseline" value={usd0(baseline)} />
        <Figure label="Actual spend" value={usd0(actual)} tone="foreground" />
        <Figure label="Saved" value={usd0(saved)} tone="ok" />
      </div>

      {/* Proportion bar: actual (spent) + saved, together = baseline. */}
      <div
        className="mt-5 flex h-3 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`Actual spend is ${actualPct.toFixed(0)}% of the frontier baseline; ${savedPct.toFixed(0)}% saved.`}
      >
        <span className="h-full bg-blue-100/70" style={{ width: `${actualPct}%` }} />
        <span className="h-full bg-ok" style={{ width: `${savedPct}%` }} />
      </div>
      <div className="mt-2 flex items-center gap-4 text-[0.72rem] text-muted-foreground">
        <LegendDot color="ml" label={`Actual · ${actualPct.toFixed(0)}%`} />
        <LegendDot color="ok" label={`Saved · ${savedPct.toFixed(0)}%`} />
      </div>
    </Card>
  )
}

/** One labelled headline figure. */
function Figure({
  label,
  value,
  tone = 'muted',
}: {
  label: string
  value: string
  tone?: 'muted' | 'foreground' | 'ok'
}): ReactElement {
  const toneClass =
    tone === 'ok' ? 'text-ok-ink' : tone === 'foreground' ? 'text-foreground' : 'text-muted-foreground'
  return (
    <div>
      <p className="eyebrow mb-1">{label}</p>
      <p className={cn('tabular font-display text-2xl leading-none font-semibold', toneClass)}>
        {value}
      </p>
    </div>
  )
}

/** A small legend dot + label keyed to a signal. */
function LegendDot({ color, label }: { color: 'ml' | 'ok'; label: string }): ReactElement {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2.5 rounded-full" style={{ background: SIGNALS[color].hex }} aria-hidden />
      {label}
    </span>
  )
}

/** A thin share bar under a breakdown row. */
function ShareBar({ share, color }: { share: number; color: keyof typeof SIGNALS }): ReactElement {
  const pct = Math.max(0, Math.min(100, share * 100))
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
      <span className="block h-full rounded-full" style={{ width: `${pct}%`, background: SIGNALS[color].hex }} />
    </div>
  )
}

/** Confirms (or flags) that the breakdown adds up to the headline saving. */
function ReconcileChip({ reconciles, delta }: { reconciles: boolean; delta: number }): ReactElement {
  if (reconciles) {
    return (
      <span className="ml-auto inline-flex items-center gap-1 rounded-md border border-ok/60 bg-ok/15 px-2 py-0.5 text-[0.68rem] font-medium text-ok-ink">
        <Check className="size-3" aria-hidden /> Breakdown adds up to the total
      </span>
    )
  }
  return (
    <span className="ml-auto inline-flex items-center gap-1 rounded-md border border-risk/60 bg-risk/15 px-2 py-0.5 text-[0.68rem] font-medium text-risk-ink">
      <TriangleAlert className="size-3" aria-hidden /> {formatUsd(Math.abs(delta), 0)} unattributed
    </span>
  )
}

/** Client entry for the Savings section — gated on a reachable backend. */
export function SavingsMount(): ReactElement {
  // `GET /savings` is tenant-scoped: hand the view the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <div className="space-y-4">
          <div>
            <p className="eyebrow mb-1">baseline vs actual</p>
            <h1 className="t-hero text-foreground">Savings</h1>
          </div>
          <SavingsView token={session?.token ?? null} />
        </div>
      </TooltipProvider>
    </BackendGate>
  )
}
