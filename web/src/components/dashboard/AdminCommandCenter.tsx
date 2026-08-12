'use client'

import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  Coins,
  DatabaseZap,
  GitBranch,
  Landmark,
  ListChecks,
  Loader2,
  PiggyBank,
  Route,
  ScrollText,
  ShieldCheck,
  Target,
  Timer,
  Users,
  WifiOff,
  Zap,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement, type ReactNode } from 'react'

import { AreaChart } from '@/components/charts/AreaChart'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import type { ChartColor } from '@/components/charts/palette'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
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

import { RoutingTable } from './RoutingTable'

// ── formatting helpers ───────────────────────────────────────────────────────

/** Thousands-grouped integer, or an em-dash for null. */
function fmtInt(n: number | null | undefined): string {
  return n == null || !Number.isFinite(n) ? '—' : Math.round(n).toLocaleString('en-US')
}

/** Compact USD ($1.2k / $3.4M), or an em-dash for null. */
function fmtUsd(n: number | null | undefined): string {
  if (n == null || !Number.isFinite(n)) return '—'
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`
  if (abs >= 1_000) return `$${(n / 1_000).toFixed(1)}k`
  return `$${n.toFixed(2)}`
}

/** A whole-percent from a 0..1 fraction, or an em-dash for null. */
function fmtPct(frac: number | null | undefined): string {
  return frac == null || !Number.isFinite(frac) ? '—' : `${Math.round(frac * 100)}%`
}

/** Milliseconds rendered as seconds with one decimal, or an em-dash. */
function fmtMs(ms: number | null | undefined): string {
  return ms == null || !Number.isFinite(ms) ? '—' : ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

/** A short human timestamp (UTC); passes through non-dates. */
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

/** Meter fill colour by utilisation band — green under 70 %, amber, then red. */
function meterTone(frac: number): string {
  if (frac >= 0.9) return 'var(--danger)'
  if (frac >= 0.7) return 'var(--risk-ink, #b45309)'
  return 'var(--success)'
}

/** RBAC role → an honest badge tone across the four Aegis portals. */
function roleTone(role: string): BadgeTone {
  switch (role) {
    case 'admin':
      return 'ml'
    case 'ai_team':
      return 'agent'
    case 'devops':
      return 'graph'
    default:
      return 'neutral'
  }
}

/** Risk level → badge tone for the approvals queue. */
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

/** Posture status → badge tone. */
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

/** Rotating chart palette for the model-mix donut. */
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
 * Admin command center — the platform's single pane of glass. Every figure is a
 * real projection of an existing accessor endpoint (nothing fabricated):
 *
 * - **Business band** — cost saved, spend, queries, quality, cache, efficiency,
 *   p95, pending approvals (← `/metrics` · `/gateway/optimization` ·
 *   `/governance/dashboard` · `/latency` · `/approvals`).
 * - **Financials** — real cost/token trend (`usage.series`), model-mix donut
 *   (`usage.by_model`), where-the-spend-goes per-role table, routing table.
 * - **Governance** — tenant + budget health, users-by-role rollup.
 * - **Safety & queue** — pending-approvals preview, security-posture summary,
 *   latency SLA.
 * - **Audit & alerts** — recent audit tail + a derived alert feed.
 *
 * Each panel renders its own honest empty state when a source has no data yet,
 * so the offline demo (mock fixtures) and a cold live backend both read true.
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

  // ── business figures (real, with honest fallbacks between equivalent sources) ─
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

  // ── real cost/token trend from the ledger's own time buckets ─────────────────
  const trend = useMemo(
    () =>
      (usage?.series ?? []).map((p) => ({
        bucket: fmtTs(p.bucket),
        cost: Number(p.cost_usd.toFixed(4)),
        tokens: p.tokens,
      })),
    [usage],
  )

  // ── model mix by real ledger spend (falls back to routing count) ─────────────
  const mix: DonutDatum[] = useMemo(() => {
    const byModel = usage?.by_model ?? []
    const withSpend = byModel.filter((m) => m.cost_usd > 0)
    if (withSpend.length > 0) {
      return withSpend
        .slice()
        .sort((a, b) => b.cost_usd - a.cost_usd)
        .map((m, i) => ({ name: m.model, value: Number(m.cost_usd.toFixed(4)), color: MIX_COLORS[i % MIX_COLORS.length] }))
    }
    // No spend yet: show the routing footprint (which models are wired), by call count.
    const byCalls = byModel.filter((m) => m.calls > 0)
    return byCalls.map((m, i) => ({ name: m.model, value: m.calls, color: MIX_COLORS[i % MIX_COLORS.length] }))
  }, [usage])

  // ── tenant + budget health ───────────────────────────────────────────────────
  const budgetByTenant = useMemo(() => {
    const map = new Map<number, BudgetStatusRow>()
    for (const b of gov?.budgets ?? []) {
      if (b.budget.tenant_id != null) map.set(b.budget.tenant_id, b)
    }
    return map
  }, [gov])

  const tenants = useMemo(() => gov?.tenants ?? [], [gov])
  const users = useMemo(() => gov?.users ?? [], [gov])
  const audit = gov?.recent_audit ?? []

  // ── users grouped by role (a rollup, not the full roster) ────────────────────
  const roleCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const u of users) counts.set(u.role, (counts.get(u.role) ?? 0) + 1)
    return [...counts.entries()].sort((a, b) => b[1] - a[1])
  }, [users])

  // ── security posture rollup ──────────────────────────────────────────────────
  const postureCounts = useMemo(() => {
    const c: Record<string, number> = { enforced: 0, partial: 0, not_covered: 0 }
    for (const e of posture?.entries ?? []) c[e.status] = (c[e.status] ?? 0) + 1
    return c
  }, [posture])
  const postureGaps = (posture?.entries ?? []).filter((e) => e.status !== 'enforced').slice(0, 4)

  // ── derived alert feed (composed client-side from the panels above) ──────────
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
    if (latency?.empty) out.push({ tone: 'neutral', text: 'No runs recorded yet — latency telemetry is empty' })
    return out
  }, [tenants, budgetByTenant, postureCounts, pending, latency])

  const loading = opt == null && gov == null && metrics == null

  return (
    <div className="space-y-6">
      {/* Header */}
      <div>
        <p className="eyebrow mb-1">platform · command center</p>
        <h1 className="t-hero text-foreground">Admin overview</h1>
        <p className="mt-2 max-w-2xl text-sm text-muted-foreground">
          Every business, governance and safety figure the platform exposes, in one pane. Numbers
          are read straight from the live accessors — savings and spend from the gateway ledger,
          budgets and audit from governance, p95 from real latency samples. Nothing is fabricated;
          panels with no data yet say so.
        </p>
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
            <StatCard
              label="Pending approvals"
              value={pending == null ? '—' : String(pending)}
              icon={ListChecks}
              tone={pending && pending > 0 ? 'risk' : 'ok'}
            />
            <StatCard label="Quality score" value={fmtPct(quality)} icon={Target} tone="ml" />
            <StatCard label="Cache hit rate" value={fmtPct(cacheHit)} icon={DatabaseZap} tone="ok" />
            <StatCard label="Small-model share" value={fmtPct(smallShare)} icon={GitBranch} tone="graph" />
            <StatCard label="p95 latency" value={fmtMs(p95)} icon={Timer} tone="graph" />
          </div>

          {/* ── B · Financials: real trend + model mix ─────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                eyebrow="aegis.governance · usage.series"
                title="Cost trend"
                description="Ledger spend per time bucket — the real series the accessor rolls up, not a sample."
              />
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
                  <Empty>No metered spend yet — the ledger has no buckets to chart.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="aegis.governance · by_model"
                title="Model mix"
                description="Where spend lands across deployments."
              />
              <CardBody className="pt-0">
                {mix.length > 0 ? (
                  <DonutChart
                    data={mix}
                    centerLabel={fmtUsd(totalSpend)}
                    centerSub="total spend"
                    valueFormatter={(v) => (usage?.by_model?.some((m) => m.cost_usd > 0) ? fmtUsd(v) : `${fmtInt(v)} calls`)}
                    height={220}
                  />
                ) : (
                  <Empty>No model usage recorded yet.</Empty>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── C · Where the spend goes (per-role) + routing ──────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <Card>
              <CardHeader
                eyebrow="aegis.gateway · by_role"
                title="Where the spend goes"
                description="Per-role usage and cost, and whether that role routes to a small model."
                actions={
                  summary ? (
                    <Badge tone="neutral" className="gap-1.5">
                      <Coins className="size-3" />
                      {fmtUsd(summary.total_cost_usd)}
                    </Badge>
                  ) : null
                }
              />
              <CardBody className="pt-0">
                {summary && Object.keys(summary.by_role).length > 0 ? (
                  <div className="overflow-hidden rounded-xl border border-border">
                    <Table>
                      <THead>
                        <TH className="text-left">Role</TH>
                        <TH className="text-right">Calls</TH>
                        <TH className="text-right">Tokens</TH>
                        <TH className="text-right">Cost</TH>
                        <TH className="text-left">Tier</TH>
                      </THead>
                      <TBody>
                        {Object.entries(summary.by_role)
                          .sort((a, b) => b[1].cost_usd - a[1].cost_usd)
                          .map(([role, r]) => (
                            <TR key={role}>
                              <TD className="text-sm font-medium text-foreground">{role}</TD>
                              <TD className="tabular text-right text-sm text-foreground">{fmtInt(r.calls)}</TD>
                              <TD className="tabular text-right text-sm text-foreground">
                                {fmtInt(r.prompt_tokens + r.completion_tokens)}
                              </TD>
                              <TD className="tabular text-right text-sm text-foreground">{fmtUsd(r.cost_usd)}</TD>
                              <TD>
                                <Badge tone={r.small_model ? 'ok' : 'ml'} className="font-mono">
                                  {r.small_model ? 'small' : 'frontier'}
                                </Badge>
                              </TD>
                            </TR>
                          ))}
                      </TBody>
                    </Table>
                  </div>
                ) : (
                  <Empty>No metered calls yet — per-role spend appears once traffic flows.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="aegis.gateway · routing"
                title="Model routing"
                description="The effective role → deployment map — the mechanism behind the small-model share and the savings."
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Route className="size-3" />
                    heterogeneous
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {metrics?.routing && Object.keys(metrics.routing).length > 0 ? (
                  <RoutingTable routing={metrics.routing} />
                ) : opt?.config.routing && Object.keys(opt.config.routing).length > 0 ? (
                  <RoutingTable routing={opt.config.routing} />
                ) : (
                  <Empty>Routing table unavailable.</Empty>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── D · Tenant & budget health ─────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                eyebrow="aegis.governance · /governance/dashboard"
                title="Tenants & budgets"
                description="Every tenant with its cap, ledger-derived spend, remaining headroom and calls. The bar is spend vs the USD cap."
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Landmark className="size-3" />
                    {tenants.length} {tenants.length === 1 ? 'tenant' : 'tenants'}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {tenants.length === 0 ? (
                  <Empty>No tenant data (governance stores off) — the accessor returned an empty snapshot.</Empty>
                ) : (
                  <div className="overflow-hidden rounded-xl border border-border">
                    <Table>
                      <THead>
                        <TH className="text-left">Tenant</TH>
                        <TH className="text-left">Spend / limit</TH>
                        <TH className="text-right">Remaining</TH>
                        <TH className="text-right">Calls</TH>
                      </THead>
                      <TBody>
                        {tenants.map((t) => {
                          const b = budgetByTenant.get(t.id)
                          const cap = b?.budget.usd_cap ?? null
                          const spent = b?.cost_usd_used ?? null
                          const remaining = b?.usd_remaining ?? null
                          const calls = b?.calls ?? null
                          const frac = cap != null && cap > 0 && spent != null ? Math.min(1, spent / cap) : null
                          return (
                            <TR key={t.id} className="align-top">
                              <TD className="whitespace-nowrap">
                                <div className="flex flex-col gap-0.5">
                                  <span className="text-sm font-medium text-foreground">{t.name}</span>
                                  <span className="font-mono text-[0.7rem] text-muted-foreground">tenant #{t.id}</span>
                                </div>
                              </TD>
                              <TD className="min-w-[9rem]">
                                <div className="flex items-baseline justify-between gap-2">
                                  <span className="tabular text-sm text-foreground">{fmtUsd(spent)}</span>
                                  <span className="tabular font-mono text-[0.7rem] text-muted-foreground">
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
                              </TD>
                              <TD className="tabular whitespace-nowrap text-right text-sm text-foreground">{fmtUsd(remaining)}</TD>
                              <TD className="tabular whitespace-nowrap text-right text-sm text-foreground">{fmtInt(calls)}</TD>
                            </TR>
                          )
                        })}
                      </TBody>
                    </Table>
                  </div>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="aegis.governance · RBAC"
                title="Users & roles"
                description="Members in scope, grouped by the portal role granting their access."
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Users className="size-3" />
                    {users.length}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {roleCounts.length === 0 ? (
                  <Empty>No users (stores off).</Empty>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {roleCounts.map(([role, count]) => (
                      <li
                        key={role}
                        className="flex items-center justify-between rounded-xl border border-border bg-surface-2/30 px-3.5 py-2.5"
                      >
                        <Badge tone={roleTone(role)} className="font-mono">
                          {role}
                        </Badge>
                        <span className="tabular text-sm font-medium text-foreground">
                          {count} {count === 1 ? 'user' : 'users'}
                        </span>
                      </li>
                    ))}
                  </ul>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── E · Safety & queue ─────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card>
              <CardHeader
                eyebrow="aegis.agent · /approvals"
                title="Approvals queue"
                description="Risk-gated actions awaiting a human decision."
                actions={
                  <Badge tone={pending && pending > 0 ? 'risk' : 'ok'} className="gap-1.5">
                    <ListChecks className="size-3" />
                    {pending ?? 0} pending
                  </Badge>
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
                    {approvals.rows.length > 4 && (
                      <li className="px-1 pt-1 text-[0.72rem] text-muted-foreground">
                        +{approvals.rows.length - 4} more in the Approvals inbox
                      </li>
                    )}
                  </ul>
                ) : (
                  <Empty>No pending approvals — the human gate is clear.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="aegis.security · /security/posture"
                title="Security posture"
                description="Threat controls by live-derived status."
                actions={
                  <Badge tone={postureCounts.not_covered > 0 ? 'block' : postureCounts.partial > 0 ? 'risk' : 'ok'} className="gap-1.5">
                    <ShieldCheck className="size-3" />
                    {postureCounts.enforced} enforced
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {posture && posture.entries.length > 0 ? (
                  <div className="flex flex-col gap-3">
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="rounded-lg bg-ok/15 py-2">
                        <p className="tabular t-title text-ok-ink">{postureCounts.enforced}</p>
                        <p className="text-[0.68rem] text-muted-foreground">enforced</p>
                      </div>
                      <div className="rounded-lg bg-risk/20 py-2">
                        <p className="tabular t-title text-risk-ink">{postureCounts.partial}</p>
                        <p className="text-[0.68rem] text-muted-foreground">partial</p>
                      </div>
                      <div className="rounded-lg bg-block/20 py-2">
                        <p className="tabular t-title text-block-ink">{postureCounts.not_covered}</p>
                        <p className="text-[0.68rem] text-muted-foreground">not covered</p>
                      </div>
                    </div>
                    {postureGaps.length > 0 ? (
                      <ul className="flex flex-col gap-1.5">
                        {postureGaps.map((e) => (
                          <li key={e.threat_id} className="flex items-center justify-between gap-2 text-[0.78rem]">
                            <span className="truncate text-foreground">{e.name}</span>
                            <Badge tone={postureTone(e.status)} className="font-mono">
                              {e.status}
                            </Badge>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-[0.78rem] text-ok-ink">All mapped controls enforced.</p>
                    )}
                  </div>
                ) : (
                  <Empty>Posture unavailable.</Empty>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="aegis.observability · /latency"
                title="Latency SLA"
                description="Per-run percentiles from real samples."
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <Activity className="size-3" />
                    {latency?.run_count ?? 0} runs
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {latency && !latency.empty ? (
                  <div className="flex flex-col gap-3">
                    <div className="grid grid-cols-3 gap-2 text-center">
                      <div className="rounded-lg bg-surface-2/50 py-2">
                        <p className="tabular t-title text-foreground">{fmtMs(latency.run_p50_ms)}</p>
                        <p className="text-[0.68rem] text-muted-foreground">p50</p>
                      </div>
                      <div className="rounded-lg bg-surface-2/50 py-2">
                        <p className="tabular t-title text-foreground">{fmtMs(latency.run_p95_ms)}</p>
                        <p className="text-[0.68rem] text-muted-foreground">p95</p>
                      </div>
                      <div className="rounded-lg bg-surface-2/50 py-2">
                        <p className="tabular t-title text-foreground">{fmtMs(latency.run_max_ms)}</p>
                        <p className="text-[0.68rem] text-muted-foreground">max</p>
                      </div>
                    </div>
                    {latency.slowest_node ? (
                      <p className="text-[0.78rem] text-muted-foreground">
                        Slowest node: <span className="font-mono text-foreground">{latency.slowest_node}</span>
                      </p>
                    ) : null}
                  </div>
                ) : (
                  <Empty>No runs recorded yet — an honest empty state, not fake zeros.</Empty>
                )}
              </CardBody>
            </Card>
          </div>

          {/* ── F · Audit & alerts ─────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader
                eyebrow="aegis.governance · audit"
                title="Recent audit tail"
                description="The most recent governance audit rows — actor, action and time. Read-only."
                actions={
                  <Badge tone="neutral" className="gap-1.5">
                    <ScrollText className="size-3" />
                    {audit.length}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {audit.length === 0 ? (
                  <Empty>No audit entries (stores off).</Empty>
                ) : (
                  <div className="overflow-hidden rounded-xl border border-border">
                    <Table>
                      <THead>
                        <TH className="text-left">Actor</TH>
                        <TH className="text-left">Action</TH>
                        <TH className="text-right">Time</TH>
                      </THead>
                      <TBody>
                        {audit.slice(0, 8).map((row, i) => {
                          const r = row as Record<string, unknown>
                          const actor = (r.actor ?? r.username ?? r.user ?? '—') as string
                          const action = (r.action ?? r.event ?? '—') as string
                          const ts = r.ts ?? r.created_at ?? r.timestamp
                          const key = (r.id as number | string | undefined) ?? i
                          return (
                            <TR key={key}>
                              <TD className="text-sm font-medium text-foreground">{String(actor)}</TD>
                              <TD>
                                <span className="font-mono text-[0.78rem] text-foreground">{String(action)}</span>
                              </TD>
                              <TD className="tabular whitespace-nowrap text-right font-mono text-[0.72rem] text-muted-foreground">
                                {fmtTs(ts)}
                              </TD>
                            </TR>
                          )
                        })}
                      </TBody>
                    </Table>
                  </div>
                )}
              </CardBody>
            </Card>

            <Card>
              <CardHeader
                eyebrow="derived · needs-attention"
                title="Alerts"
                description="Composed from the panels above — budget, security and queue signals."
                actions={
                  <Badge tone={alerts.length > 0 ? 'risk' : 'ok'} className="gap-1.5">
                    <AlertTriangle className="size-3" />
                    {alerts.length}
                  </Badge>
                }
              />
              <CardBody className="pt-0">
                {alerts.length === 0 ? (
                  <div className="flex items-center gap-2 rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-8 text-sm text-muted-foreground">
                    <CheckCircle2 className="size-4 text-ok-ink" />
                    All clear — no budget, security or queue signals need attention.
                  </div>
                ) : (
                  <ul className="flex flex-col gap-2">
                    {alerts.map((a, i) => (
                      <li key={i} className="flex items-start gap-2.5 rounded-xl border border-border bg-surface-2/30 px-3.5 py-2.5">
                        <span
                          className="mt-1.5 size-2 shrink-0 rounded-full"
                          style={{ background: a.tone === 'block' ? 'var(--danger)' : a.tone === 'risk' ? 'var(--risk-ink, #b45309)' : 'var(--muted-foreground, #64748b)' }}
                          aria-hidden
                        />
                        <span className="text-[0.82rem] text-foreground">{a.text}</span>
                      </li>
                    ))}
                  </ul>
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
