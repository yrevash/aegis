'use client'

import { Loader2 } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getRiskMap } from '@/lib/api/client'
import { SceneState } from '@/components/illustration/Scene'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { PageHeader } from '@/components/primitives/PageHeader'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { BackendGate } from '@/components/shared/BackendGate'
import { SIGNALS } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { RiskMapResponse } from '@/lib/api/types'

import { RiskDumbbell } from './RiskDumbbell'
import { RiskMatrix } from './RiskMatrix'
import {
  maxExposure,
  rankByReduction,
  RESIDUAL_META,
  RESIDUAL_ORDER,
  residualByBand,
  residualCounts,
  residualSignal,
  riskTotals,
  type Residual,
  type RiskTotals,
} from './riskRanking'

/**
 * Client — Risk Map.
 *
 * One question, answered once: **how much has Aegis reduced my risk?**
 *
 * The headline gives the number a client repeats to their boss (total exposure
 * before → after, and the % removed), split by the residual band so the amber
 * that is left is visible in the very first thing you read. Under it, the
 * likelihood × impact grid shows *which factor* each control actually moved, and
 * then one dumbbell per risk shows the distance travelled, with the control that
 * did the moving named on the same row.
 *
 * Pure scoring lives in the render-free `riskRanking.ts`; this file fetches and
 * composes. Colour means exactly one thing on this page: the residual band still
 * carried. Everything about "before" is achromatic on purpose.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: RiskMapResponse }

/** Module-level formatter — never re-created per render (DESIGN.md §3). */
const WHEN = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })

/** A note is a demo sample when it says so — kept honest, never hidden. */
function isSample(note: string): boolean {
  return /sample/i.test(note)
}

/** Format an ISO timestamp for the provenance line. */
function formatWhen(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : WHEN.format(d)
}

