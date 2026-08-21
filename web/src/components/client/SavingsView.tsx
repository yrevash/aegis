'use client'

import { Check, Loader2, TriangleAlert } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getSavings } from '@/lib/api/client'
import { chartHex, rampHex } from '@/components/charts/palette'
import { DonutChart } from '@/components/charts/DonutChart'
import { formatUsd, formatUsdAuto } from '@/components/dashboard/roi'
import { SceneState } from '@/components/illustration/Scene'
import { KpiHero } from '@/components/shared/KpiHero'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { BackendGate } from '@/components/shared/BackendGate'
import { SIGNALS } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { SavingsResponse } from '@/lib/api/types'

import { breakdownData, reconcile, savedFraction, type BreakdownDatum } from './savingsCalc'

/**
 * Client — Savings.
 *
 * The value story, told with its derivation: a money hero (saved USD + % of
 * baseline), the baseline-vs-actual split, and the **breakdown by source** so the
 * headline is reconciled against its parts rather than asserted. Pure math lives
 * in the recharts-free `savingsCalc.ts` (unit-tested); this file fetches and renders.
 *
 * **Why the breakdown chart is conditional.** `GET /savings` returns one metered
 * source (small-model routing, taken exactly off the gateway ledger) plus cache
 * sources reported at **$0 — not because they save nothing, but because a cache
 * hit bypasses the model and never enters the ledger this figure is built from**.
 * Charting `saved_usd` across all rows would draw "not priced here" as "saved
 * nothing", which is the one claim the endpoint's own note refuses to make. So the
 * composition chart is drawn over the *priced* rows only, and only when there are
 * at least two of them; the unpriced rows are stated as an `Absence` beside the
 * table, each carrying its own reason one hover away.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: SavingsResponse }

/** Module-level formatters — never re-created per row (DESIGN.md §3). */
const WHEN = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const PCT = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 0 })

/** A note is a demo sample when it says so — kept honest, never hidden. */
function isSample(note: string): boolean {
  return /sample/i.test(note)
}

