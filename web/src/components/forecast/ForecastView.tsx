'use client'

import { ChevronDown, Loader2, RefreshCw, ShieldCheck, Target, TrendingUp, Wallet } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { BacktestPanel } from '@/components/forecast/BacktestPanel'
import { BurndownPanel } from '@/components/forecast/BurndownPanel'
import { CoverageMeter } from '@/components/forecast/CoverageMeter'
import { ExplainabilityPanel } from '@/components/forecast/ExplainabilityPanel'
import { ExportsPanel } from '@/components/forecast/ExportsPanel'
import { HorizonChart, HorizonLegend } from '@/components/forecast/HorizonChart'
import { NotRecordedPanel } from '@/components/forecast/NotRecordedPanel'
import { RefusalNotice } from '@/components/forecast/RefusalNotice'
import { SourceLine } from '@/components/forecast/SourceLine'
import { FORECAST_SOURCE, NOT_RECORDED, forecastSourceDetail } from '@/components/forecast/sources'
import { InfoTip } from '@/components/primitives/InfoTip'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import {
  getForecastBudget,
  getForecastDomain,
  getForecastUsage,
  getTenants,
} from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { ForecastResponse, ForecastResult, Tenant } from '@/lib/api/types'
import type { Role } from '@/lib/portal'

/** Horizons the operator may switch between, in steps of the series' own frequency. */
const HORIZONS = [7, 14, 30] as const

/** The two ledger measures. They are never drawn on one pair of axes — see below. */
const METRICS = [
  { id: 'spend', label: 'Spend' },
  { id: 'calls', label: 'Calls' },
] as const

type Metric = (typeof METRICS)[number]['id']

/** Provenance → an honest tone + label, so a demo series never passes as live data. */
function sourceBadge(source: string): { tone: BadgeTone; label: string } {
  switch (source) {
    case 'usage_ledger':
      return { tone: 'ok', label: 'usage ledger' }
    case 'adapter':
      return { tone: 'ml', label: 'adapter (synthetic domain)' }
    default:
      return { tone: 'neutral', label: source }
  }
}

/**
 * The word for one step of a series' own frequency.
 *
 * The response reports `freq` as a pandas offset alias — `D`, `W`, `H` — which is
 * the right thing for it to say and the wrong thing for a tile label to repeat.
 * "Next step · D" is a machine talking; "Next day" is the same fact.
 */
function stepWord(freq: string, count = 1): string {
  const plural = count === 1 ? '' : 's'
  switch (freq.toUpperCase()) {
    case 'D':
      return `day${plural}`
    case 'W':
      return `week${plural}`
    case 'H':
      return `hour${plural}`
    case 'M':
    case 'MS':
      return `month${plural}`
    default:
      return `step${plural}`
  }
}

/** Format a value with its unit, keeping currency and counts distinguishable. */
function formatUnit(value: number, unit: string | null): string {
  if (unit === 'USD') return `$${value.toFixed(2)}`
  return `${value.toFixed(2)}${unit ? ` ${unit}` : ''}`
}

