'use client'

import { Loader2, ShieldAlert, WifiOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { getRiskMap } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { SIGNALS } from '@/config/signals'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { cn } from '@/lib/utils'
import type { RiskMapResponse } from '@/lib/api/types'

import { RiskLadder } from './RiskLadder'
import {
  maxExposure,
  rankRisks,
  RESIDUAL_META,
  RESIDUAL_ORDER,
  residualCounts,
  residualSignal,
  type Residual,
} from './riskRanking'

/**
 * Client — Risk Map.
 *
 * The business-facing assurance view: every way an autonomous agent can go
 * wrong, **ranked worst residual first**, each with the concrete Aegis control
 * that holds it down. Ordering and bar length carry inherent exposure
 * (likelihood × impact) — the *before*; colour carries the residual band left
 * after the control — the *after*. Pure ranking/scoring lives in the
 * render-free `riskRanking.ts`; this file fetches and composes.
 *
 * This deliberately replaced a 5×5 heat-map grid: with a handful of risks the
 * matrix was mostly empty cells and needed a second pass over cards below to
 * decode each marker. The ladder is one reading order — no cross-referencing,
 * no duplicated likelihood/impact readout, one colour with one meaning.
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
        <CardContent className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> Loading risk map…
        </CardContent>
      </Card>
    )
  }
  if (load.status === 'error') {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-block-ink">
          Could not load the risk map. {load.message}
        </CardContent>
      </Card>
    )
  }

  const { note, generated_at, scale, risks } = load.data
  const counts = residualCounts(risks)
  const ceiling = maxExposure(scale, risks)
  const ranked = rankRisks(scale, risks)
  const sample = isSample(note)

  return (
    <div className="flex flex-col gap-4">
      {/* Intro + honest provenance. */}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <ShieldAlert className="size-4 shrink-0 text-risk" />
          <p className="t-body max-w-2xl text-muted-foreground">
            Every way an autonomous agent can go wrong, ranked by what is still left after the
            control that holds it down.
          </p>
          <InfoTip label="How to read this">
            How to read this: risks are ordered worst residual first. A bar&apos;s length is the
            inherent exposure before mitigation (likelihood × impact, out of {ceiling}); its colour
            is the residual band after the Aegis control. A long green bar is the point — a serious
            risk the guardrail brought down. This map is populated for this deployment&apos;s posture
            and is repopulated per problem.
          </InfoTip>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <Badge variant="secondary">OWASP-Agentic</Badge>
          {sample && (
            <span className="eyebrow rounded-sm border border-border/70 px-1.5 py-0.5 text-[0.58rem] text-muted-foreground">
              sample
            </span>
          )}
        </div>
      </div>

      {/* Residual band tally — the headline number the ladder then explains. */}
      <div className="flex flex-wrap gap-2">
        {RESIDUAL_ORDER.map((band) => (
          <ResidualCount key={band} band={band} count={counts[band]} />
        ))}
      </div>

      {/* The ranked ladder: risks, their exposure, and their controls in one pass. */}
      <Card>
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 px-5 pt-4 pb-3">
          <p className="eyebrow">Risks &amp; controls · worst residual first</p>
          <p className="t-body text-muted-foreground">
            Bar = inherent exposure (likelihood × impact, of {ceiling}) · colour = residual after
            control
          </p>
        </div>
        <RiskLadder ranked={ranked} ceiling={ceiling} />
      </Card>

      <p className="font-mono text-[0.68rem] leading-relaxed text-muted-foreground">
        {note} Generated {formatWhen(generated_at)}.
      </p>
    </div>
  )
}

/** A residual-band tally chip. */
function ResidualCount({ band, count }: { band: Residual; count: number }): ReactElement {
  const token = SIGNALS[residualSignal(band)]
  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border px-3 py-1.5',
        token.border,
        token.bg,
      )}
    >
      <span className="tabular font-display text-lg leading-none font-semibold text-foreground">
        {count}
      </span>
      <span className={cn('t-label', token.text)}>{RESIDUAL_META[band].label} residual</span>
    </div>
  )
}

/**
 * Client entry for the Risk Map section. Runs the boot probe once (live-first,
 * mock fallback), shows the honest offline banner, then renders the ladder wired
 * to `GET /risk-map`.
 */
export function RiskMount(): ReactElement {
  // `GET /risk-map` is tenant-scoped: hand the view the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null || !hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-4">
        <div>
          <p className="eyebrow mb-1">OWASP-Agentic</p>
          <h1 className="t-hero text-foreground">Risk Map</h1>
        </div>
        {mode.mode === 'mock' && (
          <div
            role="status"
            className="flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
          >
            <WifiOff className="size-3.5 shrink-0" />
            <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
          </div>
        )}
        <RiskMap token={session?.token ?? null} />
      </div>
    </TooltipProvider>
  )
}
