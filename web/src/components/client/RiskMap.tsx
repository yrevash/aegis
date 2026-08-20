'use client'

import { Loader2, ShieldAlert } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getRiskMap } from '@/lib/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { PageHeader } from '@/components/primitives/PageHeader'
import { InfoTip } from '@/components/primitives/InfoTip'
import { BackendGate } from '@/components/shared/BackendGate'
import { SIGNALS } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { RiskMapResponse } from '@/lib/api/types'

import { RiskDumbbell } from './RiskDumbbell'
import {
  maxExposure,
  rankByReduction,
  RESIDUAL_META,
  RESIDUAL_ORDER,
  residualByBand,
  residualCounts,
  residualSignal,
  riskTotals,
  type RiskTotals,
} from './riskRanking'

/**
 * Client — Risk Map.
 *
 * One question, answered once: **how much has Aegis reduced my risk?**
 *
 * The headline gives the number a client repeats to their boss (total exposure
 * before → after, and the % removed), split by the residual band so the amber
 * that is left is visible in the very first thing you read. Under it, one
 * dumbbell per risk shows the *movement* — hollow mark where the risk sits with
 * no control, filled mark where the real Aegis control leaves it, arrow along the
 * distance removed — with the control that did the moving named on the same row.
 *
 * Pure scoring lives in the render-free `riskRanking.ts`; this file fetches and
 * composes. Colour means exactly one thing on this page: the residual band still
 * carried. Everything about "before" is achromatic on purpose.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: RiskMapResponse }

/** A note is a demo sample when it says so — kept honest, never hidden. */
function isSample(note: string): boolean {
  return /sample/i.test(note)
}

/** Format an ISO timestamp for the "generated" caption. */
function formatWhen(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime())
    ? iso
    : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
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
        <CardBody className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading risk map…
        </CardBody>
      </Card>
    )
  }
  if (load.status === 'error') {
    return (
      <Card>
        <CardBody className="py-10 text-sm text-block-ink">
          Could not load the risk map. {load.message}
        </CardBody>
      </Card>
    )
  }

  const { note, generated_at, scale, risks } = load.data
  const totals = riskTotals(risks)
  const ceiling = maxExposure(scale, risks)
  const moves = rankByReduction(scale, risks)
  const sample = isSample(note)

  return (
    <div className="flex flex-col gap-4">
      {/* Intro + honest provenance. */}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <ShieldAlert className="size-4 shrink-0 text-risk" />
          <p className="t-body max-w-2xl text-muted-foreground">
            Every way an autonomous agent can go wrong — and how far each one moved once the Aegis
            control was put on it.
          </p>
          <InfoTip label="How to read this">
            How to read this: each risk is scored likelihood × impact, out of {ceiling}. The hollow
            mark is where it sits with no control in the way; the filled mark is where the Aegis
            control leaves it, and the arrow is the distance removed. Left is safer. Colour means one
            thing only — the risk still carried after the control. Both positions are engineering
            judgement grounded in the OWASP-Agentic mapping, not measured incident rates, and prompt
            injection is deliberately never shown as solved.
          </InfoTip>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge tone="neutral">OWASP-Agentic</Badge>
          {sample && (
            <span className="eyebrow rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.58rem] text-muted-foreground">
              sample
            </span>
          )}
        </div>
      </div>

      {/* The headline: what the whole map adds up to. */}
      <ReductionHero totals={totals} risks={risks} />

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

      <p className="font-mono text-[0.68rem] leading-relaxed text-muted-foreground">
        {note} Generated {formatWhen(generated_at)}.
      </p>
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
}: {
  totals: RiskTotals
  risks: RiskMapResponse['risks']
}): ReactElement {
  const byBand = residualByBand(risks)
  const counts = residualCounts(risks)
  const pctOf = (n: number): number => (totals.inherent > 0 ? (n / totals.inherent) * 100 : 0)

  return (
    <Card className="p-6">
      <div className="flex items-center gap-2">
        <span className="size-2 rounded-full" style={{ background: SIGNALS.ok.hex }} aria-hidden />
        <span className="eyebrow">Risk removed by Aegis</span>
        <InfoTip label="About this number">
          Total exposure is every risk&apos;s likelihood × impact added up — a crude aggregate, but
          derived from exactly the numbers each row below shows, so you can audit it against them.
          The &quot;after&quot; total is the same sum taken at each risk&apos;s residual position.
        </InfoTip>
      </div>

      <div className="mt-3 flex flex-wrap items-end justify-between gap-x-10 gap-y-4">
        <div className="flex items-end gap-3">
          {/*
            28px, not 48px. DESIGN.md §3: no published enterprise system in the survey
            puts a KPI numeral above 32px, and Atlassian — the only one with a dedicated
            metric ramp — caps its largest at 28. `text-5xl` was the marketing dialect
            on a governance figure.
          */}
          <span className="tabular font-display text-[1.75rem] leading-8 font-semibold tracking-[-0.01em] text-foreground">
            {Math.round(totals.removedPct)}%
          </span>
          <span className="t-body mb-1 max-w-[20rem] text-muted-foreground">
            less agent-risk exposure across all {totals.total} risks
          </span>
        </div>
        <div className="flex flex-wrap items-end gap-x-8 gap-y-3">
          <Figure label="Before Aegis" value={totals.inherent} muted />
          <Figure label="After Aegis" value={totals.residual} />
          <Figure label="Risks moved" value={totals.moved} suffix={`of ${totals.total}`} />
        </div>
      </div>

      {/* Before (whole bar) vs after (the coloured head) — reconciles the % above. */}
      <div
        className="mt-5 flex h-3.5 w-full overflow-hidden rounded-full bg-surface-2"
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
                  className="size-2.5 rounded-full"
                  style={{ background: SIGNALS[residualSignal(band)].hex }}
                  aria-hidden
                />
                {RESIDUAL_META[band].label} residual ·{' '}
                <span className="tabular font-medium text-foreground">{counts[band]} risks</span>,{' '}
                <span className="tabular font-medium text-foreground">{byBand[band]}</span> still
                carried
              </span>
            ) : null,
          )}
        <span className="flex items-center gap-1.5">
          <span className="size-2.5 rounded-full bg-surface-2 ring-1 ring-border" aria-hidden />
          Removed by Aegis ·{' '}
          <span className="tabular font-medium text-foreground">{totals.removed}</span>
        </span>
      </div>
    </Card>
  )
}

/** One labelled headline figure. */
function Figure({
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
    <div>
      <p className="eyebrow mb-1">{label}</p>
      <p
        className={cn(
          'tabular font-display text-2xl leading-none font-semibold',
          muted ? 'text-muted-foreground' : 'text-foreground',
        )}
      >
        {value}
        {suffix && <span className="ml-1 text-sm font-normal text-muted-foreground">{suffix}</span>}
      </p>
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
        <span className="size-2.5 rounded-full border-[1.5px] border-muted-foreground bg-card" aria-hidden />
      ) : (
        <span className="size-2.5 rounded-full bg-muted-foreground" aria-hidden />
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

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
        <div className="space-y-4">
          <PageHeader
            eyebrow="OWASP-Agentic"
            title="Risk Map"
          />
          <RiskMap token={session?.token ?? null} />
        </div>
    </BackendGate>
  )
}