/** A segmented control — the page's one interaction idiom, used three times. */
function Segmented<T extends string | number>({
  options,
  value,
  onChange,
  label,
}: {
  options: ReadonlyArray<{ id: T; label: string }>
  value: T
  onChange: (next: T) => void
  label: string
}): ReactElement {
  return (
    <div
      role="group"
      aria-label={label}
      className="flex overflow-hidden rounded-lg border border-border"
    >
      {options.map((option) => (
        <button
          key={String(option.id)}
          type="button"
          onClick={() => onChange(option.id)}
          aria-pressed={value === option.id}
          className={`tabular px-2.5 py-1.5 font-mono text-[0.72rem] transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none ${
            value === option.id
              ? 'bg-surface-2 font-semibold text-foreground'
              : 'text-muted-foreground hover:bg-surface-2'
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

/**
 * Forecast — two panels, two `Source:` lines, two different models (§7.15).
 *
 * **The label is load-bearing, and this is an integrity requirement rather than a
 * styling one.** A forecast is a *projection* and a model card is a *record*; a page
 * that rendered them identically would invite a reader to take the SHAP attributions
 * as an explanation of the spend line. They are not: the spend forecast is univariate
 * over `usage_ledger` — its only input is its own history, so there is nothing to
 * attribute — and the attributions belong to the supervised spine, a different model
 * over a different table. Each panel therefore states where its numbers came from,
 * and the explainability panel carries the sentence that answers the question out
 * loud.
 *
 * **Every projected point is drawn as a band, never as a line.** The chart draws the
 * conformal interval and the coverage meter reports the rate that band *achieved* on
 * rolling-origin held-out windows beside the rate it was *asked* for — normally lower.
 * A single confident line would be a claim this data cannot support.
 *
 * **Spend and calls are never on one pair of axes.** They are two measures of
 * different scale, and a second y-axis is the fastest way to imply a relationship
 * that was never measured; the metric switch redraws the same chart instead.
 *
 * **What changed in the redesign, and why.** The page was one card holding four
 * stacked sections, and roughly sixteen blocks of prose explaining them. Every
 * sentence was true, and together they buried three genuinely good charts. So: the
 * controls moved up to the page header, the band, the burn-down and the backtest each
 * became a card of their own, a band of four figures reads the projection before any
 * chart is scrolled to, and the explanatory sentences moved into the `InfoTip`s
 * DESIGN.md §4 says they belong in. Nothing was deleted — "what this page cannot tell
 * you" is still five stated absences, now behind a disclosure with its count on the
 * summary, so it is a footnote rather than a wall.
 *
 * The platform admin defaults to the **aggregate across every tenant** and may narrow
 * to one; a tenant admin sees its own tenant and the selector never renders, because
 * the server would refuse the request anyway (`_scope_tenant`). The client portal
 * keeps the domain demand series, read through the adapter seam.
 */
function ForecastView({ role }: { role: Role }): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const portal = session?.fineRole ?? null

  // The client portal has no tenant-admin rights and no business reading ledger
  // spend, so it gets the domain demand series; every other portal gets the ledger
  // projection, which is the surface with a decision attached to it.
  const isLedgerView = role !== 'client'
  const isPlatformAdmin = portal === 'platform_admin'

  const [horizon, setHorizon] = useState<number>(14)
  const [metric, setMetric] = useState<Metric>('spend')
  const [tenantId, setTenantId] = useState<number | null>(null)
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [data, setData] = useState<ForecastResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    const fetcher = !isLedgerView
      ? getForecastDomain(token, horizon)
      : metric === 'spend'
        ? // The burn-down response carries the same forecast *and* the projection
          // against the cap, so the panel is one request rather than two fits.
          getForecastBudget(token, horizon, 'month', tenantId)
        : getForecastUsage(token, horizon, 'calls', tenantId)
    fetcher
      .then(setData)
      .catch(() => setError('Could not reach the forecast service. Is the backend running?'))
      .finally(() => setLoading(false))
  }, [token, horizon, isLedgerView, metric, tenantId])

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer and 401.
    if (!hydrated) return
    load()
  }, [load, hydrated])

  useEffect(() => {
    // Only a platform admin may list tenants, and only a platform admin is offered
    // the selector. A failure here is not an error on this page: the aggregate view
    // stands on its own and the selector simply does not appear.
    if (!hydrated || !isPlatformAdmin) return
    let alive = true
    getTenants(token)
      .then((res) => {
        if (alive) setTenants(res.rows)
      })
      .catch(() => {
        if (alive) setTenants([])
      })
    return () => {
      alive = false
    }
  }, [token, hydrated, isPlatformAdmin])

  const result: ForecastResult | null = data?.forecast ?? null
  const badge = result ? sourceBadge(result.data_source) : null
  const scopeLabel = useMemo(() => {
    if (!isLedgerView) return 'the domain demand series'
    if (!isPlatformAdmin) return 'your tenant'
    if (tenantId == null) return 'every tenant'
    return tenants.find((t) => t.id === tenantId)?.name ?? `tenant ${tenantId}`
  }, [isLedgerView, isPlatformAdmin, tenantId, tenants])

  const sourceLine = isLedgerView
    ? FORECAST_SOURCE
    : 'Source: adapter (the domain records, through the swap seam) · univariate · statsforecast'
  const sourceDetail = result
    ? `${forecastSourceDetail(result.model, result.history_points, result.interval_method)} · ${scopeLabel}`
    : null

  // ── the four figures that read the projection before a chart is reached ─────
  // Each is already in the response; none is re-derived from a rounded display
  // value, and the horizon total is the sum of the same points the band draws.
  const fmt = (v: number): string => formatUnit(v, result?.unit ?? null)
  const nextStep = result?.points[0] ?? null
  const horizonTotal = result
    ? result.points.reduce((sum, p) => sum + p.point, 0)
    : null
  const history = useMemo(
    () => (result?.history ?? []).map((h) => h.value).filter((v) => Number.isFinite(v)),
    [result],
  )

  /*
    The controls are a **toolbar row of their own**, not the header's right slot.

    `SectionHeader` wraps its `right` in `shrink-0`, which is correct for a badge
    or a count and wrong for five controls: at 390px the cluster refused to
    shrink and pushed the document 95px wider than the viewport, which DESIGN.md
    §4 rules out outright — the page body never scrolls horizontally. A row that
    owns its own line wraps instead of overflowing, and reads as what it is.
  */
  const controls = (
    <div className="flex flex-wrap items-center gap-2 md:justify-end">
      {badge ? <Badge tone={badge.tone}>{badge.label}</Badge> : null}
      {isPlatformAdmin ? (
        <label className="flex items-center gap-1.5">
          <span className="sr-only">Tenant</span>
          <select
            value={tenantId == null ? '' : String(tenantId)}
            onChange={(event) =>
              setTenantId(event.target.value === '' ? null : Number(event.target.value))
            }
            className="rounded-lg border border-border bg-card px-2.5 py-1.5 font-mono text-[0.72rem] text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            <option value="">All tenants (platform)</option>
            {tenants.map((tenant) => (
              <option key={tenant.id} value={String(tenant.id)}>
                {tenant.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {isLedgerView ? (
        <Segmented label="Measure" options={METRICS} value={metric} onChange={setMetric} />
      ) : null}
      <Segmented
        label="Horizon"
        options={HORIZONS.map((h) => ({ id: h, label: String(h) }))}
        value={horizon}
        onChange={setHorizon}
      />
      <button
        type="button"
        onClick={load}
        disabled={loading}
        className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
      >
        {loading ? (
          <Loader2 className="size-3.5 motion-safe:animate-spin" aria-hidden />
        ) : (
          <RefreshCw className="size-3.5" aria-hidden />
        )}
        Refresh
      </button>
    </div>
  )

  return (
    <TooltipProvider>
      <div className="flex flex-col gap-6">
        <div className="flex flex-col gap-4 md:flex-row md:items-end md:justify-between">
          <SectionHeader
            as="h1"
            eyebrow="statsforecast · conformal · measured coverage"
            title="Forecast"
            className="min-w-0"
          />
          {controls}
        </div>

        {error ? (
          <Card>
            <CardBody>
              <p className="py-8 text-center text-sm text-danger">{error}</p>
            </CardBody>
          </Card>
        ) : loading && data == null ? (
          <Card>
            <CardBody>
              <div
                role="status"
                className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground"
              >
                <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
                Fitting and backtesting…
              </div>
            </CardBody>
          </Card>
        ) : data == null ? null : !data.available ? (
          <Card>
            <CardBody>
              {data.refusal ? (
                <RefusalNotice refusal={data.refusal} />
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No forecast and no stated reason — this should not happen.
                </p>
              )}
            </CardBody>
          </Card>
        ) : result ? (
          <>
            {/* ── The projection, as four figures ─────────────────────────── */}
            <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
              <StatCard
                label={`Next ${stepWord(result.freq)}`}
                value={nextStep ? fmt(nextStep.point) : '—'}
                icon={TrendingUp}
                tone="graph"
                trend={history.length > 1 ? history : undefined}
                source={`${result.history_points} observations · ${result.data_source}`}
              />
              <StatCard
                label={`Projected, next ${result.horizon} ${stepWord(result.freq, result.horizon)}`}
                value={horizonTotal == null ? '—' : fmt(horizonTotal)}
                icon={Wallet}
                tone="ml"
                source={
                  nextStep
                    ? `Sum of the ${result.horizon} projected points · band ${fmt(nextStep.lo)}–${fmt(nextStep.hi)} at step 1`
                    : undefined
                }
              />
              {/*
                The model tile leads with its *error*, not its name. A model name is
                not a figure, and set at the 28px tile numeral it overflowed the tile
                and collided with the chip beside it — the shape of the mistake was
                the mistake. The number a reader can act on is how wrong the winner
                was on held-out data; which winner it was is the provenance.
              */}
              <StatCard
                label={`Held-out error · ${result.selection_metric}`}
                value={`${result.backtest.smape.toFixed(1)}%`}
                icon={Target}
                tone="agent"
                source={`${result.model} selected from ${result.candidates.length} candidates`}
              />
              <StatCard
                label="Coverage achieved"
                value={`${(result.backtest.empirical_coverage * 100).toFixed(0)}%`}
                icon={ShieldCheck}
                tone={result.backtest.coverage_meets_request ? 'ok' : 'risk'}
                source={`${(result.backtest.requested_coverage * 100).toFixed(0)}% requested · ${result.backtest.n_points} held-out points`}
              />
            </div>

            {/* ── Card 1 · the band ───────────────────────────────────────── */}
            <Card>
              <CardHeader
                eyebrow={isLedgerView ? 'aegis.forecast · platform' : 'aegis.forecast'}
                title={
                  <span className="inline-flex items-center gap-1.5">
                    {result.label}
                    <InfoTip label="How to read the band">
                      The band is the forecast; the line through it is only its centre. Read
                      the achieved coverage under the chart before quoting either — it is
                      normally lower than the level the interval was asked for.
                    </InfoTip>
                  </span>
                }
                actions={<Badge tone="neutral">{scopeLabel}</Badge>}
              />
              <CardBody className="space-y-4 pt-0">
                <HorizonChart result={result} valueFormatter={fmt} />
                <HorizonLegend result={result} valueFormatter={fmt} />
                <CoverageMeter result={result} />
                <SourceLine source={sourceLine} detail={sourceDetail} />
              </CardBody>
            </Card>

            {/* ── Cards 2 & 3 · the burn-down and the selection evidence ──── */}
            <div className="grid grid-cols-1 gap-4 xl:grid-cols-12">
              {data.burndown ? (
                <Card className="xl:col-span-7">
                  <CardHeader
                    eyebrow="aegis.governance"
                    title="Burn-down against the cap"
                  />
                  <CardBody className="pt-0">
                    <BurndownPanel burndown={data.burndown} />
                  </CardBody>
                </Card>
              ) : null}

              <Card className={data.burndown ? 'xl:col-span-5' : 'xl:col-span-12'}>
                <CardHeader
                  eyebrow="rolling-origin · held out"
                  title="How the model was chosen"
                  actions={
                    <Badge tone="neutral" className="tabular font-mono">
                      {result.backtest.windows} windows
                    </Badge>
                  }
                />
                <CardBody className="pt-0">
                  <BacktestPanel result={result} />
                </CardBody>
              </Card>
            </div>
          </>
        ) : null}

        {/* ── Panel 2: the supervised spine, which is a different model ─────── */}
        {isLedgerView ? <ExplainabilityPanel /> : null}

        {/* ── Taking the record away, and what this page will not claim ─────── */}
        {isLedgerView ? (
          <ExportsPanel
            forecastFilters={{
              tenantId,
              metric,
              horizon,
              window: 'month',
            }}
          />
        ) : null}

        {isLedgerView ? <NotRecordedFootnote /> : null}
      </div>
    </TooltipProvider>
  )
}

/**
 * The five stated absences, collapsed.
 *
 * They were a full card of five three-line blocks at the bottom of the page — a
 * wall of text that read as an apology and pushed the charts up out of the first
 * screen. Nothing is removed: a stated absence is this product's signature and
 * deleting one to tidy the page would be exactly the dishonesty the panel exists
 * to prevent. It is a disclosure instead, with the count on the summary so a
 * reader knows there is something behind it, and DESIGN.md §4's rule — prose that
 * explains a mechanism lives one layer down — is finally followed here too.
 */
function NotRecordedFootnote(): ReactElement {
  return (
    <Card className="overflow-hidden">
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-2 px-5 py-3.5 select-none md:px-6">
          <span className="text-base leading-6 font-semibold text-foreground">
            What this page cannot tell you
          </span>
          <span className="eyebrow rounded-sm border border-border px-1.5 py-0.5">
            {NOT_RECORDED.length} stated absences
          </span>
          <ChevronDown
            className="ml-auto size-4 text-muted-foreground transition-transform duration-200 group-open:rotate-180"
            aria-hidden
          />
        </summary>
        <div className="border-t border-border px-5 py-5 md:px-6">
          <NotRecordedPanel />
        </div>
      </details>
    </Card>
  )
}

/** Mount point for the portal's `forecast` section. */
export function ForecastMount({ role }: { role: Role }): ReactElement {
  return <ForecastView role={role} />
}
