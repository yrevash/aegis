'use client'

import Link from 'next/link'
import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  ChevronRight,
  Coins,
  DatabaseZap,
  GitBranch,
  Landmark,
  Loader2,
  PiggyBank,
  ShieldCheck,
  Target,
  Timer,
  XCircle,
  Zap,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { rampHex } from '@/components/charts/palette'
import { StackedArea } from '@/components/charts/StackedArea'
import { Figure } from '@/components/primitives/Figure'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import {
  getGatewayOptimization,
  getGovernanceDashboard,
  getLatency,
  getSecurityPosture,
  getUsage,
} from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type {
  BudgetStatusRow,
  GatewayOptimizationResponse,
  GovernanceDashboardResponse,
  LatencyResponse,
  PostureStatus,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import type { UsageResponse } from '@/lib/api/types'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import { useMetricsSeries } from '@/state/useMetrics'

import { modelMix, shortModel, stackSpendByTenant, toDaily } from './adminOverview'

// ── formatting helpers ───────────────────────────────────────────────────────

function fmtInt(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? '—' : Math.round(n).toLocaleString('en-US')
}

function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}k`
  return `$${n.toFixed(2)}`
}

function fmtPct(frac: number | null | undefined): string {
  return frac == null || !Number.isFinite(frac) ? '—' : `${Math.round(frac * 100)}%`
}

function fmtMs(ms: number | null | undefined): string {
  return ms == null || !Number.isFinite(ms) ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/**
 * The budget meter's fill, by how much of the cap is gone.
 *
 * Every branch now names one of the three status tokens DESIGN.md §2 actually
 * lists, and names it without a fallback. `--success` (which does exist, and
 * carries the same `#12b76a` as `--ok-ink`) was a second spelling of one colour,
 * and the amber branch shipped a hardcoded `#b45309` fallback that is not the
 * value of `--risk-ink` — so if that fallback had ever been reached it would have
 * painted an off-system hue, silently. One name per colour, no second spellings,
 * no fallbacks that disagree with the token they stand in for.
 */
function meterTone(frac: number): string {
  if (frac >= 0.9) return 'var(--danger)'
  if (frac >= 0.7) return 'var(--risk-ink)'
  return 'var(--ok-ink)'
}

function postureTone(status: PostureStatus | string): BadgeTone {
  switch (status) {
    case 'enforced':
      return 'ok'
    case 'partial':
      return 'risk'
    default:
      return 'block'
  }
}

/**
 * A series worth drawing, or nothing.
 *
 * A sparkline is a claim that the figure has a shape. Two identical samples have
 * no shape — the mark it produces is a horizontal rule under the number, which
 * reads as a chart that failed to load rather than as a counter that has not
 * moved. So a series that is constant (or shorter than two finite points) is
 * withheld, and the tile simply has no sparkline until the figure does something.
 */
function movingSeries(values: readonly number[]): number[] | undefined {
  const finite = values.filter((v) => Number.isFinite(v))
  if (finite.length < 2) return undefined
  return finite.some((v) => v !== finite[0]) ? finite : undefined
}

/** An honest dashed empty-state panel. */
function Empty({ children }: { children: ReactNode }): ReactElement {
  return (
    <p className="rounded-lg border border-dashed border-border bg-surface-2/40 px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </p>
  )
}

// ── the command center ───────────────────────────────────────────────────────

/**
 * Admin command center — the platform's single pane of glass, ordered for what an
 * admin acts on first: alerts, customer/budget health, then
 * the financial and health charts. Every figure is a real projection of a live
 * accessor (`/metrics`, `/gateway/optimization`, `/governance/dashboard`,
 * `/latency`, `/security/posture`); panels with no data yet say so.
 */
