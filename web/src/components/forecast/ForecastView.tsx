'use client'

import { Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { BacktestPanel } from '@/components/forecast/BacktestPanel'
import { BurndownPanel } from '@/components/forecast/BurndownPanel'
import { CoverageMeter } from '@/components/forecast/CoverageMeter'
import { HorizonChart, HorizonLegend } from '@/components/forecast/HorizonChart'
import { RefusalNotice } from '@/components/forecast/RefusalNotice'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
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

  return (
    <TooltipProvider>
      <div className="space-y-6">
        {/* Section header */}
        <div>
          <p className="eyebrow mb-1">statsforecast · conformal · measured coverage</p>
          <h1 className="t-hero text-foreground">Forecast</h1>
        </div>

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
                <HorizonChart result={result} valueFormatter={(v) => formatUnit(v, result.unit)} />
                <HorizonLegend result={result} valueFormatter={(v) => formatUnit(v, result.unit)} />
                <CoverageMeter result={result} />
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
            <CardHeader eyebrow="rolling-origin backtest" title="Held-out accuracy" />
            <CardBody>
              <BacktestPanel result={result} />
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
