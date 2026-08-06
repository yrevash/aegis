import { Building2, Coins, Users, Wallet, type LucideIcon } from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { getBudgets, getTenants, getUsage, getUsers } from '@/api/client'
import { formatUsd } from '@/components/dashboard/roi'
import { CountUp } from '@/components/shared'
import { Card } from '@/components/ui/card'
import { Gauge } from '@/components/ui/Gauge'
import { InfoTip } from '@/components/ui/InfoTip'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { BudgetsResponse, TenantsResponse, UsageResponse, UsersResponse } from '@/types/api'

import { activeUserCount, budgetUtilisation, monthUsdCapTotal } from './governance'
import { useAsync } from './useAsync'

/**
 * The Governance overview band (§4.6): four live headline figures above the
 * tabs so the surface opens on the state of the estate — tenants, active users,
 * month-to-date spend and budget utilisation — before any raw table. Every
 * number is measured (real endpoints); the plain-language "who / capped / spent"
 * framing lives in tooltips, not on the face.
 */
export function GovernanceOverview({ token }: { token: string | null }): ReactElement {
  const tenants = useAsync<TenantsResponse>(() => getTenants(token), [token])
  const users = useAsync<UsersResponse>(() => getUsers(token, null), [token])
  const usage = useAsync<UsageResponse>(() => getUsage(token, { window: 'month' }), [token])
  const budgets = useAsync<BudgetsResponse>(() => getBudgets(token), [token])

  const tenantCount = tenants.state.status === 'ready' ? tenants.state.data.rows.length : null
  const activeUsers = users.state.status === 'ready' ? activeUserCount(users.state.data.rows) : null
  const spend = usage.state.status === 'ready' ? usage.state.data.total_cost_usd : null
  const spendTrend =
    usage.state.status === 'ready'
      ? usage.state.data.series.map((p) => Number(p.cost_usd.toFixed(2)))
      : undefined

  const capTotal = budgets.state.status === 'ready' ? monthUsdCapTotal(budgets.state.data.rows) : null
  const utilisation = spend != null ? budgetUtilisation(spend, capTotal) : null

  return (
    <Card className="gap-0 p-0">
      <div className="grid grid-cols-1 divide-y divide-border/70 sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 lg:divide-x">
        <Stat
          label="Tenants"
          icon={Building2}
          signal="graph"
          value={tenantCount}
          info="Enterprise clients on the platform — each an isolated tenant with its own users, budgets and usage."
        />
        <Stat
          label="Active users"
          icon={Users}
          signal="agent"
          value={activeUsers}
          info="Enabled members across all tenants. Role-based access (member / tenant admin / platform admin) governs what each can do."
        />
        <Stat
          label="Spend this month"
          icon={Coins}
          signal="ml"
          value={spend}
          format={(n) => formatUsd(n)}
          trend={spendTrend}
          info="Measured USD spend for the current month window, summed from per-model usage. Not an estimate."
        />
        <BudgetCell utilisation={utilisation} spend={spend} capTotal={capTotal} />
      </div>
    </Card>
  )
}

interface StatProps {
  label: string
  icon: LucideIcon
  signal: Signal
  value: number | null
  format?: (n: number) => string
  trend?: number[]
  info?: ReactNode
}

/** One compact overview figure with an icon chip, count-up and optional trend. */
function Stat({ label, icon: Icon, signal, value, format, trend, info }: StatProps): ReactElement {
  const token = SIGNALS[signal]
  return (
    <div className="flex flex-col gap-2 p-5">
      <div className="flex items-center gap-2">
        <span className={cn('grid size-6 place-items-center rounded-md', token.bg)}>
          <Icon className={cn('size-3.5', token.text)} />
        </span>
        <span className="eyebrow">{label}</span>
        {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </div>
      {value == null ? (
        <span className="t-metric text-muted-foreground/40">—</span>
      ) : (
        <CountUp value={value} format={format} className="t-metric text-foreground" />
      )}
      {trend && trend.length > 1 ? (
        <MiniInline data={trend} signal={signal} />
      ) : (
        <span className="h-[18px]" aria-hidden />
      )}
    </div>
  )
}

/** Tiny inline trend for the spend tile (kept import-light, no chart lib). */
function MiniInline({ data, signal }: { data: number[]; signal: Signal }): ReactElement {
  const hex = SIGNALS[signal].hex
  const lo = Math.min(...data)
  const hi = Math.max(...data)
  const span = hi - lo || 1
  const stepX = 100 / (data.length - 1)
  const pts = data.map((v, i) => `${(i * stepX).toFixed(1)},${(16 - ((v - lo) / span) * 14).toFixed(1)}`).join(' ')
  return (
    <svg viewBox="0 0 100 18" preserveAspectRatio="none" className="h-[18px] w-full" aria-hidden>
      <polyline
        points={pts}
        fill="none"
        stroke={hex}
        strokeWidth="1.5"
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
    </svg>
  )
}

/** The fourth cell: a budget-utilisation gauge, or an honest "no cap set". */
function BudgetCell({
  utilisation,
  spend,
  capTotal,
}: {
  utilisation: number | null
  spend: number | null
  capTotal: number | null
}): ReactElement {
  return (
    <div className="flex flex-col gap-2 p-5">
      <div className="flex items-center gap-2">
        <span className={cn('grid size-6 place-items-center rounded-md', SIGNALS.risk.bg)}>
          <Wallet className={cn('size-3.5', SIGNALS.risk.text)} />
        </span>
        <span className="eyebrow">Budget used</span>
        <InfoTip label="About budget used">
          Month-to-date spend against the sum of all month USD caps. Enforced on every request — a
          call is blocked once any level along its tenant → user path is over budget.
        </InfoTip>
      </div>
      {utilisation != null && capTotal != null ? (
        <div className="flex items-center gap-3">
          <Gauge value={utilisation} color="risk" size={64} />
          <span className="font-mono text-[0.68rem] text-muted-foreground">
            {spend != null ? formatUsd(spend) : '—'}
            <span className="text-muted-foreground/60"> / {formatUsd(capTotal)}</span>
          </span>
        </div>
      ) : (
        <div className="flex h-full flex-col justify-center">
          <span className="t-metric text-muted-foreground/50">—</span>
          <span className="font-mono text-[0.68rem] text-muted-foreground">no month cap set</span>
        </div>
      )}
    </div>
  )
}
