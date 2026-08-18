'use client'

import {
  Coins,
  Landmark,
  Loader2,
  PhoneCall,
  ScrollText,
  ShieldCheck,
  Sigma,
  Users,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { getGovernanceDashboard } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { adminScopeCaption, isPlatformAdmin } from '@/lib/auth/tier'
import type { BudgetStatusRow, GovernanceDashboardResponse } from '@/lib/api/platform'

// ── formatting helpers ───────────────────────────────────────────────────────

/** Thousands-grouped integer, or an em-dash for null. */
function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : Math.round(n).toLocaleString('en-US')
}

/** USD with cents, or an em-dash for null. */
function fmtUsd(n: number | null | undefined): string {
  return n == null ? '—' : `$${n.toFixed(2)}`
}

/** A short human timestamp (UTC) for the audit tail; passes through non-dates. */
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
    case 'client':
      return 'neutral'
    default:
      return 'neutral'
  }
}

// ── tenant + budget row ──────────────────────────────────────────────────────

/**
 * One tenant joined to its budget row (matched by `budget.tenant_id`). The
 * spend-vs-limit bar is driven by the ledger-derived `cost_usd_used` against the
 * budget's `usd_cap` — real figures the accessor computes from the ledger, never
 * fabricated.
 */
function TenantRow({
  name,
  tenantId,
  budget,
}: {
  name: string
  tenantId: number
  budget: BudgetStatusRow | undefined
}): ReactElement {
  const cap = budget?.budget.usd_cap ?? null
  const spent = budget?.cost_usd_used ?? null
  const remaining = budget?.usd_remaining ?? null
  const tokens = budget?.tokens_used ?? null
  const calls = budget?.calls ?? null
  const frac = cap != null && cap > 0 && spent != null ? Math.min(1, spent / cap) : null

  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">{name}</span>
          <span className="font-mono text-[0.7rem] text-muted-foreground">tenant #{tenantId}</span>
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
      <TD className="tabular whitespace-nowrap text-right text-sm text-foreground">{fmtInt(tokens)}</TD>
      <TD className="tabular whitespace-nowrap text-right text-sm text-foreground">{fmtInt(calls)}</TD>
    </TR>
  )
}

// ── the dashboard ────────────────────────────────────────────────────────────

/**
 * Governance dashboard — the `aegis.governance` read-surface (`/governance/
 * dashboard`), tenant-scoped and admin-only. Four honest panels drawn straight
 * from the accessor snapshot: tenants + budgets (spend computed from the ledger,
 * so the bar's spend == ledger sum by design), a usage roll-up, users + RBAC
 * roles, and the read-only recent-audit tail. In lite mode (stores off) the
 * accessor returns an empty snapshot and the panels read an honest empty state
 * rather than fake zeros.
 */