/** Format an ISO timestamp for the provenance line. */
function formatWhen(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : WHEN.format(d)
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
        <CardBody
          role="status"
          aria-live="polite"
          className="flex items-center gap-2 py-10 text-sm text-muted-foreground"
        >
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading savings…
        </CardBody>
      </Card>
    )
  }
  if (load.status === 'error') {
    return (
      <Card>
        <CardBody role="status" aria-live="polite" className="py-10 text-sm text-block-ink">
          Could not load savings. {load.message}
        </CardBody>
      </Card>
    )
  }

  const d = load.data
  const savedPct = Math.round(
    (d.saved_pct || savedFraction(d.baseline_cost_usd, d.actual_cost_usd)) * 100,
  )
  const rows = breakdownData(d.breakdown)
  const priced = rows.filter((r) => r.saved > 0)
  const unpriced = rows.filter((r) => r.saved <= 0)
  const rec = reconcile(d)
  const sample = isSample(d.note)

  // A donut needs at least two slices to be a composition; one slice is a ring
  // that encodes nothing. Below that the card is not drawn at all, so the table
  // takes the full width rather than sitting beside dead space.
  const showShare = priced.length >= 2

  return (
    <div className="flex flex-col gap-4">
      {/* Money hero + baseline-vs-actual split. */}
      <div className="grid grid-cols-12 items-start gap-4">
        <KpiHero
          // `h-auto` overrides `KpiHero`'s own `h-full`. A percentage height in a
          // grid resolves against the *row*, which is definite, so `h-full` beat the
          // row's `items-start` and stretched this tile to the taller card beside it
          // — 120px of empty card under a two-line figure, which is the dead space
          // `items-start` was there to prevent.
          className="col-span-12 h-auto min-w-0 lg:col-span-5"
          label="Saved vs frontier"
          value={d.saved_usd}
          format={formatUsdAuto}
          delta={{ value: savedPct, direction: 'up', tone: 'good', suffix: '% of baseline' }}
          signal="ok"
          sample={sample}
          info="Baseline is the same workload priced on the frontier model; actual is what this stack spent. The gap is the saving."
        />
        <BaselineSplit
          className="col-span-12 min-w-0 lg:col-span-7"
          baseline={d.baseline_cost_usd}
          actual={d.actual_cost_usd}
          saved={d.saved_usd}
        />
      </div>

      {/* The derivation. */}
      {rows.length === 0 ? (
        <Card>
          <CardHeader eyebrow="usage ledger" title="Where the savings come from" />
          <CardBody>
            <SceneState name="cost" size="md">
              <Absence
                figure="Savings by source"
                why="The ledger has attributed no source to this tenant's spend yet."
                needed="One metered gateway call, attributed to the route that saved the money."
                className="mx-auto max-w-md text-left"
              />
            </SceneState>
          </CardBody>
        </Card>
      ) : (
        <div className="grid grid-cols-12 items-start gap-4">
          {showShare ? (
            <Card className="col-span-12 min-w-0 lg:col-span-5">
              <CardHeader eyebrow="priced sources" title="Share of the saving" />
              <CardBody>
                <DonutChart
                  data={priced.map((r, i) => ({
                    name: r.source,
                    value: r.saved,
                    color: 'graph',
                    hex: rampHex(i, priced.length),
                  }))}
                  centerLabel={formatUsdAuto(priced.reduce((sum, r) => sum + r.saved, 0))}
                  centerSub="itemised"
                  valueFormatter={formatUsdAuto}
                  height={200}
                />
              </CardBody>
            </Card>
          ) : unpriced.length > 0 ? (
            <Card className="col-span-12 min-w-0 lg:col-span-5">
              {/* The title names what the card actually holds. A share chart the
                  payload cannot support must not leave its heading behind. */}
              <CardHeader eyebrow="usage ledger" title="What this figure leaves out" />
              <CardBody>
                <SceneState name="cost" size="sm">
                  <Absence
                    figure={`${unpriced.length} of ${rows.length} sources at $0`}
                    why="Reported at zero rather than estimated — each row carries its own reason."
                    className="text-left"
                  />
                </SceneState>
              </CardBody>
            </Card>
          ) : null}

          <DataPanel
            className={cn('col-span-12 min-w-0', showShare || unpriced.length > 0 ? 'lg:col-span-7' : null)}
            eyebrow="usage ledger"
            title="Where the savings come from"
            actions={<ReconcileChip reconciles={rec.reconciles} delta={rec.deltaUsd} />}
            footer={
              <Receipt
                origin={`gateway usage ledger · computed ${formatWhen(d.generated_at)}`}
                detail={d.note}
              />
            }
          >
            <BreakdownTable rows={rows} />
          </DataPanel>
        </div>
      )}
    </div>
  )
}

/**
 * The breakdown as a table, not a stack of paragraphs.
 *
 * `row.explanation` used to be a full API paragraph rendered under every source —
 * three of them down one card, which is the wall DESIGN.md §9 names and the thing
 * the owner called out by name. It explains a *mechanism*, so §4 sends it to an
 * `InfoTip`: nothing is deleted, it is one hover from the source it describes, and
 * the figures are now scannable in a column. `SecurityView`'s `PostureRow` is the
 * precedent.
 */
