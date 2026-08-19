'use client'

import { Loader2, RefreshCw } from 'lucide-react'
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
import { FORECAST_SOURCE, forecastSourceDetail } from '@/components/forecast/sources'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
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

  return (
    <TooltipProvider>
      <div className="space-y-6">
        <div>
          <p className="eyebrow mb-1">statsforecast · conformal · measured coverage</p>
          <h1 className="t-hero text-foreground">Forecast</h1>
        </div>

        {/* ── Panel 1: the projection ───────────────────────────────────────── */}
        <Card>
          <CardHeader
            eyebrow={isLedgerView ? 'aegis.forecast · platform' : 'aegis.forecast'}
            title={result ? result.label : isLedgerView ? 'Ledger projection' : 'Domain demand'}
            actions={
              <div className="flex flex-wrap items-center justify-end gap-2">
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
                  <Segmented
                    label="Measure"
                    options={METRICS}
                    value={metric}
                    onChange={setMetric}
                  />
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
                    <Loader2 className="size-3.5 motion-safe:animate-spin" />
                  ) : (
                    <RefreshCw className="size-3.5" />
                  )}
                  Refresh
                </button>
              </div>
            }
          />
          <CardBody className="space-y-5">
            {error ? (
              <p className="py-8 text-center text-sm text-danger">{error}</p>
            ) : loading && data == null ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                <Loader2 className="size-4 motion-safe:animate-spin" />
                Fitting and backtesting…
              </div>
            ) : data == null ? null : !data.available ? (
              data.refusal ? (
                <RefusalNotice refusal={data.refusal} />
              ) : (
                <p className="py-8 text-center text-sm text-muted-foreground">
                  No forecast and no stated reason — this should not happen.
                </p>
              )
            ) : result ? (
              <>
                <p className="text-[0.78rem] leading-relaxed text-muted-foreground">
                  The band is the forecast; the line through it is only its centre. Read the
                  achieved coverage under the chart before quoting either.
                </p>
                <HorizonChart result={result} valueFormatter={(v) => formatUnit(v, result.unit)} />
                <HorizonLegend result={result} valueFormatter={(v) => formatUnit(v, result.unit)} />
                <CoverageMeter result={result} />
                {data.burndown ? (
                  <div className="space-y-3 border-t border-border pt-5">
                    <p className="eyebrow">burn-down against the cap · aegis.governance</p>
                    <BurndownPanel burndown={data.burndown} />
                  </div>
                ) : null}
                <div className="space-y-3 border-t border-border pt-5">
                  <p className="eyebrow">rolling-origin backtest · held-out accuracy</p>
                  <BacktestPanel result={result} />
                </div>
              </>
            ) : null}

            <SourceLine source={sourceLine} detail={sourceDetail} />
          </CardBody>
        </Card>

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

        {isLedgerView ? (
          <Card>
            <CardHeader
              eyebrow="not recorded · not shown"
              title="What this page cannot tell you"
            />
            <CardBody>
              <NotRecordedPanel />
            </CardBody>
          </Card>
        ) : null}
      </div>
    </TooltipProvider>
  )
}

/** Mount point for the portal's `forecast` section. */
export function ForecastMount({ role }: { role: Role }): ReactElement {
  return <ForecastView role={role} />
}
