'use client'

import { Activity, Boxes, Loader2, RefreshCw, Sigma, TrendingUp } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { ConformalBand } from '@/components/charts/ConformalBand'
import { BacktestPanel } from '@/components/forecast/BacktestPanel'
import { BurndownPanel } from '@/components/forecast/BurndownPanel'
import { HorizonChart } from '@/components/forecast/HorizonChart'
import { RefusalNotice } from '@/components/forecast/RefusalNotice'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { getForecastBudget, getForecastDomain } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type { ForecastResponse, ForecastResult } from '@/lib/api/types'
import type { Role } from '@/lib/portal'

/** Horizons the operator may switch between, in steps of the series' own frequency. */
const HORIZONS = [7, 14, 30] as const

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

/**
 * Forecast — the `aegis.forecast` surface.
 *
 * Two series, one contract. The platform view projects the tenant's ledger spend
 * forward and burns it down against the configured cap; the client view projects
 * the domain's own demand series, read through the adapter seam so it retargets
 * with everything else. Whichever is shown, the page reports what was *measured*:
 * the achieved interval coverage, the accuracy on held-out windows and the
 * candidate models that lost.
 *
 * When a series is too short, the page renders the refusal — the observation count,
 * the count required and the reason — instead of a chart. That is the deliberate
 * behaviour: a line drawn through nine points would look exactly like a forecast.
 */