function AdminCommandCenter(): ReactElement {
  // Read the live session token. The module-level fallback in `request` is not
  // enough here: `AuthProvider` restores the persisted session in an effect that
  // runs *after* first paint, so on a reload this component's fetch effect would
  // otherwise fire with no bearer and 401 — and, with a constant `null` in the
  // dependency array, never retry once the token arrived.
  const { session, hydrated } = useAuth()
  const token: string | null = session?.token ?? null

  const series = useMetricsSeries(token)
  const metrics = series.latest

  const [opt, setOpt] = useState<GatewayOptimizationResponse | null>(null)
  const [gov, setGov] = useState<GovernanceDashboardResponse | null>(null)
  const [latency, setLatency] = useState<LatencyResponse | null>(null)
  const [posture, setPosture] = useState<SecurityPostureResponse | null>(null)
  // The 30-day ledger roll-up, platform-wide. `gov.usage` carries the same shape
  // for the *day* window only, which is fifteen hourly buckets — enough for a
  // number, not enough for a shape. This is the series the trend and the stack
  // are drawn from.
  const [ledger, setLedger] = useState<UsageResponse | null>(null)
  // Per-tenant slices of the same window, keyed by tenant id. Fetched in a second
  // pass because the tenant list only exists once `/governance/dashboard` lands.
  const [tenantLedger, setTenantLedger] = useState<Map<number, UsageResponse>>(new Map())
  // True once every accessor above has settled (fulfilled *or* rejected), so a
  // failed fetch resolves the spinner into the honest per-panel empty states
  // instead of leaving it spinning forever.
  const [settled, setSettled] = useState(false)

  useEffect(() => {
    // Wait for the persisted session to hydrate; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    const set = <T,>(fn: (v: T) => void) => (v: T) => {
      if (alive) fn(v)
    }
    void Promise.allSettled([
      getGatewayOptimization(token).then(set(setOpt)),
      getGovernanceDashboard(token).then(set(setGov)),
      getLatency(token).then(set(setLatency)),
      // Posture describes the DEPLOYMENT — one reading across every tenant that shared
      // the worker — so `require_infra_reader` refuses a tenant-pinned principal. This
      // dashboard serves both admin portals, and a tenant admin is pinned, so asking
      // unconditionally guaranteed a 403 on their overview every single load. The gate
      // is the tenant pin, never the role name.
      ...(session?.tenantId == null
        ? [getSecurityPosture(token).then(set(setPosture))]
        : []),
      getUsage(token, { window: 'month' }).then(set(setLedger)),
    ]).then(() => {
      if (alive) setSettled(true)
    })
    return () => {
      alive = false
    }
  }, [token, hydrated, session?.tenantId])

  // ── the per-tenant slices, once the tenant list is known ─────────────────────
  const tenantIds = useMemo(() => (gov?.tenants ?? []).map((t) => t.id), [gov])
  const tenantKey = tenantIds.join(',')
  useEffect(() => {
    if (!hydrated || tenantIds.length === 0) return
    let alive = true
    void Promise.allSettled(
      tenantIds.map((id) =>
        getUsage(token, { tenantId: id, window: 'month' }).then((u) => [id, u] as const),
      ),
    ).then((results) => {
      if (!alive) return
      const next = new Map<number, UsageResponse>()
      for (const r of results) if (r.status === 'fulfilled') next.set(r.value[0], r.value[1])
      setTenantLedger(next)
    })
    return () => {
      alive = false
    }
    // `tenantKey` is the stable identity of `tenantIds`; the array is rebuilt on
    // every `gov` change and would otherwise refire this on every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, hydrated, tenantKey])

  const summary = opt?.summary ?? null
  const usage = gov?.usage ?? null

  // ── business figures ─────────────────────────────────────────────────────────
  const costSaved = summary?.cost_saved_usd ?? metrics?.cost_saved_usd ?? null
  const baseline = summary?.baseline_cost_usd ?? metrics?.baseline_cost_usd ?? null
  const savingsPct = costSaved != null && baseline != null && baseline > 0 ? costSaved / baseline : null
  const totalSpend = usage?.total_cost_usd ?? summary?.total_cost_usd ?? null
  const queries = summary?.total_calls ?? usage?.calls ?? null
  const quality = metrics?.quality_score ?? null
  const cacheHit = metrics?.cache_hit_rate ?? null
  const smallShare = summary?.small_model_share ?? metrics?.small_model_share ?? null
  const p95 = latency?.run_p95_ms ?? null

  // The two figures that accumulate inside this process carry the polled
  // `/metrics` window as their sparkline. It is short — one point per poll since
  // the page opened — so it is passed only once there are two points to draw a
  // segment between; a one-point "trend" is a flat line that means nothing.
  const savedTrend = useMemo(
    () => movingSeries(series.history.map((m) => m.cost_saved_usd)),
    [series.history],
  )
  const callsTrend = useMemo(
    () => movingSeries(series.history.map((m) => m.total_calls)),
    [series.history],
  )

  // ── the 30-day daily spend series, from the ledger's own buckets ─────────────
  const daily = useMemo(() => toDaily(ledger?.series ?? []), [ledger])
  const spendTrend = useMemo(() => daily.map((d) => d.cost), [daily])
  const ledgerSpend = ledger?.total_cost_usd ?? null

  // ── spend over time, stacked by who it was billed to ─────────────────────────
  const stacked = useMemo(
    () =>
      stackSpendByTenant(
        ledger?.series ?? [],
        (gov?.tenants ?? []).map((t) => ({
          id: t.id,
          name: t.name,
          series: tenantLedger.get(t.id)?.series ?? [],
        })),
      ),
    [ledger, gov, tenantLedger],
  )
  const stackReady = stacked.rows.length > 0 && stacked.bands.length > 0

  // ── model mix by real ledger spend, folded to the ramp's four steps ──────────
  const mix: DonutDatum[] = useMemo(() => {
    const slices = modelMix(ledger?.by_model ?? [])
    return slices.map((slice, i) => ({
      name: slice.name,
      value: slice.value,
      color: 'graph' as const,
      hex: rampHex(i, slices.length),
    }))
  }, [ledger])
  const topModel = useMemo(() => modelMix(ledger?.by_model ?? [])[0] ?? null, [ledger])

  // ── model routing — which deployment each role lands on ──────────────────────
  const routing = useMemo(() => {
    const table =
      metrics?.routing && Object.keys(metrics.routing).length ? metrics.routing : opt?.config.routing ?? {}
    return Object.entries(table).map(([role, model]) => ({ role, model: shortModel(model) }))
  }, [metrics, opt])

  // ── tenant + budget health (top customers by spend) ──────────────────────────
  const budgetByTenant = useMemo(() => {
    // `/governance/dashboard` sends budgets keyed by **scope**, not by a
    // `tenant_id` field: a tenant cap arrives as `scope_type: "tenant"` with the
    // tenant in `scope_id`, and a per-user cap as `scope_type: "user"`. Reading
    // `budget.tenant_id` — which the response does not carry — matched nothing,
    // so every customer row rendered "— calls" and "no cap" while real caps sat
    // in the payload. The scope is read first and `tenant_id` kept as a fallback
    // for any deployment that does send it.
    const map = new Map<number, BudgetStatusRow>()
    for (const b of gov?.budgets ?? []) {
      const id =
        b.budget.tenant_id ?? (b.budget.scope_type === 'tenant' ? b.budget.scope_id : null)
      if (id != null) map.set(id, b)
    }
    return map
  }, [gov])
  const tenants = useMemo(() => gov?.tenants ?? [], [gov])
  const topTenants = useMemo(
    () =>
      tenants
        .map((t) => ({ t, b: budgetByTenant.get(t.id) }))
        .sort((a, b) => (b.b?.cost_usd_used ?? 0) - (a.b?.cost_usd_used ?? 0))
        .slice(0, 5),
    [tenants, budgetByTenant],
  )

  // ── security posture rollup — as a pie ───────────────────────────────────────
  const postureCounts = useMemo(() => {
    const c: Record<string, number> = { enforced: 0, partial: 0, not_covered: 0 }
    for (const e of posture?.entries ?? []) c[e.status] = (c[e.status] ?? 0) + 1
    return c
  }, [posture])
  /**
   * The posture roll-up, as three labelled states rather than three slices.
   *
   * `scripts/validate_palette.js` fails this trio as a categorical palette, and
   * DESIGN.md §2 says that is correct and expected: amber against red separates by
   * only **ΔE 7.0 under deuteranopia — and ΔE 10.5 for normal vision**, which is
   * below the floor of 15, so full-colour readers cannot reliably tell them apart
   * either. The status hues are reserved to mean a *state*, and they always ship
   * with an icon and a word.
   *
   * A donut cannot honour that, because a donut's only route from a slice to its
   * name is the colour — the very encoding that fails here. So each state is now
   * its own row carrying its icon, its word and its count, and the hue is the
   * third cue rather than the only one.
   */
  const securityMix = useMemo(
    () =>
      (
        [
          { name: 'Enforced', value: postureCounts.enforced, tone: 'ok', Icon: CheckCircle2 },
          { name: 'Partial', value: postureCounts.partial, tone: 'risk', Icon: AlertTriangle },
          { name: 'Not covered', value: postureCounts.not_covered, tone: 'block', Icon: XCircle },
        ] as const
      ).filter((row) => row.value > 0),
    [postureCounts],
  )
  const postureTotal = postureCounts.enforced + postureCounts.partial + postureCounts.not_covered
  const coverage = postureTotal > 0 ? postureCounts.enforced / postureTotal : null
  const topGap = (posture?.entries ?? []).find((e) => e.status !== 'enforced')

  // ── latency — a positive, all-green read-out ─────────────────────────────────
  const latencyBars = useMemo(() => {
    if (!latency || latency.empty) return []
    return [
      { stage: 'p50', ms: Math.round(latency.run_p50_ms ?? 0) },
      { stage: 'p95', ms: Math.round(latency.run_p95_ms ?? 0) },
      { stage: 'max', ms: Math.round(latency.run_max_ms ?? 0) },
    ]
  }, [latency])

  // ── derived alerts ───────────────────────────────────────────────────────────
  const alerts = useMemo(() => {
    const out: { tone: BadgeTone; text: string }[] = []
    for (const t of tenants) {
      const b = budgetByTenant.get(t.id)
      const cap = b?.budget.usd_cap ?? null
      const spent = b?.cost_usd_used ?? null
      if (cap != null && cap > 0 && spent != null) {
        const frac = spent / cap
        if (frac >= 1) out.push({ tone: 'block', text: `${t.name} is over budget (${fmtUsd(spent)} / ${fmtUsd(cap)})` })
        else if (frac >= 0.8) out.push({ tone: 'risk', text: `${t.name} nearing budget (${fmtPct(frac)} of cap)` })
      }
    }
    if (postureCounts.not_covered > 0)
      out.push({ tone: 'block', text: `${postureCounts.not_covered} security control(s) not covered` })
    if (postureCounts.partial > 0)
      out.push({ tone: 'risk', text: `${postureCounts.partial} security control(s) only partial` })
    return out
  }, [tenants, budgetByTenant, postureCounts])

  const loading = !settled && opt == null && gov == null && metrics == null

  return (
    <div className="space-y-6">
      {/* Header — no explainer prose; admins know the surface. */}
      <SectionHeader as="h1" eyebrow="platform · command center" title="Admin overview" />

      {loading ? (
        <Card>
          <CardBody>
            <div
              role="status"
              className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground"
            >
              <Loader2 className="size-4 motion-safe:animate-spin" aria-hidden />
              Loading the command center…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── A · Business KPI band ──────────────────────────────────────────────
              Seven tiles in a four-column grid left three dead cells in the second
              row, which reads as a page that ran out of things to say. The lead
              tile takes two columns instead, so eight slots hold seven tiles and
              both rows close. The `trend` sparkline is only passed where a real
              series exists behind the figure — the ledger's own 30 daily buckets
              for spend, the polled `/metrics` window for the two counters that
              accumulate in-process. The rest are single samples and stay flat by
              omission rather than by invention. ── */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              className="col-span-2"
              label="Total spend, 30 days"
              value={fmtUsd(ledgerSpend ?? totalSpend)}
              icon={Coins}
              tone="graph"
              trend={movingSeries(spendTrend)}
              source={
                ledgerSpend != null
                  ? `GET /admin/usage · ${daily.length} days of the usage ledger`
                  : 'GET /governance/dashboard · today'
              }
            />
            <StatCard
              label="Cost saved vs frontier"
              value={fmtUsd(costSaved)}
              icon={PiggyBank}
              tone="ok"
              delta={savingsPct != null ? { value: `${fmtPct(savingsPct)} saved`, direction: 'up' } : undefined}
              trend={savedTrend}
            />
            <StatCard
              label="Queries served"
              value={fmtInt(queries)}
              icon={Zap}
              tone="agent"
              trend={callsTrend}
            />
            <StatCard label="Small-model share" value={fmtPct(smallShare)} icon={GitBranch} tone="graph" />
            <StatCard label="Cache hit rate" value={fmtPct(cacheHit)} icon={DatabaseZap} tone="ok" />
            <StatCard label="Quality score" value={fmtPct(quality)} icon={Target} tone="ml" />
            <StatCard label="p95 latency" value={fmtMs(p95)} icon={Timer} tone="graph" />
          </div>

          {/* ── B · Needs attention: alerts ────────────────────────────────────── */}
          <div>
            <Card
              className={
                alerts.length > 0
                  ? 'border-[color:var(--danger)]/40 bg-[color:var(--danger)]/[0.04] ring-1 ring-[color:var(--danger)]/20'
                  : undefined
              }
            >
              <CardHeader
                title="Alerts"
                actions={
                  <Badge tone={alerts.length > 0 ? 'block' : 'ok'} className="gap-1.5">
                    <AlertTriangle className="size-3" aria-hidden />
                    {alerts.length}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {alerts.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-lg border border-dashed border-border bg-surface-2/40 px-4 py-6 text-sm text-muted-foreground">
                    <CheckCircle2 className="size-4 text-ok-ink" aria-hidden />
                    All clear — nothing needs attention.
                  </div>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {alerts.map((a, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2.5 rounded-lg border border-border bg-card px-3.5 py-2.5"
                      >
                        <span
                          className="mt-1.5 size-2.5 shrink-0 rounded-full"
                          style={{
                            background:
                              a.tone === 'block'
                                ? 'var(--danger)'
                                : a.tone === 'risk'
                                  ? 'var(--risk-ink)'
                                  : 'var(--muted-foreground)',
                          }}
                          aria-hidden
                        />
                        <span className="text-[0.86rem] font-medium text-foreground">{a.text}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>

          </div>

          {/* ── C · Customers & budgets (summary → own page) ───────────────────── */}
          <Card>
            <CardHeader
              title="Customers & budgets"
              actions={
                <Link
                  href={`/app/${session?.fineRole ?? 'tenant_admin'}/governance`}
                  className="inline-flex items-center gap-1 text-[0.78rem] font-medium text-primary hover:underline"
                >
                  View all customers <ChevronRight className="size-3.5" aria-hidden />
                </Link>
              }
            />
            <CardBody className="pt-0">
              {topTenants.length === 0 ? (
                <Empty>No customer data yet.</Empty>
              ) : (
                <ul className="flex flex-col divide-y divide-border/70">
                  {topTenants.map(({ t, b }) => {
                    const cap = b?.budget.usd_cap ?? null
                    const spent = b?.cost_usd_used ?? null
                    const frac = cap != null && cap > 0 && spent != null ? Math.min(1, spent / cap) : null
                    return (
                      <li key={t.id} className="flex items-center gap-4 py-3 first:pt-0 last:pb-0">
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-surface-2">
                          <Landmark className="size-4 text-muted-foreground" aria-hidden />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-foreground">{t.name}</p>
                          <p className="font-mono text-[0.6875rem] text-muted-foreground">
                            {fmtInt(b?.calls ?? null)} calls
                          </p>
                        </div>
                        {/* At 320px an icon, a truncating name and a 160px meter
                            leave the name nothing; the meter narrows first. */}
                        <div className="w-28 shrink-0 sm:w-40">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="tabular text-sm text-foreground">{fmtUsd(spent)}</span>
                            <span className="tabular font-mono text-[0.6875rem] text-muted-foreground">
                              / {cap != null ? fmtUsd(cap) : 'no cap'}
                            </span>
                          </div>
                          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                            {frac != null ? (
                              <div
                                className="h-full rounded-full"
                                style={{ width: `${Math.max(2, Math.round(frac * 100))}%`, background: meterTone(frac) }}
                              />
                            ) : null}
                          </div>
                        </div>
                      </li>
                    )
                  })}
                </ul>
              )}
            </CardBody>
          </Card>

          {/* ── D · Financials — where the money went, over time and by model ────
              Both cards were `RankedBars`: a horizontal bar per row, which is a
              fine mark and the wrong one here. Spend has a *shape* over thirty
              days that a ranked list cannot show, and a model's share of a total
              is a part-of-whole question that a bar answers only by arithmetic.
              The stack answers the first, the donut the second, and neither
              invents a category the ledger did not report. ── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                title="Daily spend, by who it was billed to"
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Coins className="size-3" aria-hidden />
                    {daily.length} days
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {stackReady ? (
                  <div className="flex flex-col gap-3">
                    <StackedArea
                      data={stacked.rows}
                      index="day"
                      series={stacked.bands.map((b) => ({ key: b.key, label: b.label, value: b.total }))}
                      valueFormatter={(v) => fmtUsd(v)}
                      height={240}
                    />
                    <Receipt
                      origin="GET /admin/usage · one call per tenant, same window"
                      detail="Bands sum to the platform total; untenanted work is its own band, not dropped."
                    />
                  </div>
                ) : daily.length > 0 ? (
                  <Empty>Spend is recorded, but no tenant attribution has landed yet.</Empty>
                ) : (
                  <Absence
                    figure="Daily spend"
                    why="The usage ledger has no rows in the last 30 days, so there is nothing to bucket."
                    needed="One metered model call. Every gateway call writes a ledger row."
                  />
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Model mix" />
              <CardBody className="pt-0">
                {mix.length > 0 ? (
                  <div className="flex flex-col gap-4">
                    <DonutChart
                      data={mix}
                      centerLabel={fmtUsd(ledgerSpend)}
                      centerSub="30-day spend"
                      valueFormatter={(v) => fmtUsd(v)}
                      height={190}
                    />
                    {topModel != null && (
                      <Receipt
                        origin="GET /admin/usage · by_model"
                        detail={`${topModel.name} carries ${fmtPct(topModel.share)} of it.`}
                      />
                    )}
                  </div>
                ) : (
                  <Absence
                    figure="Spend by model"
                    why="No priced model call is recorded in the window."
                    needed="One metered call. Unpriced deployments are excluded rather than counted at zero."
                  />
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── E · Routing — a mapping, so a table rather than a chart ───────────
              This was a bar chart of "roles per deployment", which measured the
              length of a `Object.values(...).length` and nothing a reader wants:
              the question is never *how many* roles land on a model, it is *which*
              one each role lands on. That is a two-column mapping, and DESIGN.md
              §4 is explicit that anything countable is a table. ── */}
          <Card>
            <CardHeader
              title="Model routing"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <GitBranch className="size-3" aria-hidden />
                  {routing.length} roles
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {routing.length > 0 ? (
                <ul className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
                  {routing.map(({ role, model }) => (
                    // `min-w-0` on the grid child, not only on the truncating span inside
                    // it: a grid item's automatic minimum is its content's min-content
                    // width, so the `1fr` track floors at the widest model id and the whole
                    // page scrolls sideways rather than the id ellipsing. Invisible at
                    // 390px until the text-size control was raised a step, then a 56px
                    // document scroll.
                    <li
                      key={role}
                      className="flex min-w-0 items-center gap-2.5 rounded-md border border-border bg-surface-2/50 px-3 py-2"
                    >
                      <span className="shrink-0 text-sm font-medium text-foreground">{role}</span>
                      <ChevronRight className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                      {/* The clip has to be *inside* the Figure. This span carried
                          `truncate`, but the Figure it wraps is an `inline-flex`, and
                          `text-overflow` does not cross a flex formatting context — so
                          the model id was cut mid-glyph with no ellipsis and no
                          measurable document overflow (`DeepSeek-V4-Fl`,
                          `Llama-3.2-90B-Visi`). `block` so `max-w-full` resolves against
                          this slot rather than the whole row. */}
                      <span className="block min-w-0" title={model}>
                        <Figure truncate className="text-[0.78rem] text-muted-foreground">
                          {model}
                        </Figure>
                      </span>
                    </li>
                  ))}
                </ul>
              ) : (
                <Empty>Routing table unavailable.</Empty>
              )}
            </CardBody>
          </Card>

          {/* ── F · Health: security posture + latency (positive/green) ────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="Security posture"
                actions={
                  <Badge tone={postureCounts.not_covered > 0 ? 'block' : postureCounts.partial > 0 ? 'risk' : 'ok'} className="gap-1.5">
                    <ShieldCheck className="size-3" aria-hidden />
                    {coverage != null ? `${fmtPct(coverage)} covered` : '—'}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {securityMix.length > 0 ? (
                  <div className="flex flex-col gap-4">
                    <div>
                      <p className="eyebrow">enforced</p>
                      <p className="mt-1">
                        <Figure size="stat">{coverage != null ? fmtPct(coverage) : '—'}</Figure>
                      </p>
                    </div>
                    <ul className="flex flex-col gap-2">
                      {securityMix.map(({ name, value, tone, Icon }) => (
                        <li key={name} className="flex items-center gap-2.5 text-sm">
                          <Icon className={cn('size-4 shrink-0', SIGNALS[tone].text)} aria-hidden />
                          <span className="min-w-0 flex-1 truncate text-foreground">{name}</span>
                          <Figure className="shrink-0 font-medium">
                            {value} {value === 1 ? 'control' : 'controls'}
                          </Figure>
                        </li>
                      ))}
                    </ul>
                    {topGap ? (
                      <div className="flex items-center justify-between gap-2 rounded-lg border border-border bg-surface-2/40 px-3 py-2 text-[0.78rem]">
                        <span className="truncate text-foreground">Top gap: {topGap.name}</span>
                        <Badge tone={postureTone(topGap.status)} className="font-mono">
                          {topGap.status}
                        </Badge>
                      </div>
                    ) : (
                      <p className="text-center text-[0.78rem] text-ok-ink">All mapped controls enforced.</p>
                    )}
                  </div>
                ) : (
                  <Empty>Posture unavailable.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                title="Latency"
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Activity className="size-3" aria-hidden />
                    {fmtInt(latency?.run_count ?? null)} runs
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {/*
                  Three quantiles of one distribution are three numbers, not a
                  chart: the bars carried no comparison a reader could not make
                  faster from the figures themselves, and they were drawn in the
                  reserved status green, which reads as a verdict on the latency.
                  The verdict is gone too — "Healthy" and "well within a
                  responsive envelope" were editorial, with no threshold behind
                  them and nothing on the response to source them to.
                */}
                {latencyBars.length > 0 ? (
                  <div className="flex flex-col gap-4">
                    <dl className="grid grid-cols-3 gap-4">
                      {latencyBars.map((bar) => (
                        <div key={bar.stage}>
                          <dt className="eyebrow">{bar.stage}</dt>
                          <dd className="mt-1">
                            <Figure size="stat">{fmtMs(bar.ms)}</Figure>
                          </dd>
                        </div>
                      ))}
                    </dl>
                    <Receipt
                      origin="GET /latency · whole-run duration, this process"
                      detail={`${fmtInt(latency?.run_count ?? null)} runs in the window`}
                    />
                  </div>
                ) : (
                  <Absence
                    figure="Run latency, at any percentile"
                    why="No run has completed in this process yet, so there is no distribution to take a percentile of."
                    needed="One completed run. The window is per-process and resets when the backend restarts."
                  />
                )}
              </CardBody>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

/** Client entry for the admin Overview section — gated on a reachable backend. */
export function AdminDashboardMount(): ReactElement {
  return (
    <BackendGate>
      <TooltipProvider>
        <AdminCommandCenter />
      </TooltipProvider>
    </BackendGate>
  )
}