export function RiskMap({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getRiskMap(token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch(
        (e: unknown) =>
          alive &&
          setLoad({
            status: 'error',
            message: e instanceof Error ? e.message : 'Failed to load risk map',
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
          <Loader2 className="size-4 animate-spin" aria-hidden /> Loading risk map…
        </CardBody>
      </Card>
    )
  }
  if (load.status === 'error') {
    return (
      <Card>
        <CardBody role="status" aria-live="polite" className="py-10 text-sm text-block-ink">
          Could not load the risk map. {load.message}
        </CardBody>
      </Card>
    )
  }

  const { note, generated_at, scale, risks } = load.data
  const provenance = (
    <Receipt
      origin={`docs/security/owasp-agentic.md · generated ${formatWhen(generated_at)}`}
      detail={note}
    />
  )

  // An empty register used to render a headline of zeros over an empty list —
  // "0% less exposure across all 0 risks" asserts a measurement that was never
  // taken. Nothing is drawn until there is something to draw.
  if (risks.length === 0) {
    return (
      <div className="flex flex-col gap-4">
        <Card>
          <CardHeader eyebrow="OWASP-Agentic" title="No risk is on the map yet" />
          <CardBody>
            <SceneState name="empty" size="md">
              <Absence
                figure="Risk removed by Aegis"
                why="This deployment has published no risk register, so there is no before and no after to compare."
                needed="A risk map for this deployment — each entry an inherent and a residual position on the same grid."
                className="mx-auto max-w-md text-left"
              />
            </SceneState>
          </CardBody>
        </Card>
        {provenance}
      </div>
    )
  }

  const totals = riskTotals(risks)
  const ceiling = maxExposure(scale, risks)
  const moves = rankByReduction(scale, risks)
  const sample = isSample(note)

  return (
    <div className="flex flex-col gap-4">
      {/* The headline: what the whole map adds up to. */}
      <ReductionHero totals={totals} risks={risks} sample={sample} />

      {/*
        Which factor the control moved — the claim no number on the page makes.

        This was first drawn beside the headline in a 7/5 row, and the measurement
        killed it: the headline tile is ~190px tall at 1920 and a grid is ~450, so
        the row carried 260px of dead canvas. Letting the tile stretch only moved
        that hole inside the card. Full width with the picture and an accessible
        roll-up side by side is the `PipelineIso` duality the pass notes call the
        pattern to copy — and the roll-up is the one grouping of `category` that
        exists anywhere on this screen.
      */}
      <Card>
        <CardHeader
          eyebrow="likelihood × impact"
          title="Which factor the control moved"
          actions={
            <InfoTip label="How to read the grid">
              Each risk is drawn twice — an open mark with no control in the way, a filled one where
              the Aegis control leaves it — so a horizontal move is a control that made the risk
              rarer and a downward move is one that made it cheaper.
            </InfoTip>
          }
        />
        <CardBody className="@container">
          <div className="flex flex-col gap-6 @3xl:flex-row @3xl:items-start">
            <div className="min-w-0 flex-1">
              <RiskMatrix risks={risks} scale={scale} />
            </div>
            <CategoryRoll risks={risks} className="min-w-0 @3xl:w-72" />
          </div>
        </CardBody>
      </Card>

      {/* The movement, risk by risk. */}
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4 pb-2">
          <p className="eyebrow">How far each risk moved · biggest reduction first</p>
          <p className="t-body flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
            <MarkKey kind="before" label="before Aegis" />
            <MarkKey kind="after" label="after the control" />
            <span className="text-muted-foreground/80">← lower risk</span>
          </p>
        </div>
        <RiskDumbbell moves={moves} ceiling={ceiling} />
      </Card>

      {provenance}
    </div>
  )
}

/**
 * The same nine risks cut by `category` — a field the response has always carried
 * and that nothing on this screen grouped by.
 *
 * It is a count list rather than a chart on purpose. Nine risks fall into seven
 * OWASP-Agentic categories, and seven categories is well past the three-series
 * ceiling DESIGN.md §2 measures: a donut of them would be seven wedges in one hue,
 * most of them a single risk, which is a picture of nothing. Counts stated
 * compactly is what that rule asks for instead. The band beside each count is the
 * worst residual still carried in that category, so the list says which subject
 * area is not finished — and it ships the word, not just the hue.
 */
function CategoryRoll({
  risks,
  className,
}: {
  risks: RiskMapResponse['risks']
  className?: string
}): ReactElement {
  const rolled = new Map<string, { count: number; worst: Residual }>()
  for (const r of risks) {
    const seen = rolled.get(r.category)
    if (!seen) rolled.set(r.category, { count: 1, worst: r.residual })
    else {
      seen.count += 1
      if (RESIDUAL_META[r.residual].rank > RESIDUAL_META[seen.worst].rank) seen.worst = r.residual
    }
  }
  const rows = [...rolled.entries()].sort(
    (a, b) =>
      RESIDUAL_META[b[1].worst].rank - RESIDUAL_META[a[1].worst].rank ||
      b[1].count - a[1].count ||
      a[0].localeCompare(b[0]),
  )

  return (
    <div className={cn(className)}>
      <p className="eyebrow mb-1">Risks by category · worst residual first</p>
      <ul className="divide-y divide-border">
        {rows.map(([category, { count, worst }]) => (
          <li key={category} className="flex items-baseline justify-between gap-3 py-2">
            <span className="min-w-0 text-[0.82rem] break-words text-foreground">{category}</span>
            <span className="flex shrink-0 items-center gap-1.5 text-[0.72rem] text-muted-foreground">
              <span
                className="size-2 shrink-0 rounded-full"
                style={{ background: SIGNALS[residualSignal(worst)].hex }}
                aria-hidden
              />
              {RESIDUAL_META[worst].label}
              <Figure className="ml-1 font-medium text-foreground">{count}</Figure>
            </span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * The number a client repeats to their boss: total exposure before Aegis, total
 * after, and the share removed — reconciled against a bar whose remaining
 * portion is split by residual band, so "what is left" never reads as uniformly
 * safe. The removed portion is deliberately achromatic: it is absence of risk,
 * not a fourth signal colour.
 */
function ReductionHero({
  totals,
  risks,
  sample,
  className,
}: {
  totals: RiskTotals
  risks: RiskMapResponse['risks']
  sample: boolean
  className?: string
}): ReactElement {
  const byBand = residualByBand(risks)
  const counts = residualCounts(risks)
  const pctOf = (n: number): number => (totals.inherent > 0 ? (n / totals.inherent) * 100 : 0)

  return (
    <Card className={cn('p-6', className)}>
      <div className="flex items-center gap-2">
        <span className="size-2 rounded-full" style={{ background: SIGNALS.ok.hex }} aria-hidden />
        <span className="eyebrow">Risk removed by Aegis</span>
        <InfoTip label="About this number">
          Every risk&apos;s likelihood × impact, summed at both positions — a crude aggregate, but
          derived from exactly the numbers each row below shows.
        </InfoTip>
        {sample && (
          <span className="eyebrow ml-auto rounded-sm border border-border px-1.5 py-0.5 text-[0.58rem]">
            sample
          </span>
        )}
      </div>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
        <div className="flex items-end gap-3">
          {/*
            28px, not 48px. DESIGN.md §3: no published enterprise system in the survey
            puts a KPI numeral above 32px, and Atlassian — the only one with a dedicated
            metric ramp — caps its largest at 28. `text-5xl` was the marketing dialect
            on a governance figure.
          */}
          <Figure size="display" className="text-foreground">
            {Math.round(totals.removedPct)}%
          </Figure>
          <span className="t-body mb-1 max-w-[20rem] text-muted-foreground">
            less agent-risk exposure across all {totals.total} risks
          </span>
        </div>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <Stat label="Before Aegis" value={totals.inherent} muted />
          <Stat label="After Aegis" value={totals.residual} />
          <Stat label="Risks moved" value={totals.moved} suffix={`of ${totals.total}`} />
        </div>
      </div>

      {/* Before (whole bar) vs after (the coloured head) — reconciles the % above. */}
      <div className="pt-5">
        <div
          className="flex h-3.5 w-full overflow-hidden rounded-full bg-surface-2"
          role="img"
          aria-label={`Total exposure fell from ${totals.inherent} to ${totals.residual}, removing ${Math.round(totals.removedPct)} percent. Of what remains, ${byBand.low} sits in the low band, ${byBand.medium} in medium and ${byBand.high} in high.`}
        >
          {RESIDUAL_ORDER.slice()
            .reverse()
            .map((band) =>
              byBand[band] > 0 ? (
                <span
                  key={band}
                  className="h-full"
                  style={{ width: `${pctOf(byBand[band])}%`, background: SIGNALS[residualSignal(band)].hex }}
                />
              ) : null,
            )}
        </div>

        <div className="mt-2.5 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[0.72rem] text-muted-foreground">
          {RESIDUAL_ORDER.slice()
            .reverse()
            .map((band) =>
              counts[band] > 0 ? (
                <span key={band} className="flex items-center gap-1.5">
                  <span
                    className="size-2.5 shrink-0 rounded-full"
                    style={{ background: SIGNALS[residualSignal(band)].hex }}
                    aria-hidden
                  />
                  {RESIDUAL_META[band].label} residual ·{' '}
                  <Figure className="font-medium text-foreground">{counts[band]}</Figure> risks,{' '}
                  <Figure className="font-medium text-foreground">{byBand[band]}</Figure> still carried
                </span>
              ) : null,
            )}
          <span className="flex items-center gap-1.5">
            <span className="size-2.5 shrink-0 rounded-full bg-surface-2 ring-1 ring-border" aria-hidden />
            Removed by Aegis ·{' '}
            <Figure className="font-medium text-foreground">{totals.removed}</Figure>
          </span>
        </div>
      </div>
    </Card>
  )
}

/**
 * One labelled figure, in the tile anatomy DESIGN.md §3 specifies — label above
 * the value, 4px apart, in muted ink. The numeral goes through the shared
 * `Figure` primitive rather than a local copy of it.
 */
function Stat({
  label,
  value,
  suffix,
  muted = false,
}: {
  label: string
  value: number
  suffix?: string
  muted?: boolean
}): ReactElement {
  return (
    <div className="min-w-0">
      <p className="eyebrow mb-1">{label}</p>
      <Figure
        size="stat"
        className={muted ? 'text-muted-foreground' : 'text-foreground'}
        unit={suffix}
      >
        {value}
      </Figure>
    </div>
  )
}

/**
 * The two marks, shown once in the chart header so the dumbbells need no legend
 * lecture: a hollow mark for "before", a filled one for "after".
 */
function MarkKey({ kind, label }: { kind: 'before' | 'after'; label: string }): ReactElement {
  return (
    <span className="flex items-center gap-1.5">
      {kind === 'before' ? (
        <span className="size-2.5 shrink-0 rounded-full border-[1.5px] border-muted-foreground bg-card" aria-hidden />
      ) : (
        <span className="size-2.5 shrink-0 rounded-full bg-muted-foreground" aria-hidden />
      )}
      {label}
    </span>
  )
}

/** Client entry for the Risk Map section — gated on a reachable backend. */
export function RiskMount(): ReactElement {
  // `GET /risk-map` is tenant-scoped: hand the view the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  return (
    <BackendGate>
      <div className="space-y-4">
        <PageHeader eyebrow="OWASP-Agentic" title="Risk Map" />
        {hydrated ? (
          <RiskMap token={session?.token ?? null} />
        ) : (
          // A `min-h-[420px]` dashed box for a restore that takes one tick stranded
          // most of a viewport behind a single word.
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
