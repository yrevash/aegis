'use client'

import {
  AlertTriangle,
  Coins,
  Landmark,
  PhoneCall,
  ScrollText,
  ShieldCheck,
  Sigma,
  Users,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { SectionHeader } from '@/components/primitives/SectionHeader'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
import { getGovernanceDashboard } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { adminScopeCaption } from '@/lib/auth/tier'
import type { BudgetStatusRow, GovernanceDashboardResponse } from '@/lib/api/platform'

// ── formatting helpers ───────────────────────────────────────────────────────

/** Thousands-grouped integer, or an em-dash for null. */
function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : Math.round(n).toLocaleString('en-US')
}

/**
 * USD with cents, or an em-dash for null.
 *
 * Through `Intl.NumberFormat` rather than a template string, so a five-figure cap is
 * grouped rather than arriving as `$12345.00` — which is the one shape a spend cap must
 * not have on a screen somebody reads in a hurry.
 */
const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

function fmtUsd(n: number | null | undefined): string {
  return n == null ? '—' : USD.format(n)
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

/**
 * The utilisation band a spend bar is in, as a fill colour *and* a word.
 *
 * The bar used to be green / amber / red and nothing else, which is colour carrying a
 * verdict on its own — and amber and red are the pair the palette validator fails on
 * CVD separation, so the two bands that matter were the two a reader might not be able
 * to tell apart. Under the first threshold the fill is the one blue that carries
 * magnitude everywhere else in the console; at 70% and at 90% it takes a reserved
 * status hue *and* the row prints the word beside the figure.
 */
function band(frac: number): { fill: string; word: string | null; ink: string } {
  if (frac >= 0.9) return { fill: 'var(--danger)', word: 'at cap', ink: 'text-block-ink' }
  if (frac >= 0.7) return { fill: 'var(--risk-ink)', word: 'near cap', ink: 'text-risk-ink' }
  return { fill: 'var(--blue-600)', word: null, ink: 'text-muted-foreground' }
}

// ── tenant + budget row ──────────────────────────────────────────────────────

/**
 * One tenant joined to its budget row (matched by `budget.tenant_id`). The
 * spend-vs-limit bar is driven by the ledger-derived `cost_usd_used` against the
 * budget's `usd_cap` — real figures the accessor computes from the ledger, never
 * fabricated. A tenant with no cap gets a stated absence rather than an empty
 * track, because an empty track and a full-but-uncapped one look the same.
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
  const meter = frac == null ? null : band(frac)
  const pct = frac == null ? null : Math.round(frac * 100)

  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">{name}</span>
          <Figure className="text-muted-foreground">{`tenant #${tenantId}`}</Figure>
        </div>
      </TD>
      <TD className="min-w-[11rem]">
        <div className="flex items-baseline justify-between gap-2">
          <Figure className="text-foreground">{fmtUsd(spent)}</Figure>
          <Figure className="text-muted-foreground">
            {cap != null ? `/ ${fmtUsd(cap)}` : '/ no cap'}
          </Figure>
        </div>
        {frac != null && meter != null && pct != null ? (
          <>
            <div
              className="mt-1.5 h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
              role="img"
              aria-label={`${pct}% of the cap spent`}
            >
              <div
                className="h-full rounded-full transition-[width] duration-200 motion-reduce:transition-none"
                style={{ width: `${Math.max(2, pct)}%`, background: meter.fill }}
              />
            </div>
            <p className={`mt-1 flex items-center gap-1 text-[0.68rem] ${meter.ink}`}>
              {meter.word ? <AlertTriangle aria-hidden className="size-3 shrink-0" /> : null}
              <Figure>{`${pct}%`}</Figure>
              <span>{meter.word ?? 'of cap'}</span>
            </p>
          </>
        ) : (
          <p className="mt-1.5 text-[0.68rem] leading-snug text-muted-foreground">
            No USD cap is set for this tenant, so there is no proportion to draw.
          </p>
        )}
      </TD>
      <TD className="whitespace-nowrap text-right">
        <Figure className="text-foreground">{fmtUsd(remaining)}</Figure>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <Figure className="text-foreground">{fmtInt(tokens)}</Figure>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <Figure className="text-foreground">{fmtInt(calls)}</Figure>
      </TD>
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
 *
 * A **portal role is not a status**, so it is no longer painted like one. Four
 * roles used to take four different badge tones, which is a colour that means
 * nothing sitting next to the reserved hues that mean a great deal — the exact
 * thing DESIGN.md §2 is about. The role is told apart by its word.
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
      .catch((failure: unknown) => {
        if (alive) {
          setError(
            errorSentence(
              failure,
              'The governance dashboard did not load. Check the backend is reachable, then retry.',
            ),
          )
        }
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
  const window = data?.window ?? null
  const source = `aegis.governance · /governance/dashboard${window ? ` · ${window}` : ''}`

  return (
    <div className="space-y-6">
      {/* The scope caption is driven by the session's fine tier (`fine_role`),
          because the backend pins a tenant admin to its own tenant: captioning both
          tiers the same would show one tenant's rows as the platform's. */}
      <SectionHeader
        as="h1"
        eyebrow="tenants · budgets"
        title="Governance"
        note="Every tenant, what it may spend, what it has spent, and who inside it holds which portal."
        right={
          <Badge
            tone="neutral"
            className="max-w-[52vw] gap-1.5 text-left whitespace-normal sm:max-w-none"
          >
            <ShieldCheck className="size-3 shrink-0" aria-hidden />
            {adminScopeCaption(session)}
          </Badge>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : data == null ? (
        <LoadingState rows={6} label="Reading the governance dashboard…" />
      ) : (
        <>
          {/* ── Usage summary tiles ───────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
            <StatCard
              label="Calls"
              value={fmtInt(usage?.calls ?? 0)}
              icon={PhoneCall}
              source={source}
              className="rounded-lg"
            />
            <StatCard
              label="Tokens"
              value={fmtInt(usage?.total_tokens ?? 0)}
              icon={Sigma}
              source={source}
              className="rounded-lg"
            />
            <StatCard
              label="Cost"
              value={fmtUsd(usage?.total_cost_usd ?? 0)}
              icon={Coins}
              source={`${source} · summed from the usage ledger`}
              className="rounded-lg"
            />
          </div>

          {/* ── Tenants + budgets ─────────────────────────────────────────────── */}
          <Card className="rounded-lg">
            <CardHeader
              eyebrow="aegis.governance · /governance/dashboard"
              title="Tenants & budgets"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Landmark className="size-3" aria-hidden />
                  <Figure>{tenants.length}</Figure>{' '}
                  {tenants.length === 1 ? 'tenant' : 'tenants'}
                  {window ? ` · ${window}` : ''}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {tenants.length === 0 ? (
                <EmptyState
                  icon={Landmark}
                  title="No tenant data"
                  body="The governance stores are not running, so the accessor returned an empty snapshot. This is lite mode, not an empty platform."
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
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
          <Card className="rounded-lg">
            <CardHeader
              eyebrow="aegis.governance · RBAC"
              title="Users & roles"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Users className="size-3" aria-hidden />
                  <Figure>{users.length}</Figure> {users.length === 1 ? 'user' : 'users'}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {users.length === 0 ? (
                <EmptyState
                  icon={Users}
                  title="No users in scope"
                  body="The governance stores are not running, so no roster could be read."
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
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
                            <Badge tone="neutral" className="font-mono">
                              {u.role}
                            </Badge>
                          </TD>
                          <TD className="whitespace-nowrap text-right">
                            <Figure className="text-muted-foreground">{`#${u.id}`}</Figure>
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
          <Card className="rounded-lg">
            <CardHeader
              eyebrow="aegis.governance · audit"
              title="Recent audit tail"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <ScrollText className="size-3" aria-hidden />
                  <Figure>{audit.length}</Figure> {audit.length === 1 ? 'entry' : 'entries'}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              {audit.length === 0 ? (
                <EmptyState
                  icon={ScrollText}
                  title="No audit entries"
                  body="The governance stores are not running. The full trail, when they are, lives on the Audit page."
                />
              ) : (
                <div className="overflow-hidden rounded-lg border border-border">
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
                            <TD className="text-sm font-medium text-foreground">
                              {String(actor)}
                            </TD>
                            <TD>
                              <Figure className="text-foreground">{String(action)}</Figure>
                            </TD>
                            <TD className="whitespace-nowrap text-right">
                              <Figure className="text-muted-foreground">{fmtTs(ts)}</Figure>
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
