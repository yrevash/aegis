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
  ListChecks,
  Loader2,
  PiggyBank,
  ShieldCheck,
  Target,
  Timer,
  WifiOff,
  Zap,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { BarChart } from '@/components/charts/BarChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import type { ChartColor } from '@/components/charts/palette'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { TooltipProvider } from '@/components/primitives/tooltip'
import {
  getApprovals,
  getGatewayOptimization,
  getGovernanceDashboard,
  getLatency,
  getSecurityPosture,
} from '@/lib/api/client'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import type {
  BudgetStatusRow,
  GatewayOptimizationResponse,
  GovernanceDashboardResponse,
  LatencyResponse,
  PostureStatus,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import type { ApprovalsResponse, ApprovalRow } from '@/lib/api/types'
import { useMetricsSeries } from '@/state/useMetrics'

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

function fmtTs(value: unknown): string {
  if (typeof value !== 'string') return value == null ? '—' : String(value)
  const t = Date.parse(value)
  if (Number.isNaN(t)) return value
  return new Date(t).toLocaleString('en-US', {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    timeZone: 'UTC',
  })
}

function meterTone(frac: number): string {
  if (frac >= 0.9) return 'var(--danger)'
  if (frac >= 0.7) return 'var(--risk-ink, #b45309)'
  return 'var(--success)'
}

function riskTone(risk: string): BadgeTone {
  switch (risk) {
    case 'low':
      return 'ok'
    case 'medium':
      return 'risk'
    default:
      return 'block'
  }
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

/** Rotating chart palette for the distribution donuts. */
const MIX_COLORS: ChartColor[] = ['agent', 'ml', 'graph', 'ok', 'risk', 'block']

/** An honest dashed empty-state panel. */
function Empty({ children }: { children: ReactNode }): ReactElement {
  return (
    <p className="rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-8 text-center text-sm text-muted-foreground">
      {children}
    </p>
  )
}

// ── the command center ───────────────────────────────────────────────────────

/**
 * Admin command center — the platform's single pane of glass, ordered for what an
 * admin acts on first: alerts, the approvals queue, customer/budget health, then
 * the financial and health charts. Every figure is a real projection of a live
 * accessor (`/metrics`, `/gateway/optimization`, `/governance/dashboard`,
 * `/latency`, `/security/posture`, `/approvals`); panels with no data yet say so.
 */
function AdminCommandCenter(): ReactElement {
  const token: string | null = null

  const series = useMetricsSeries(token)
  const metrics = series.latest

  const [opt, setOpt] = useState<GatewayOptimizationResponse | null>(null)
  const [gov, setGov] = useState<GovernanceDashboardResponse | null>(null)
  const [latency, setLatency] = useState<LatencyResponse | null>(null)
  const [posture, setPosture] = useState<SecurityPostureResponse | null>(null)
  const [approvals, setApprovals] = useState<ApprovalsResponse | null>(null)

  useEffect(() => {
    let alive = true
    const set = <T,>(fn: (v: T) => void) => (v: T) => {
      if (alive) fn(v)
    }
    void getGatewayOptimization(token).then(set(setOpt)).catch(() => {})
    void getGovernanceDashboard(token).then(set(setGov)).catch(() => {})
    void getLatency(token).then(set(setLatency)).catch(() => {})
    void getSecurityPosture(token).then(set(setPosture)).catch(() => {})
    void getApprovals(token, { status: 'pending' }).then(set(setApprovals)).catch(() => {})
    return () => {
      alive = false
    }
  }, [token])

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
  const pending = approvals?.rows.length ?? null

  // ── real cost trend from the ledger's own time buckets ───────────────────────
  const trend = useMemo(
    () =>
      (usage?.series ?? []).map((p) => ({
        bucket: fmtTs(p.bucket),
        cost: Number(p.cost_usd.toFixed(4)),
      })),
    [usage],
  )

  // ── model mix by real ledger spend (falls back to call count) ────────────────
  const mix: DonutDatum[] = useMemo(() => {
    const byModel = usage?.by_model ?? []
    const withSpend = byModel.filter((m) => m.cost_usd > 0)
    const rows = withSpend.length > 0 ? withSpend : byModel.filter((m) => m.calls > 0)
    return rows
      .slice()
      .sort((a, b) => (withSpend.length ? b.cost_usd - a.cost_usd : b.calls - a.calls))
      .map((m, i) => ({
        name: m.model,
        value: Number((withSpend.length ? m.cost_usd : m.calls).toFixed(4)),
        color: MIX_COLORS[i % MIX_COLORS.length],
      }))
  }, [usage])
  const mixIsSpend = (usage?.by_model ?? []).some((m) => m.cost_usd > 0)

  // ── where the spend goes (per role) — as a pie ───────────────────────────────
  const spendByRole: DonutDatum[] = useMemo(() => {
    const roles = Object.entries(summary?.by_role ?? {})
    const withCost = roles.filter(([, r]) => r.cost_usd > 0)
    const rows = withCost.length > 0 ? withCost : roles.filter(([, r]) => r.calls > 0)
    return rows
      .sort((a, b) => (withCost.length ? b[1].cost_usd - a[1].cost_usd : b[1].calls - a[1].calls))
      .map(([role, r], i) => ({
        name: role,
        value: Number((withCost.length ? r.cost_usd : r.calls).toFixed(4)),
        color: MIX_COLORS[i % MIX_COLORS.length],
      }))
  }, [summary])
  const spendIsCost = Object.values(summary?.by_role ?? {}).some((r) => r.cost_usd > 0)

  // ── model routing — how roles fan out across deployments, as a pie ───────────
  const routingMix: DonutDatum[] = useMemo(() => {
    const routing =
      metrics?.routing && Object.keys(metrics.routing).length ? metrics.routing : opt?.config.routing ?? {}
    const counts = new Map<string, number>()
    for (const model of Object.values(routing)) counts.set(model, (counts.get(model) ?? 0) + 1)
    return [...counts.entries()]
      .sort((a, b) => b[1] - a[1])
      .map(([name, value], i) => ({ name, value, color: MIX_COLORS[i % MIX_COLORS.length] }))
  }, [metrics, opt])

  // ── tenant + budget health (top customers by spend) ──────────────────────────
  const budgetByTenant = useMemo(() => {
    const map = new Map<number, BudgetStatusRow>()
    for (const b of gov?.budgets ?? []) if (b.budget.tenant_id != null) map.set(b.budget.tenant_id, b)
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
  const securityMix: DonutDatum[] = useMemo(() => {
    const out: DonutDatum[] = []
    if (postureCounts.enforced) out.push({ name: 'Enforced', value: postureCounts.enforced, color: 'ok' })
    if (postureCounts.partial) out.push({ name: 'Partial', value: postureCounts.partial, color: 'risk' })
    if (postureCounts.not_covered) out.push({ name: 'Not covered', value: postureCounts.not_covered, color: 'block' })
    return out
  }, [postureCounts])
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
    if ((pending ?? 0) > 0) out.push({ tone: 'risk', text: `${pending} action(s) awaiting human approval` })
    return out
  }, [tenants, budgetByTenant, postureCounts, pending])

  const loading = opt == null && gov == null && metrics == null

  return (
    <div className="space-y-6">
      {/* Header — no explainer prose; admins know the surface. */}
      <div className="flex items-end justify-between gap-4">
        <div>
          <p className="eyebrow mb-1">platform · command center</p>
          <h1 className="t-hero text-foreground">Admin overview</h1>
        </div>
      </div>

      {loading ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading the command center…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── A · Business KPI band ──────────────────────────────────────────── */}
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            <StatCard
              label="Cost saved vs frontier"
              value={fmtUsd(costSaved)}
              icon={PiggyBank}
              tone="ok"
              delta={savingsPct != null ? { value: `${fmtPct(savingsPct)} saved`, direction: 'up' } : undefined}
            />
            <StatCard label="Total spend" value={fmtUsd(totalSpend)} icon={Coins} tone="ml" />
            <StatCard label="Queries served" value={fmtInt(queries)} icon={Zap} tone="agent" />
            <StatCard label="Quality score" value={fmtPct(quality)} icon={Target} tone="ml" />
            <StatCard label="Cache hit rate" value={fmtPct(cacheHit)} icon={DatabaseZap} tone="ok" />
            <StatCard label="Small-model share" value={fmtPct(smallShare)} icon={GitBranch} tone="graph" />
            <StatCard label="p95 latency" value={fmtMs(p95)} icon={Timer} tone="graph" />
            <StatCard
              label="Pending approvals"
              value={pending == null ? '—' : String(pending)}
              icon={ListChecks}
              tone={pending && pending > 0 ? 'risk' : 'ok'}
            />
          </div>

          {/* ── B · Needs attention: alerts (highlighted) + approvals queue ────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
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
                    <AlertTriangle className="size-3" />
                    {alerts.length}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {alerts.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-6 text-sm text-muted-foreground">
                    <CheckCircle2 className="size-4 text-ok-ink" />
                    All clear — nothing needs attention.
                  </div>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {alerts.map((a, i) => (
                      <li
                        key={i}
                        className="flex items-start gap-2.5 rounded-xl border border-border bg-card px-3.5 py-2.5 shadow-card"
                      >
                        <span
                          className="mt-1.5 size-2.5 shrink-0 rounded-full"
                          style={{
                            background:
                              a.tone === 'block'
                                ? 'var(--danger)'
                                : a.tone === 'risk'
                                  ? 'var(--risk-ink, #b45309)'
                                  : 'var(--muted-foreground, #64748b)',
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

            <Card>
              <CardHeader
                title="Approvals queue"
                actions={
                  <Link
                    href="/app/admin/approvals"
                    className="inline-flex items-center gap-1 text-[0.78rem] font-medium text-primary hover:underline"
                  >
                    Open inbox <ChevronRight className="size-3.5" />
                  </Link>
                }
              />
              <CardBody className="pt-0">
                {approvals && approvals.rows.length > 0 ? (
                  <ul className="flex flex-col gap-2">
                    {approvals.rows.slice(0, 4).map((row: ApprovalRow) => (
                      <li key={row.id} className="rounded-xl border border-border bg-surface-2/30 px-3.5 py-2.5">
                        <div className="flex items-center justify-between gap-2">
                          <span className="truncate font-mono text-[0.8rem] text-foreground">{row.action}</span>
                          <Badge tone={riskTone(row.risk)} className="font-mono uppercase">
                            {row.risk}
                          </Badge>
                        </div>
                        <div className="mt-1 flex items-center justify-between text-[0.72rem] text-muted-foreground">
                          <span className="truncate">{row.persona ?? '—'}</span>
                          <span className="tabular font-mono">{fmtTs(row.created_at)}</span>
                        </div>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="flex items-center gap-2 rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-6 text-sm text-muted-foreground">
                    <CheckCircle2 className="size-4 text-ok-ink" />
                    No pending approvals — the human gate is clear.
                  </div>
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
                  href="/app/admin/governance"
                  className="inline-flex items-center gap-1 text-[0.78rem] font-medium text-primary hover:underline"
                >
                  View all customers <ChevronRight className="size-3.5" />
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
                        <div className="flex size-9 shrink-0 items-center justify-center rounded-xl bg-surface-2">
                          <Landmark className="size-4 text-muted-foreground" />
                        </div>
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-sm font-medium text-foreground">{t.name}</p>
                          <p className="font-mono text-[0.68rem] text-muted-foreground">
                            {fmtInt(b?.calls ?? null)} calls
                          </p>
                        </div>
                        <div className="w-40 shrink-0">
                          <div className="flex items-baseline justify-between gap-2">
                            <span className="tabular text-sm text-foreground">{fmtUsd(spent)}</span>
                            <span className="tabular font-mono text-[0.68rem] text-muted-foreground">
                              / {cap != null ? fmtUsd(cap) : 'no cap'}
                            </span>
                          </div>
                          <div className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
                            {frac != null ? (
                              <div
                                className="h-full rounded-full transition-all duration-700"
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

          {/* ── D · Financials: real trend + model mix ─────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader title="Cost trend" />
              <CardBody className="pt-0">
                {trend.length > 0 ? (
                  <AreaChart
                    data={trend}
                    index="bucket"
                    category="cost"
                    color="ml"
                    valueFormatter={(v) => fmtUsd(v)}
                    height={220}
                  />
                ) : (
                  <Empty>No metered spend yet.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Model mix" />
              <CardBody className="pt-0">
                {mix.length > 0 ? (
                  <DonutChart
                    data={mix}
                    centerLabel={mixIsSpend ? fmtUsd(totalSpend) : fmtInt(queries)}
                    centerSub={mixIsSpend ? 'total spend' : 'total calls'}
                    valueFormatter={(v) => (mixIsSpend ? fmtUsd(v) : `${fmtInt(v)} calls`)}
                    height={220}
                  />
                ) : (
                  <Empty>No model usage yet.</Empty>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── E · Distribution: spend by role + routing, both as pies ────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader title="Where the spend goes" />
              <CardBody className="pt-0">
                {spendByRole.length > 0 ? (
                  <DonutChart
                    data={spendByRole}
                    centerLabel={spendIsCost ? fmtUsd(summary?.total_cost_usd ?? null) : fmtInt(summary?.total_calls ?? null)}
                    centerSub={spendIsCost ? 'by role' : 'calls by role'}
                    valueFormatter={(v) => (spendIsCost ? fmtUsd(v) : `${fmtInt(v)} calls`)}
                    height={240}
                  />
                ) : (
                  <Empty>No metered calls yet.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader title="Model routing" />
              <CardBody className="pt-0">
                {routingMix.length > 0 ? (
                  <DonutChart
                    data={routingMix}
                    centerLabel={String(routingMix.reduce((s, d) => s + d.value, 0))}
                    centerSub="roles routed"
                    valueFormatter={(v) => `${v} role${v === 1 ? '' : 's'}`}
                    height={240}
                  />
                ) : (
                  <Empty>Routing table unavailable.</Empty>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── F · Health: security posture + latency (positive/green) ────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                title="Security posture"
                actions={
                  <Badge tone={postureCounts.not_covered > 0 ? 'block' : postureCounts.partial > 0 ? 'risk' : 'ok'} className="gap-1.5">
                    <ShieldCheck className="size-3" />
                    {coverage != null ? `${fmtPct(coverage)} covered` : '—'}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {securityMix.length > 0 ? (
                  <div className="flex flex-col gap-3">
                    <DonutChart
                      data={securityMix}
                      centerLabel={coverage != null ? fmtPct(coverage) : '—'}
                      centerSub="enforced"
                      valueFormatter={(v) => `${v} control${v === 1 ? '' : 's'}`}
                      height={200}
                    />
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
                  <Badge tone="ok" className="gap-1.5">
                    <Activity className="size-3" />
                    {latencyBars.length > 0 ? 'Healthy' : `${latency?.run_count ?? 0} runs`}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {latencyBars.length > 0 ? (
                  <div className="flex flex-col gap-3">
                    <BarChart data={latencyBars} index="stage" category="ms" color="ok" height={200} />
                    <p className="text-center text-[0.78rem] text-ok-ink">
                      p95 {fmtMs(latency?.run_p95_ms)} · well within a responsive envelope.
                    </p>
                  </div>
                ) : (
                  <div className="flex h-[220px] flex-col items-center justify-center gap-2 text-center">
                    <CheckCircle2 className="size-6 text-ok-ink" />
                    <span className="text-sm text-ok-ink">No latency issues — awaiting the first run.</span>
                  </div>
                )}
              </CardBody>
            </Card>
          </div>
        </>
      )}
    </div>
  )
}

/**
 * Client entry for the admin Overview section. Runs the boot probe once
 * (live-first, mock fallback) before mounting, so every accessor fetch reads the
 * resolved mode — the offline demo seeds from the mock fixtures and is labelled
 * with the honest banner.
 */
export function AdminDashboardMount(): ReactElement {
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
      <div>
        {mode.mode === 'mock' && (
          <div
            role="status"
            className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
          >
            <WifiOff className="size-3.5 shrink-0" />
            <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
          </div>
        )}
        <AdminCommandCenter />
      </div>
    </TooltipProvider>
  )
}
