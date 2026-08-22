'use client'

import { CalendarClock, Clock4, Coins, Cpu, Gauge as GaugeIcon } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { RankedBars } from '@/components/charts/RankedBars'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { dayLabel, modelMix, shortModel, toDaily } from '@/components/dashboard/adminOverview'
import {
  getAnalyticsBoards,
  getAnalyticsStatus,
  type AnalyticsBoard,
  type AnalyticsStatus,
} from '@/lib/api/analytics'
import { getUsage } from '@/lib/api/client'
import type { UsageResponse } from '@/lib/api/types'
import { errorSentence } from '@/lib/api/apiError'
import { useAuth } from '@/lib/auth/AuthContext'

import { analyticsState } from './analyticsBoard'
import { SupersetBoards, SupersetOffPanel } from './SupersetBoards'
import { hourProfile, totalTokens, unitCosts, weekdayProfile } from './usageSeries'

// ── formatting ───────────────────────────────────────────────────────────────

function usd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}k`
  if (abs > 0 && abs < 0.01) return `$${n.toFixed(4)}`
  return `$${n.toFixed(2)}`
}

function compact(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(Math.round(n))
}

/** The two windows `GET /admin/usage` actually implements. */
const WINDOWS = [
  { key: 'month', label: '30 days' },
  { key: 'day', label: '24 hours' },
] as const
type WindowKey = (typeof WINDOWS)[number]['key']

/**
 * Analytics — the usage ledger, drawn by Aegis, with Superset as an add-on.
 *
 * **Why this screen changed shape.** `board_data()` on the backend is
 * `return await self._live_client().board_data(...)` and nothing else: every
 * chart on this page came from Superset, and Superset is not provisioned on this
 * deployment. So the page rendered one dashed rectangle explaining that, which
 * was truthful and useless — a jury reading "analytics" saw a product with no
 * analytics.
 *
 * The fix is a second path rather than a fallback. `GET /admin/usage` already
 * serves the metered ledger — hourly spend buckets and a per-model roll-up, both
 * RBAC-scoped — and five real questions come straight out of it: what spend does
 * over the window, which models carry it, what a thousand tokens actually cost per
 * model, and the weekday and hour-of-day rhythms underneath. None of that needs a
 * BI server, so none of it goes dark when the BI server is off.
 *
 * Superset keeps the section it earns: boards built by an analyst, datasets the
 * API does not expose, ad-hoc SQL. When it is absent the section says so in the
 * space it would occupy, and the charts above are unaffected — which is the whole
 * point of splitting them.
 */
function AnalyticsView(): ReactElement {
  const { session, hydrated } = useAuth()
  const token: string | null = session?.token ?? null

  const [windowKey, setWindowKey] = useState<WindowKey>('month')
  const [usage, setUsage] = useState<UsageResponse | null>(null)
  const [usageError, setUsageError] = useState<string | null>(null)
  const [usageLoaded, setUsageLoaded] = useState(false)

  const [status, setStatus] = useState<AnalyticsStatus | null>(null)
  const [statusLoaded, setStatusLoaded] = useState(false)
  const [boards, setBoards] = useState<AnalyticsBoard[]>([])
  const [windows, setWindows] = useState<Record<string, string>>({})

  // ── the ledger, which is the page's own data ───────────────────────────────
  useEffect(() => {
    if (!hydrated) return
    // `GET /admin/usage` is `require_tenant_admin`. The analytics section is mounted on
    // the client portal too, where this panel could only ever 403 — so it asked, was
    // refused, and rendered "the usage ledger did not answer", which blames the backend
    // for a permission decision. The boards on this page are unaffected and still load;
    // a client reads their own spend on Savings, which is scoped to them by design.
    if (session?.role !== 'admin') {
      setUsage(null)
      setUsageError(
        'The spend ledger is a tenant-administrator reading. Your own costs are on Savings.',
      )
      setUsageLoaded(true)
      return
    }
    let alive = true
    setUsageLoaded(false)
    void getUsage(token, { window: windowKey })
      .then((u) => {
        if (!alive) return
        setUsage(u)
        setUsageError(null)
      })
      .catch((err: unknown) => {
        if (!alive) return
        setUsage(null)
        // The server's own sentence, never "something went wrong" (DESIGN.md §8).
        setUsageError(
          errorSentence(err, 'The usage ledger did not answer. Check the backend is running.'),
        )
      })
      .finally(() => {
        if (alive) setUsageLoaded(true)
      })
    return () => {
      alive = false
    }
  }, [token, hydrated, windowKey, session?.role])

  // ── whether the Superset add-on is there at all ────────────────────────────
  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const resolved = await getAnalyticsStatus()
        if (!alive) return
        setStatus(resolved)
        if (resolved.boards > 0) {
          const catalogue = await getAnalyticsBoards()
          if (!alive) return
          setBoards(catalogue.boards)
          setWindows(catalogue.windows)
        }
      } catch {
        if (alive) setStatus(null)
      } finally {
        if (alive) setStatusLoaded(true)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const series = useMemo(() => usage?.series ?? [], [usage])
  const byModel = useMemo(() => usage?.by_model ?? [], [usage])

  const daily = useMemo(() => toDaily(series), [series])
  const spendOverTime = useMemo(
    () =>
      windowKey === 'month'
        ? daily.map((d) => ({ t: dayLabel(d.day), cost: Number(d.cost.toFixed(4)) }))
        : series.map((p) => ({
            t: `${String(p.ts).slice(11, 13)}h`,
            cost: Number(p.cost_usd.toFixed(4)),
          })),
    [windowKey, daily, series],
  )

  const mix: DonutDatum[] = useMemo(() => {
    const slices = modelMix(byModel)
    return slices.map((slice, i) => ({
      name: slice.name,
      value: slice.value,
      color: 'graph' as const,
      hex: rampHex(i, slices.length),
    }))
  }, [byModel])

  const rates = useMemo(() => unitCosts(byModel), [byModel])
  const weekday = useMemo(() => weekdayProfile(series), [series])
  const hours = useMemo(() => hourProfile(series), [series])
  const tokens = useMemo(() => totalTokens(byModel), [byModel])

  const state = analyticsState(status)
  const supersetReady = statusLoaded && state === 'ready' && boards.length > 0
  const hasLedger = usageLoaded && series.length > 0

  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        as="h1"
        eyebrow="analytics · metered usage"
        title="Analytics"
        right={
          <div className="flex items-center gap-1 rounded-lg border border-border p-1">
            {WINDOWS.map((w) => (
              <button
                key={w.key}
                type="button"
                onClick={() => setWindowKey(w.key)}
                aria-pressed={windowKey === w.key}
                className={
                  windowKey === w.key
                    ? 'rounded-md bg-surface-2 px-3 py-1.5 text-sm font-medium text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                    : 'rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors duration-[var(--dur-fast)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                }
              >
                {w.label}
              </button>
            ))}
          </div>
        }
      />

      {usageError !== null ? (
        <Card>
          <CardBody>
            <Absence
              figure="Metered usage"
              why={usageError}
              needed="A reachable Aegis backend and a session that may read /admin/usage."
            />
          </CardBody>
        </Card>
      ) : !usageLoaded ? (
        <Card>
          <CardBody>
            <p role="status" className="py-8 text-center text-sm text-muted-foreground">
              Reading the usage ledger…
            </p>
          </CardBody>
        </Card>
      ) : !hasLedger ? (
        <Card>
          <CardBody>
            <Absence
              figure="Every chart on this page"
              why="The usage ledger holds no metered call in this window."
              needed="One gateway call. Every call writes a ledger row; widen the window if the traffic is older."
            />
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── The four figures every other chart is a decomposition of ───── */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Metered spend"
              value={usd(usage?.total_cost_usd ?? null)}
              icon={Coins}
              tone="graph"
              trend={daily.length > 1 ? daily.map((d) => d.cost) : undefined}
            />
            <StatCard label="Tokens" value={compact(tokens)} icon={Cpu} tone="ml" />
            <StatCard
              label="Models in play"
              value={String(byModel.length)}
              icon={GaugeIcon}
              tone="agent"
            />
            <StatCard
              label={windowKey === 'month' ? 'Days with traffic' : 'Hours with traffic'}
              value={String(windowKey === 'month' ? daily.length : series.length)}
              icon={CalendarClock}
              tone="ok"
            />
          </div>

          {/* ── Row A — the shape of the spend, and what carries it ────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                title={windowKey === 'month' ? 'Spend per day' : 'Spend per hour'}
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Clock4 className="size-3" aria-hidden />
                    {spendOverTime.length} buckets
                  </Badge>
                }
              />
              <CardBody className="space-y-3 pt-0">
                <AreaChart
                  data={spendOverTime}
                  index="t"
                  category="cost"
                  color="graph"
                  valueFormatter={usd}
                  axisFormatter={(v) => `$${v.toFixed(0)}`}
                  height={240}
                />
                <Receipt
                  origin={`GET /admin/usage?window=${windowKey} · usage_ledger`}
                  detail="Buckets are summed, never averaged — an hour with no rows is an hour nobody used."
                />
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Spend by model" />
              <CardBody className="space-y-3 pt-0">
                <DonutChart
                  data={mix}
                  centerLabel={usd(usage?.total_cost_usd ?? null)}
                  centerSub="metered"
                  valueFormatter={usd}
                  height={190}
                />
                <Receipt
                  origin="GET /admin/usage · by_model"
                  detail="Past the ramp’s four validated steps the tail is one named band, not a fifth colour."
                />
              </CardBody>
            </Card>
          </div>

          {/* ── Row B — unit economics and the two rhythms ─────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader title="Cost per 1k tokens" />
              <CardBody className="space-y-3 pt-0">
                {rates.length > 0 ? (
                  <>
                    <RankedBars
                      label="Blended cost per thousand tokens, by model, dearest first"
                      data={rates.map((r) => ({
                        name: shortModel(r.model),
                        value: r.usdPer1kTokens,
                      }))}
                      valueFormatter={(v) => `$${v.toFixed(4)}`}
                      maxRows={6}
                      tail="omit"
                    />
                    <Receipt
                      origin="GET /admin/usage · cost_usd ÷ tokens"
                      detail="The rate actually paid, not the list price. Deployments billed by second or frame are excluded."
                    />
                  </>
                ) : (
                  <Absence
                    figure="Cost per 1k tokens"
                    why="No model in this window reported a token count."
                    needed="One text completion. Audio and image deployments bill on other units."
                  />
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Weekday rhythm" />
              <CardBody className="space-y-3 pt-0">
                {weekday.length > 0 ? (
                  <>
                    <BarChart
                      data={weekday}
                      index="label"
                      category="cost"
                      color="agent"
                      valueFormatter={usd}
                      height={200}
                    />
                    <Receipt
                      origin="GET /admin/usage · daily buckets"
                      detail="Mean per day of week, so a window holding five Mondays and four Tuesdays still compares."
                    />
                  </>
                ) : (
                  <Absence
                    figure="Weekday rhythm"
                    why="The window holds fewer than one full day of buckets."
                    needed="A day of traffic. Switch the window to 30 days."
                  />
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Hour of day" />
              <CardBody className="space-y-3 pt-0">
                <BarChart
                  data={hours}
                  index="label"
                  category="cost"
                  color="graph"
                  valueFormatter={usd}
                  height={200}
                />
                <Receipt
                  origin="GET /admin/usage · hourly buckets, UTC"
                  detail="Total per hour across the window. An hour with no ledger row is absent, not zero."
                />
              </CardBody>
            </Card>
          </div>
        </>
      )}

      {/* ── The optional BI add-on, present or stated ───────────────────────── */}
      {!statusLoaded ? (
        <Card>
          <CardBody>
            <p role="status" className="text-sm text-muted-foreground">
              Checking whether Superset is answering…
            </p>
          </CardBody>
        </Card>
      ) : supersetReady ? (
        <SupersetBoards status={status} boards={boards} windows={windows} />
      ) : (
        <SupersetOffPanel status={status} state={state === 'ready' ? 'empty' : state} />
      )}
    </div>
  )
}

/** Client entry for the Analytics section — gated on a reachable backend. */
export function AnalyticsMount(): ReactElement {
  return (
    <BackendGate>
      <AnalyticsView />
    </BackendGate>
  )
}