function BreakdownTable({ rows }: { rows: BreakdownDatum[] }): ReactElement {
  return (
    <Table>
      <THead>
        <TH>Source</TH>
        <TH className="text-right">Saved</TH>
        <TH className="text-right">Share</TH>
      </THead>
      <TBody>
        {rows.map((row) => (
          <TR key={row.source}>
            <TD>
              <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium break-words text-foreground">
                {row.source}
                <InfoTip label={`How ${row.source} saves`}>{row.explanation}</InfoTip>
              </span>
            </TD>
            <TD className="text-right whitespace-nowrap">
              <Figure className={row.saved > 0 ? 'font-medium text-ok-ink' : 'text-muted-foreground'}>
                {formatUsd(row.saved, 2)}
              </Figure>
            </TD>
            <TD className="text-right whitespace-nowrap">
              <Figure className="text-muted-foreground">{PCT.format(row.share)}</Figure>
            </TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}

/** The baseline-vs-actual split as three figures over a single proportion bar. */
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
  // The legend dot and the bar segment it names must be the same paint. They were
  // not: the segment was `bg-blue-100/70` while the dot took `SIGNALS.ml.hex`
  // (`#1e40af`), so a pale band was keyed to a near-navy swatch.
  const actualHex = chartHex('ml')
  const savedHex = SIGNALS.ok.hex

  return (
    <Card className={cn('gap-0 p-6', className)}>
      <span className="eyebrow">Baseline vs actual</span>

      <div className="mt-4 flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <Stat label="Frontier baseline" value={formatUsdAuto(baseline)} />
        <Stat label="Actual spend" value={formatUsdAuto(actual)} tone="foreground" />
        <Stat label="Saved" value={formatUsdAuto(saved)} tone="ok" />
      </div>

      {/* Proportion bar: actual (spent) + saved, together = baseline. */}
      <div
        className="mt-5 flex h-3 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`Actual spend is ${actualPct.toFixed(0)}% of the frontier baseline; ${savedPct.toFixed(0)}% saved.`}
      >
        <span className="h-full" style={{ width: `${actualPct}%`, background: actualHex }} />
        <span className="h-full" style={{ width: `${savedPct}%`, background: savedHex }} />
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.72rem] text-muted-foreground">
        <LegendDot hex={actualHex} label={`Actual · ${actualPct.toFixed(0)}%`} />
        <LegendDot hex={savedHex} label={`Saved · ${savedPct.toFixed(0)}%`} />
      </div>
    </Card>
  )
}

/**
 * One labelled figure, in the tile anatomy DESIGN.md §3 specifies: label above the
 * value, 4px apart, 12px in muted ink. The numeral goes through the shared
 * `Figure` primitive — this screen used to define its own, which is how a console
 * ends up with two numeral treatments on one page.
 */
function Stat({
  label,
  value,
  tone = 'muted',
}: {
  label: string
  value: string
  tone?: 'muted' | 'foreground' | 'ok'
}): ReactElement {
  const toneClass =
    tone === 'ok'
      ? 'text-ok-ink'
      : tone === 'foreground'
        ? 'text-foreground'
        : 'text-muted-foreground'
  return (
    <div className="min-w-0">
      <p className="eyebrow mb-1">{label}</p>
      <Figure size="stat" className={toneClass}>
        {value}
      </Figure>
    </div>
  )
}

/** A small legend swatch keyed to the exact paint of the segment it names. */
function LegendDot({ hex, label }: { hex: string; label: string }): ReactElement {
  return (
    <span className="flex items-center gap-1.5">
      <span className="size-2.5 shrink-0 rounded-full" style={{ background: hex }} aria-hidden />
      {label}
    </span>
  )
}

/** Confirms (or flags) that the breakdown adds up to the headline saving. */
function ReconcileChip({ reconciles, delta }: { reconciles: boolean; delta: number }): ReactElement {
  if (reconciles) {
    return (
      <span className="inline-flex items-center gap-1 rounded-md border border-ok/60 bg-ok/15 px-2 py-0.5 text-[0.68rem] font-medium text-ok-ink">
        <Check className="size-3 shrink-0" aria-hidden /> Adds up to the total
      </span>
    )
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-md border border-risk/60 bg-risk/15 px-2 py-0.5 text-[0.68rem] font-medium text-risk-ink">
      <TriangleAlert className="size-3 shrink-0" aria-hidden />{' '}
      <Figure className="text-[0.68rem] leading-4">{formatUsdAuto(Math.abs(delta))}</Figure>{' '}
      unattributed
    </span>
  )
}

/** Client entry for the Savings section — gated on a reachable backend. */
export function SavingsMount(): ReactElement {
  // `GET /savings` is tenant-scoped: hand the view the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  return (
    <BackendGate>
      <div className="space-y-4">
        <PageHeader eyebrow="baseline vs actual" title="Savings" />
        {hydrated ? (
          <SavingsView token={session?.token ?? null} />
        ) : (
          // A `min-h-[420px]` dashed box for a restore that takes one tick stranded
          // most of a viewport behind a single word. The header paints first now,
          // and this is the same height as the view's own loading card.
          <Card>
            <CardBody
              role="status"
              aria-live="polite"
              className="flex items-center gap-2 py-10 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 animate-spin" aria-hidden /> Connecting…
            </CardBody>
          </Card>
        )}
      </div>
    </BackendGate>
  )
}
