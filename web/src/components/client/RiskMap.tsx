'use client'

import { Loader2, ShieldAlert, ShieldCheck, WifiOff } from 'lucide-react'
import { useEffect, useRef, useState, type ReactElement } from 'react'

import { getRiskMap } from '@/lib/api/client'
import { Badge } from '@/components/primitives/badge'
import { Card, CardContent } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { SIGNALS } from '@/config/signals'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { cn } from '@/lib/utils'
import type { RiskEntry, RiskMapResponse } from '@/lib/api/types'

import { RiskMatrixGrid } from './RiskMatrixGrid'
import {
  buildMatrix,
  RESIDUAL_META,
  residualCounts,
  residualSignal,
  worstFirst,
  type Residual,
} from './riskMatrix'

/**
 * Client — Risk Map.
 *
 * The business-facing **risk matrix**: every way an autonomous agent can go
 * wrong, plotted on a likelihood × impact grid and coloured by what is left
 * after the mitigating control. The exposure is legible at a glance — the hot
 * top-right corner is high-likelihood + high-impact — and each risk carries the
 * concrete Aegis control that holds it down. Pure placement/scoring lives in the
 * recharts-free `riskMatrix.ts` (unit-tested); this file fetches and renders.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: RiskMapResponse }

/** Residual band → Badge variant (green / amber / red). */
const RESIDUAL_BADGE: Record<Residual, 'ok' | 'risk' | 'block'> = {
  low: 'ok',
  medium: 'risk',
  high: 'block',
}

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
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const cardRefs = useRef(new Map<string, HTMLDivElement>())

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    setSelectedId(null)
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

  // Bring the selected risk's card into view when picked from the grid.
  useEffect(() => {
    if (selectedId == null) return
    cardRefs.current.get(selectedId)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  }, [selectedId])

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
  const matrix = buildMatrix(scale, risks)
  const counts = residualCounts(risks)
  const ranked = worstFirst(risks)
  const sample = isSample(note)

  return (
    <div className="flex flex-col gap-4">
      {/* Intro + honest provenance. */}
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          <ShieldAlert className="size-4 shrink-0 text-risk" />
          <p className="t-body max-w-2xl text-muted-foreground">
            Every way an autonomous agent can go wrong, placed by likelihood and impact — and the
            control that holds each one down.
          </p>
          <InfoTip label="Why this matters">
            Why this matters: the matrix shows where your biggest exposures sit (top-right = most
            likely and most damaging) and how each is mitigated, so residual risk is visible rather
            than assumed. This map is populated for this deployment&apos;s posture and is repopulated
            per problem.
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

      {/* Residual band tally. */}
      <div className="flex flex-wrap gap-2">
        {(['high', 'medium', 'low'] as const).map((band) => (
          <ResidualCount key={band} band={band} count={counts[band]} />
        ))}
      </div>

      {/* The matrix + legend. */}
      <Card>
        <CardContent className="grid grid-cols-1 gap-6 pt-5 lg:grid-cols-[minmax(0,1fr)_15rem]">
          <RiskMatrixGrid matrix={matrix} selectedId={selectedId} onSelect={setSelectedId} />
          <div className="flex flex-col gap-4">
            <Legend />
            {matrix.unplaced.length > 0 && (
              <p className="t-body text-muted-foreground">
                {matrix.unplaced.length} risk{matrix.unplaced.length === 1 ? '' : 's'} fell outside
                the {scale.likelihood.length}×{scale.impact.length} scale and are listed below only.
              </p>
            )}
            <p className="t-body mt-auto text-muted-foreground">
              Generated {formatWhen(generated_at)}.
            </p>
          </div>
        </CardContent>
      </Card>

      {/* The risks themselves, worst exposure first. */}
      <div>
        <p className="eyebrow mb-2">Risks &amp; controls · worst exposure first</p>
        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          {ranked.map((risk) => (
            <RiskCard
              key={risk.id}
              risk={risk}
              selected={selectedId === risk.id}
              onSelect={setSelectedId}
              registerRef={(el) => {
                if (el) cardRefs.current.set(risk.id, el)
                else cardRefs.current.delete(risk.id)
              }}
            />
          ))}
        </div>
      </div>

      <p className="font-mono text-[0.68rem] leading-relaxed text-muted-foreground">{note}</p>
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

/** The colour key for the matrix. */
function Legend(): ReactElement {
  return (
    <div className="flex flex-col gap-2">
      <p className="eyebrow">Residual after control</p>
      <ul className="flex flex-col gap-1.5">
        {(['low', 'medium', 'high'] as const).map((band) => {
          const token = SIGNALS[residualSignal(band)]
          return (
            <li key={band} className="flex items-center gap-2 text-sm">
              <span
                className="size-2.5 rounded-full"
                style={{ background: token.hex }}
                aria-hidden
              />
              <span className="text-muted-foreground">{RESIDUAL_META[band].label}</span>
            </li>
          )
        })}
      </ul>
      <p className="t-body mt-1 text-muted-foreground">
        Cell shading shows inherent exposure; a marker&apos;s colour is what remains after its
        control. Select a marker to highlight its control below.
      </p>
    </div>
  )
}

/** One risk with its category, mitigation, control and residual band. */
function RiskCard({
  risk,
  selected,
  onSelect,
  registerRef,
}: {
  risk: RiskEntry
  selected: boolean
  onSelect: (id: string | null) => void
  registerRef: (el: HTMLDivElement | null) => void
}): ReactElement {
  return (
    <Card
      ref={registerRef}
      onClick={() => onSelect(selected ? null : risk.id)}
      className={cn(
        'cursor-pointer p-4 transition-[box-shadow,border-color] duration-150 hover:shadow-hover',
        selected && 'border-ring ring-2 ring-ring',
      )}
    >
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-mono text-[0.68rem] tabular text-muted-foreground">{risk.id}</span>
        <span className="t-title text-foreground">{risk.title}</span>
        <Badge variant={RESIDUAL_BADGE[risk.residual]} className="ml-auto">
          {RESIDUAL_META[risk.residual].label} residual
        </Badge>
      </div>
      <p className="mt-1 flex flex-wrap items-center gap-x-2 text-[0.72rem] text-muted-foreground">
        <span className="eyebrow">{risk.category}</span>
        <span aria-hidden>·</span>
        <span className="tabular">
          likelihood {risk.likelihood} × impact {risk.impact}
        </span>
      </p>
      <p className="t-body mt-2 text-foreground/90">{risk.mitigation}</p>
      <p className="mt-2 flex items-center gap-1.5 text-[0.78rem] text-agent-ink">
        <ShieldCheck className="size-3.5 shrink-0" aria-hidden />
        {risk.control_ref}
      </p>
    </Card>
  )
}

/**
 * Client entry for the Risk Map section. Runs the boot probe once (live-first,
 * mock fallback), shows the honest offline banner, then renders the matrix wired
 * to `GET /risk-map`.
 */
export function RiskMount(): ReactElement {
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

  if (mode === null) {
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
        <RiskMap token={null} />
      </div>
    </TooltipProvider>
  )
}