function ForecastView({ role }: { role: Role }): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  // The client portal has no tenant-admin rights and no business reading ledger
  // spend, so it gets the domain demand series; every other portal gets the budget
  // projection, which is the surface with a decision attached to it.
  const isBudgetView = role !== 'client'

  const [horizon, setHorizon] = useState<number>(14)
  const [data, setData] = useState<ForecastResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    setLoading(true)
    setError(null)
    const fetcher = isBudgetView
      ? getForecastBudget(token, horizon)
      : getForecastDomain(token, horizon)
    fetcher
      .then(setData)
      .catch(() => setError('Could not reach the forecast service. Is the backend running?'))
      .finally(() => setLoading(false))
  }, [token, horizon, isBudgetView])

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer and 401.
    if (!hydrated) return
    load()
  }, [load, hydrated])

  const result: ForecastResult | null = data?.forecast ?? null
  const source = result ? sourceBadge(result.data_source) : null

  // The final horizon step is the one worth pinning: the band is widest there, so
  // it is the honest headline for "how far can this drift by the end".
  const terminal = useMemo(() => result?.points.at(-1) ?? null, [result])

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">statsforecast · conformal · measured coverage</p>
        <h1 className="t-hero text-foreground">Forecast</h1>
      </div>

      {/* ── Headline figures ──────────────────────────────────────────────────── */}
      {result ? (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
          <StatCard label="Model" value={result.model} icon={Sigma} tone="ml" />
          <StatCard
            label="Coverage achieved"
            value={`${(result.backtest.empirical_coverage * 100).toFixed(1)}%`}
            icon={Activity}
            tone={result.backtest.coverage_meets_request ? 'ok' : 'risk'}
          />
          <StatCard
            label="sMAPE (held out)"
            value={`${result.backtest.smape.toFixed(2)}%`}
            icon={TrendingUp}
            tone="agent"
          />
          <StatCard
            label="History"
            value={`${result.history_points} × ${result.freq}`}
            icon={Boxes}
            tone="neutral"
          />
        </div>
      ) : null}

      {/* ── The forecast itself ───────────────────────────────────────────────── */}
      <Card>
        <CardHeader
          eyebrow="aegis.forecast"
          title={result ? result.label : isBudgetView ? 'Daily spend' : 'Domain demand'}
          actions={
            <div className="flex items-center gap-2">
              {source ? <Badge tone={source.tone}>{source.label}</Badge> : null}
              <div className="flex overflow-hidden rounded-lg border border-border">
                {HORIZONS.map((h) => (
                  <button
                    key={h}
                    type="button"
                    onClick={() => setHorizon(h)}
                    aria-pressed={horizon === h}
                    className={`tabular px-2.5 py-1.5 font-mono text-[0.72rem] transition-colors ${
                      horizon === h
                        ? 'bg-surface-2 font-semibold text-foreground'
                        : 'text-muted-foreground hover:bg-surface-2'
                    }`}
                  >
                    {h}
                  </button>
                ))}
              </div>
              <button
                type="button"
                onClick={load}
                disabled={loading}
                className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
              >
                {loading ? (
                  <Loader2 className="size-3.5 animate-spin" />
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
              <Loader2 className="size-4 animate-spin" />
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
              <HorizonChart
                result={result}
                valueFormatter={(v) => formatUnit(v, result.unit)}
              />
              <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
                {terminal ? (
                  <div className="rounded-xl border border-border bg-surface-2/40 p-4">
                    <p className="eyebrow mb-3">
                      step {terminal.step} of {result.horizon} · the widest point
                    </p>
                    {/* The band component says "conformal · calibrated confidence" in
                        its own copy, so it is only rendered for a conformal interval. */}
                    {result.interval_method === 'conformal' ? (
                      <ConformalBand
                        prediction={terminal.point}
                        interval={[terminal.lo, terminal.hi]}
                        confidence={result.requested_level}
                        intervalWidth={terminal.hi - terminal.lo}
                        setSize={null}
                        unit={result.unit ?? undefined}
                      />
                    ) : (
                      <div className="space-y-2">
                        <Badge tone="risk">parametric interval — not calibrated</Badge>
                        <p className="tabular font-mono text-[1.2rem] font-bold text-foreground">
                          {formatUnit(terminal.point, result.unit)}
                        </p>
                        <p className="tabular font-mono text-[0.72rem] text-muted-foreground">
                          {formatUnit(terminal.lo, result.unit)} –{' '}
                          {formatUnit(terminal.hi, result.unit)}
                        </p>
                        <p className="text-[0.7rem] leading-snug text-muted-foreground">
                          These bounds come from the fitted model&apos;s own predictive
                          distribution. They hold only as far as its residual assumptions do —
                          read the achieved coverage below, not this level.
                        </p>
                      </div>
                    )}
                    <p className="mt-3 text-[0.7rem] leading-snug text-muted-foreground">
                      {result.requested_level * 100}% is the level requested. What the band{' '}
                      <span className="font-semibold text-foreground">achieved</span> on held-out
                      data is {(result.backtest.empirical_coverage * 100).toFixed(1)}%.
                    </p>
                  </div>
                ) : null}
                <div className="rounded-xl border border-border bg-surface-2/40 p-4">
                  <p className="eyebrow mb-3">interval provenance</p>
                  <p className="font-mono text-[0.72rem] leading-relaxed break-words text-foreground">
                    {result.interval_method_detail}
                  </p>
                  <p className="mt-3 text-[0.7rem] leading-snug text-muted-foreground">
                    Calibration is chronological throughout: every band is fitted on data
                    strictly earlier than the points it is scored on, so no future value ever
                    reaches the calibration set.
                  </p>
                </div>
              </div>
            </>
          ) : null}
        </CardBody>
      </Card>

      {/* ── Budget burn-down ──────────────────────────────────────────────────── */}
      {data?.burndown ? (
        <Card>
          <CardHeader eyebrow="aegis.forecast · aegis.governance" title="Budget burn-down" />
          <CardBody>
            <BurndownPanel burndown={data.burndown} />
          </CardBody>
        </Card>
      ) : null}

      {/* ── Backtest ──────────────────────────────────────────────────────────── */}
      {result ? (
        <Card>
          <CardHeader eyebrow="rolling-origin backtest" title="Measured accuracy and coverage" />
          <CardBody>
            <BacktestPanel result={result} />
          </CardBody>
        </Card>
      ) : null}
    </div>
  )
}

/** Mount point for the portal's `forecast` section. */
export function ForecastMount({ role }: { role: Role }): ReactElement {
  return <ForecastView role={role} />
}