function GovernanceView(): ReactElement {
  // Live session token — `/governance/dashboard` is admin-only, so a constant
  // `null` here 401s on a reload and, being constant in the dependency array,
  // never retries once `AuthProvider` restored the persisted session.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [data, setData] = useState<GovernanceDashboardResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer.
    if (!hydrated) return
    let alive = true
    getGovernanceDashboard(token)
      .then((d) => {
        if (alive) {
          setData(d)
          setError(null)
        }
      })
      .catch(() => {
        if (alive) setError('Could not load the governance dashboard. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  /** Budget rows keyed by tenant id, so each tenant row can find its budget. */
  const budgetByTenant = useMemo(() => {
    const map = new Map<number, BudgetStatusRow>()
    for (const b of data?.budgets ?? []) {
      if (b.budget.tenant_id != null) map.set(b.budget.tenant_id, b)
    }
    return map
  }, [data])

  const tenants = data?.tenants ?? []
  const users = data?.users ?? []
  const audit = data?.recent_audit ?? []
  const usage = data?.usage ?? null

  return (
    <div className="space-y-6">
      {/* Section header — the scope caption is driven by the session's fine tier
          (`fine_role`), because the backend pins a tenant admin to its own tenant:
          captioning both tiers the same would show one tenant's rows as the
          platform's. */}
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <p className="eyebrow mb-1">tenants · budgets</p>
          <h1 className="t-hero text-foreground">Governance</h1>
        </div>
        <Badge tone={isPlatformAdmin(session) ? 'ml' : 'neutral'} className="gap-1.5">
          <ShieldCheck className="size-3" />
          {adminScopeCaption(session)}
        </Badge>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : data == null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading governance dashboard…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── Usage summary tiles ───────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatCard label="Calls" value={fmtInt(usage?.calls ?? 0)} icon={PhoneCall} tone="agent" />
            <StatCard label="Tokens" value={fmtInt(usage?.total_tokens ?? 0)} icon={Sigma} tone="ml" />
            <StatCard label="Cost" value={fmtUsd(usage?.total_cost_usd ?? 0)} icon={Coins} tone="ok" />
          </div>

          {/* ── Tenants + budgets ─────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.governance · /governance/dashboard"
              title="Tenants & budgets"
              description="Each tenant with its budget cap, ledger-derived spend, remaining headroom, tokens and calls. The bar is spend vs the USD cap."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Landmark className="size-3" />
                  {tenants.length} {tenants.length === 1 ? 'tenant' : 'tenants'}
                  {data.window ? ` · ${data.window}` : ''}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {tenants.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-8 text-center text-sm text-muted-foreground">
                  No tenant data (stores off) — the governance stores are not running, so the
                  accessor returned an empty snapshot.
                </p>
              ) : (
                <div className="overflow-hidden rounded-xl border border-border">
                  <Table>
                    <THead>
                      <TH className="text-left">Tenant</TH>
                      <TH className="text-left">Spend / limit</TH>
                      <TH className="text-right">Remaining</TH>
                      <TH className="text-right">Tokens</TH>
                      <TH className="text-right">Calls</TH>
                    </THead>
                    <TBody>
                      {tenants.map((t) => (
                        <TenantRow
                          key={t.id}
                          name={t.name}
                          tenantId={t.id}
                          budget={budgetByTenant.get(t.id)}
                        />
                      ))}
                    </TBody>
                  </Table>
                </div>
              )}
            </CardBody>
          </Card>

          {/* ── Users + roles ─────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.governance · RBAC"
              title="Users & roles"
              description="Members in scope and the portal role granting each one their access."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Users className="size-3" />
                  {users.length} {users.length === 1 ? 'user' : 'users'}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {users.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-8 text-center text-sm text-muted-foreground">
                  No users (stores off).
                </p>
              ) : (
                <div className="overflow-hidden rounded-xl border border-border">
                  <Table>
                    <THead>
                      <TH className="text-left">User</TH>
                      <TH className="text-left">Role</TH>
                      <TH className="text-right">ID</TH>
                    </THead>
                    <TBody>
                      {users.map((u) => (
                        <TR key={u.id}>
                          <TD className="text-sm font-medium text-foreground">{u.username}</TD>
                          <TD>
                            <Badge tone={roleTone(u.role)} className="font-mono">
                              {u.role}
                            </Badge>
                          </TD>
                          <TD className="tabular whitespace-nowrap text-right font-mono text-[0.72rem] text-muted-foreground">
                            #{u.id}
                          </TD>
                        </TR>
                      ))}
                    </TBody>
                  </Table>
                </div>
              )}
            </CardBody>
          </Card>

          {/* ── Audit tail ────────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.governance · audit"
              title="Recent audit tail"
              description="The most recent governance audit rows — actor, action and time. Read-only."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <ScrollText className="size-3" />
                  {audit.length} {audit.length === 1 ? 'entry' : 'entries'}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {audit.length === 0 ? (
                <p className="rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-8 text-center text-sm text-muted-foreground">
                  No audit entries (stores off).
                </p>
              ) : (
                <div className="overflow-hidden rounded-xl border border-border">
                  <Table>
                    <THead>
                      <TH className="text-left">Actor</TH>
                      <TH className="text-left">Action</TH>
                      <TH className="text-right">Time</TH>
                    </THead>
                    <TBody>
                      {audit.map((row, i) => {
                        const r = row as Record<string, unknown>
                        const actor = (r.actor ?? r.username ?? r.user ?? '—') as string
                        const action = (r.action ?? r.event ?? '—') as string
                        const ts = r.ts ?? r.created_at ?? r.timestamp
                        const key = (r.id as number | string | undefined) ?? i
                        return (
                          <TR key={key}>
                            <TD className="text-sm font-medium text-foreground">{String(actor)}</TD>
                            <TD>
                              <span className="font-mono text-[0.78rem] text-foreground">
                                {String(action)}
                              </span>
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
        </>
      )}
    </div>
  )
}

/** Client entry for the Governance section — gated on a reachable backend. */
export function GovernanceMount(): ReactElement {
  return (
    <BackendGate>
      <GovernanceView />
    </BackendGate>
  )
}
