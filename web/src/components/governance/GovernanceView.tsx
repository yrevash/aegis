'use client'

import {
  AlertTriangle,
  CheckCircle2,
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
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { Gauge } from '@/components/primitives/Gauge'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { TooltipProvider } from '@/components/primitives/tooltip'
import type { Signal } from '@/config/signals'
import { errorSentence } from '@/lib/api/apiError'
import { getGovernanceDashboard } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { adminScopeCaption } from '@/lib/auth/tier'
import { cn } from '@/lib/utils'
import type { BudgetStatusRow, GovernanceDashboardResponse } from '@/lib/api/platform'

// ── formatting helpers ───────────────────────────────────────────────────────

/** Thousands-grouped integer, or an em-dash for null. */
function fmtInt(n: number | null | undefined): string {
  return n == null ? '—' : Math.round(n).toLocaleString('en-US')
}

/** 1,948,511 → `1.95M`, so a cap and its consumption fit one bullet-bar caption. */
function fmtCompact(n: number | null | undefined): string {
  if (n == null) return '—'
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (Math.abs(n) >= 1_000) return `${Math.round(n / 1_000)}K`
  return Math.round(n).toLocaleString('en-US')
}

/**
 * USD with cents, or an em-dash for null.
 *
 * Through `Intl.NumberFormat` rather than a template string, so a five-figure cap is
 * grouped rather than arriving as `$12345.00` — which is the one shape a spend cap must
 * not have on a screen somebody reads in a hurry.
 */
const USD = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
})

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

// ── the utilisation band ─────────────────────────────────────────────────────

interface Band {
  /** Fill for the bullet bar / gauge arc. */
  fill: string
  /** The verdict, always printed — colour never carries it alone. */
  word: string
  /** Ink for that verdict word. */
  ink: string
  /** Signal the `Gauge` arc resolves through. */
  signal: Signal
  /** True once a human should be looking at this row. */
  alert: boolean
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
function band(frac: number): Band {
  if (frac >= 0.9)
    return {
      fill: 'var(--block)',
      word: 'at cap',
      ink: 'text-block-ink',
      signal: 'block',
      alert: true,
    }
  if (frac >= 0.7)
    return {
      fill: 'var(--risk)',
      word: 'near cap',
      ink: 'text-risk-ink',
      signal: 'risk',
      alert: true,
    }
  return {
    fill: 'var(--blue-600)',
    word: 'within cap',
    ink: 'text-muted-foreground',
    signal: 'graph',
    alert: false,
  }
}

// ── bullet bar ───────────────────────────────────────────────────────────────

/**
 * One bounded value against its cap, in the form DESIGN.md §2 prescribes for
 * several such values side by side: a bar a third of the thickness of its track,
 * with a perpendicular target line at the 70% attention threshold, in one hue at
 * distinct intensities so it survives colour-blindness. The percentage and the
 * band word are printed, so the bar is decoration on top of a legible fact and
 * never the only carrier.
 */
function BulletBar({
  label,
  used,
  cap,
  format,
  className,
}: {
  label: string
  used: number | null | undefined
  cap: number | null | undefined
  format: (n: number | null | undefined) => string
  className?: string
}): ReactElement {
  const frac = cap != null && cap > 0 && used != null ? used / cap : null
  const b = frac == null ? null : band(frac)
  const pct = frac == null ? null : Math.round(frac * 100)

  return (
    <div className={cn('min-w-0', className)}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-xs text-muted-foreground">{label}</span>
        <span className="flex items-baseline gap-1 whitespace-nowrap">
          <Figure className="text-xs text-foreground">{format(used)}</Figure>
          <Figure className="text-xs text-muted-foreground">
            {cap == null ? '· uncapped' : `/ ${format(cap)}`}
          </Figure>
        </span>
      </div>
      {frac != null && b != null && pct != null ? (
        <>
          <div
            className="relative mt-1.5 h-3 w-full rounded-sm bg-surface-2"
            role="img"
            aria-label={`${label}: ${pct}% of the cap, ${b.word}`}
          >
            {/* the bar, one third of the track's thickness */}
            <div className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 px-0">
              <div
                className="h-full rounded-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
                style={{
                  width: `${Math.max(2, Math.min(100, pct))}%`,
                  background: b.fill,
                }}
              />
            </div>
            {/* the target line — the 70% threshold at which somebody should look */}
            <span
              aria-hidden
              className="absolute top-0.5 bottom-0.5 w-px bg-foreground/35"
              style={{ left: '70%' }}
            />
          </div>
          <p className={cn('mt-1 flex items-center gap-1 text-[0.68rem]', b.ink)}>
            {b.alert ? <AlertTriangle aria-hidden className="size-3 shrink-0" /> : null}
            <Figure>{`${pct}%`}</Figure>
            <span>{b.word}</span>
          </p>
        </>
      ) : (
        <p className="mt-1.5 text-[0.68rem] leading-snug text-muted-foreground">
          No cap recorded — nothing to draw a proportion against.
        </p>
      )}
    </div>
  )
}

// ── the per-tenant gauge ─────────────────────────────────────────────────────

/** The fraction of a cap consumed, or null when there is no cap to divide by. */
function fracOf(used: number | null | undefined, cap: number | null | undefined): number | null {
  return cap != null && cap > 0 && used != null ? used / cap : null
}

/**
 * One tenant's headline: the gauge shows the **binding** constraint — whichever
 * of the USD cap and the token cap it is closest to — because a tenant with 4%
 * of its dollars spent and 97% of its tokens burned is not comfortable, and a
 * screen that averaged the two would say it was.
 *
 * The arc clamps at the ring but the read-out does not: a tenant that has gone
 * through 180% of its token cap prints `180%`, because rounding an overrun down
 * to a full ring is the one lie this screen cannot afford.
 */
function TenantGauge({
  name,
  tenantId,
  budget,
}: {
  name: string
  tenantId: number
  budget: BudgetStatusRow | undefined
}): ReactElement {
  const usdFrac = fracOf(budget?.cost_usd_used, budget?.budget.usd_cap)
  const tokFrac = fracOf(budget?.tokens_used, budget?.budget.token_cap)
  const candidates: Array<{ frac: number; what: string }> = []
  if (usdFrac != null) candidates.push({ frac: usdFrac, what: 'of USD cap' })
  if (tokFrac != null) candidates.push({ frac: tokFrac, what: 'of token cap' })
  const binding = candidates.length ? candidates.reduce((a, b) => (b.frac > a.frac ? b : a)) : null
  const b = binding == null ? null : band(binding.frac)

  return (
    <div
      className={cn(
        'flex flex-col rounded-lg border bg-surface p-4 transition-shadow duration-[--dur-fast] motion-reduce:transition-none hover:shadow-hover',
        b?.alert ? 'border-risk/50' : 'border-border',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-foreground">{name}</p>
          <Figure className="text-muted-foreground">{`tenant #${tenantId}`}</Figure>
        </div>
        {b ? (
          <Badge tone={b.signal === 'graph' ? 'ok' : b.signal} className="shrink-0 gap-1">
            {b.alert ? (
              <AlertTriangle aria-hidden className="size-3" />
            ) : (
              <CheckCircle2 aria-hidden className="size-3" />
            )}
            {b.word}
          </Badge>
        ) : (
          <Badge tone="neutral" className="shrink-0">
            no cap
          </Badge>
        )}
      </div>

      {binding == null || b == null ? (
        <Absence
          className="mt-3"
          figure="Cap utilisation"
          why="No budget row is recorded for this tenant, so there is no denominator."
          needed="A tenant-scoped budget with a USD or token cap."
        />
      ) : (
        <>
          <div className="mt-2 flex items-center gap-4">
            <Gauge
              value={binding.frac}
              size={104}
              color={b.signal}
              centerLabel={`${Math.round(binding.frac * 100)}%`}
              className="shrink-0"
            />
            <div className="min-w-0 flex-1 space-y-3">
              <BulletBar
                label="Spend"
                used={budget?.cost_usd_used}
                cap={budget?.budget.usd_cap}
                format={fmtUsd}
              />
              <BulletBar
                label="Tokens"
                used={budget?.tokens_used}
                cap={budget?.budget.token_cap}
                format={fmtCompact}
              />
            </div>
          </div>
          <p className="mt-2 text-xs text-muted-foreground">
            Ring shows the binding constraint — <span className={b.ink}>{binding.what}</span>.
          </p>
        </>
      )}
    </div>
  )
}

// ── tenant + budget table row ────────────────────────────────────────────────

/**
 * One tenant joined to its budget row. The spend-vs-limit bars are driven by the
 * ledger-derived `cost_usd_used` / `tokens_used` against the budget's caps — real
 * figures the accessor computes from the ledger, never fabricated.
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
  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <span className="text-sm font-medium text-foreground">{name}</span>
          <Figure className="text-muted-foreground">{`tenant #${tenantId}`}</Figure>
        </div>
      </TD>
      <TD className="min-w-[11rem]">
        <BulletBar
          label="USD"
          used={budget?.cost_usd_used}
          cap={budget?.budget.usd_cap}
          format={fmtUsd}
        />
      </TD>
      <TD className="min-w-[11rem]">
        <BulletBar
          label="Tokens"
          used={budget?.tokens_used}
          cap={budget?.budget.token_cap}
          format={fmtCompact}
        />
      </TD>
      <TD className="whitespace-nowrap text-right">
        <Figure className="text-foreground">{fmtUsd(budget?.usd_remaining)}</Figure>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <Figure className="text-foreground">{fmtInt(budget?.calls)}</Figure>
      </TD>
    </TR>
  )
}

// ── cost by model ────────────────────────────────────────────────────────────

/**
 * Where the money went, as one sequential hue at two intensities — the models
 * carrying most of the bill in `--blue-600`, the tail in `--blue-400`. Not a
 * cycled palette: model identity is carried by its name, which is the only thing
 * that can carry it honestly across fourteen of them.
 */
function CostByModel({
  rows,
  total,
}: {
  rows: Array<{ model: string; cost_usd: number }>
  total: number
}): ReactElement {
  const top = rows.slice(0, 8)
  const max = top.reduce((m, r) => Math.max(m, r.cost_usd), 0)
  return (
    <ul className="space-y-2.5">
      {top.map((r) => {
        const share = total > 0 ? r.cost_usd / total : 0
        const w = max > 0 ? (r.cost_usd / max) * 100 : 0
        return (
          <li key={r.model} className="min-w-0">
            <div className="flex items-baseline justify-between gap-3">
              <span className="truncate text-xs text-foreground" title={r.model}>
                {r.model}
              </span>
              <span className="flex shrink-0 items-baseline gap-2">
                <Figure className="text-xs text-foreground">{fmtUsd(r.cost_usd)}</Figure>
                <Figure className="w-9 text-right text-xs text-muted-foreground">
                  {`${Math.round(share * 100)}%`}
                </Figure>
              </span>
            </div>
            <div className="mt-1 h-1.5 w-full overflow-hidden rounded-full bg-surface-2">
              <div
                className="h-full rounded-full transition-[width] duration-[--dur-base] motion-reduce:transition-none"
                style={{
                  width: `${Math.max(2, w)}%`,
                  background: share >= 0.1 ? 'var(--blue-600)' : 'var(--blue-400)',
                }}
              />
            </div>
          </li>
        )
      })}
    </ul>
  )
}

// ── the dashboard ────────────────────────────────────────────────────────────

/**
 * Governance dashboard — the `aegis.governance` read-surface (`/governance/
 * dashboard`), tenant-scoped and admin-only. It opens with the one question the
 * screen exists to answer — *is any tenant about to run out of what it is allowed
 * to spend?* — as a gauge per tenant, and keeps the ledgers beneath it.
 *
 * The budget join is by **scope**, not by `tenant_id`. The accessor returns
 * `scope_type`/`scope_id` and leaves `tenant_id` null on every budget row, so the
 * previous join found nothing and painted every tenant "no cap" while a tenant sat
 * at 97% of its token allowance. Matching on the scope pair is what makes the
 * gauges read the caps that are actually enforced. User-scoped rows are joined the
 * same way onto the roster.
 *
 * A **portal role is not a status**, so it is not painted like one. Four roles used
 * to take four different badge tones, which is a colour that means nothing sitting
 * next to the reserved hues that mean a great deal — the exact thing DESIGN.md §2 is
 * about. The role is told apart by its word.
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

  /**
   * Budget rows keyed by scope. The accessor emits `scope_type: 'tenant' | 'user'`
   * with `scope_id`, and leaves the row's own `tenant_id` null — so the key has to
   * be the pair, and `tenant_id` is only a fallback for a shape that carries it.
   */
  const { tenantBudgets, userBudgets } = useMemo(() => {
    const t = new Map<number, BudgetStatusRow>()
    const u = new Map<number, BudgetStatusRow>()
    for (const b of data?.budgets ?? []) {
      const scope = b.budget.scope_id ?? b.budget.tenant_id
      if (scope == null) continue
      if (b.budget.scope_type === 'user') u.set(scope, b)
      else t.set(scope, b)
    }
    return { tenantBudgets: t, userBudgets: u }
  }, [data])

  // Memoised because the flagged-tenant roll-up below depends on it, and a fresh
  // `[]` on every render would recompute the reduction on every render.
  const tenants = useMemo(() => data?.tenants ?? [], [data])
  const users = data?.users ?? []
  const audit = data?.recent_audit ?? []
  const usage = data?.usage ?? null
  const window = data?.window ?? null
  const source = `aegis.governance${window ? ` · per ${window}` : ''}`

  /** Tenants whose binding constraint has crossed the attention threshold. */
  const flagged = useMemo(
    () =>
      tenants.filter((t) => {
        const b = tenantBudgets.get(t.id)
        if (!b) return false
        const u = fracOf(b.cost_usd_used, b.budget.usd_cap) ?? 0
        const k = fracOf(b.tokens_used, b.budget.token_cap) ?? 0
        return Math.max(u, k) >= 0.7
      }).length,
    [tenants, tenantBudgets],
  )

  const byModel = usage?.by_model ?? []
  /**
   * The cost sparkline under the Cost tile — the accessor's own hourly buckets,
   * oldest → newest. Real recorded spend, so the tile can show shape as well as
   * total; there is no synthesised series anywhere on this screen, and a window
   * that returns no buckets simply gets no sparkline.
   */
  const costTrend = useMemo(
    () => (usage?.series ?? []).map((p) => p.cost_usd).filter((n) => Number.isFinite(n)),
    [usage],
  )

  return (
    <div className="space-y-6">
      {/* The scope caption is driven by the session's fine tier (`fine_role`),
          because the backend pins a tenant admin to its own tenant: captioning both
          tiers the same would show one tenant's rows as the platform's. */}
      <PageHeader
        eyebrow="tenants · budgets"
        title="Governance"
        actions={
          <>
            {flagged > 0 ? (
              <Badge tone="risk" className="gap-1.5">
                <AlertTriangle className="size-3 shrink-0" aria-hidden />
                <Figure>{flagged}</Figure>
                {flagged === 1 ? ' tenant near cap' : ' tenants near cap'}
              </Badge>
            ) : null}
            <Badge
              tone="neutral"
              className="max-w-[52vw] gap-1.5 text-left whitespace-normal sm:max-w-none"
            >
              <ShieldCheck className="size-3 shrink-0" aria-hidden />
              {adminScopeCaption(session)}
            </Badge>
          </>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : data == null ? (
        <LoadingState rows={6} label="Reading the governance dashboard…" />
      ) : (
        <>
          {/* ── Spend against cap — the question the screen exists to answer ──── */}
          {tenants.length > 0 ? (
            <section aria-labelledby="gov-caps" className="space-y-3">
              <div className="flex flex-wrap items-center gap-2">
                <h2 id="gov-caps" className="text-base font-semibold text-foreground">
                  Spend against cap
                </h2>
                <InfoTip label="How the ring is chosen">
                  Each tenant carries a USD cap and a token cap for the {window ?? 'current'}{' '}
                  window. The ring shows whichever it is closer to, because the tighter of the two
                  is the one that will stop work. Bars beneath show both. Over-cap prints its true
                  percentage rather than a full ring.
                </InfoTip>
                <span className="ml-auto">
                  <Receipt
                    variant="inline"
                    origin="aegis.governance · budgets"
                    detail="consumption summed from the usage ledger"
                  />
                </span>
              </div>
              <div
                className={cn(
                  // `[&>*]:min-w-0` is load-bearing, not tidiness: a grid item's
                  // default `min-width: auto` resolves to its content, so one wide
                  // child widens the column, the grid, and finally the page — which
                  // is invisible at 1440 and is the whole experience at 390.
                  'grid gap-4 [&>*]:min-w-0',
                  // Two tenants on a three-column track leaves a hole where a third
                  // would be, and a hole reads as a panel that failed to load.
                  tenants.length === 1
                    ? 'sm:grid-cols-1'
                    : tenants.length === 2
                      ? 'sm:grid-cols-2'
                      : 'sm:grid-cols-2 xl:grid-cols-3',
                )}
              >
                {tenants.map((t) => (
                  <TenantGauge
                    key={t.id}
                    name={t.name}
                    tenantId={t.id}
                    budget={tenantBudgets.get(t.id)}
                  />
                ))}
              </div>
            </section>
          ) : null}

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
              trend={costTrend.length > 1 ? costTrend : undefined}
              source={`${source} · usage ledger`}
              className="rounded-lg"
            />
          </div>

          <div className="grid gap-6 xl:grid-cols-3 [&>*]:min-w-0">
            {/* ── Tenants + budgets ───────────────────────────────────────────── */}
            <DataPanel
              className="rounded-lg xl:col-span-2"
              eyebrow="aegis.governance · /governance/dashboard"
              title="Tenants & budgets"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Landmark className="size-3" aria-hidden />
                  <Figure>{tenants.length}</Figure> {tenants.length === 1 ? 'tenant' : 'tenants'}
                  {window ? ` · ${window}` : ''}
                </Badge>
              }
            >
              {tenants.length === 0 ? (
                <EmptyState
                  icon={Landmark}
                  title="No tenant data"
                  body="The governance stores are not running, so the accessor returned an empty snapshot. This is lite mode, not an empty platform."
                />
              ) : (
                <Table>
                  <THead>
                    <TH className="text-left">Tenant</TH>
                    <TH className="text-left">Spend / cap</TH>
                    <TH className="text-left">Tokens / cap</TH>
                    <TH className="text-right">USD left</TH>
                    <TH className="text-right">Calls</TH>
                  </THead>
                  <TBody>
                    {tenants.map((t) => (
                      <TenantRow
                        key={t.id}
                        name={t.name}
                        tenantId={t.id}
                        budget={tenantBudgets.get(t.id)}
                      />
                    ))}
                  </TBody>
                </Table>
              )}
            </DataPanel>

            {/* ── Where the money went ────────────────────────────────────────── */}
            <DataPanel
              className="rounded-lg"
              eyebrow="aegis.governance · usage ledger"
              title="Cost by model"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Figure>{byModel.length}</Figure> {byModel.length === 1 ? 'model' : 'models'}
                </Badge>
              }
              footer={
                byModel.length > 8 ? (
                  <Receipt
                    variant="inline"
                    label="Showing"
                    origin={`the 8 costliest of ${byModel.length} models`}
                    detail="the tail is in the ledger, not summarised here"
                  />
                ) : undefined
              }
            >
              {byModel.length === 0 ? (
                <Absence
                  figure="Cost by model"
                  why="The usage ledger returned no per-model rows for this window."
                  needed="At least one recorded call carrying a model name."
                />
              ) : (
                <CostByModel rows={byModel} total={usage?.total_cost_usd ?? 0} />
              )}
            </DataPanel>
          </div>

          {/* ── Users + roles ─────────────────────────────────────────────────── */}
          <DataPanel
            className="rounded-lg"
            eyebrow="aegis.governance · RBAC"
            title="Users & roles"
            maxHeight={360}
            actions={
              <Badge tone="neutral" className="gap-1.5">
                <Users className="size-3" aria-hidden />
                <Figure>{users.length}</Figure> {users.length === 1 ? 'user' : 'users'}
              </Badge>
            }
          >
            {users.length === 0 ? (
              <EmptyState
                icon={Users}
                title="No users in scope"
                body="The governance stores are not running, so no roster could be read."
              />
            ) : (
              <Table>
                <THead>
                  <TH className="text-left">User</TH>
                  <TH className="text-left">Role</TH>
                  <TH className="text-left">Tenant</TH>
                  <TH className="text-left">Personal cap</TH>
                  <TH className="text-right">ID</TH>
                </THead>
                <TBody>
                  {users.map((u) => {
                    const ub = userBudgets.get(u.id)
                    return (
                      <TR key={u.id} className="align-top">
                        <TD className="text-sm font-medium text-foreground">{u.username}</TD>
                        <TD>
                          <Badge tone="neutral" className="font-mono">
                            {u.role}
                          </Badge>
                        </TD>
                        <TD>
                          <Figure className="text-muted-foreground">
                            {u.tenant_id == null ? 'platform' : `#${u.tenant_id}`}
                          </Figure>
                        </TD>
                        <TD className="min-w-[10rem]">
                          {ub ? (
                            <BulletBar
                              label="USD"
                              used={ub.cost_usd_used}
                              cap={ub.budget.usd_cap}
                              format={fmtUsd}
                            />
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              inherits the tenant cap
                            </span>
                          )}
                        </TD>
                        <TD className="whitespace-nowrap text-right">
                          <Figure className="text-muted-foreground">{`#${u.id}`}</Figure>
                        </TD>
                      </TR>
                    )
                  })}
                </TBody>
              </Table>
            )}
          </DataPanel>

          {/* ── Audit tail ────────────────────────────────────────────────────── */}
          <DataPanel
            className="rounded-lg"
            eyebrow="aegis.governance · audit"
            title="Recent audit tail"
            maxHeight={360}
            actions={
              <Badge tone="neutral" className="gap-1.5">
                <ScrollText className="size-3" aria-hidden />
                <Figure>{audit.length}</Figure> {audit.length === 1 ? 'entry' : 'entries'}
              </Badge>
            }
          >
            {audit.length === 0 ? (
              <EmptyState
                icon={ScrollText}
                title="No audit entries"
                body="The governance stores are not running. The full trail, when they are, lives on the Audit page."
              />
            ) : (
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
            )}
          </DataPanel>
        </>
      )}
    </div>
  )
}

/**
 * Client entry for the Governance section — gated on a reachable backend.
 *
 * The `TooltipProvider` is not decoration: `InfoTip` is a Radix tooltip, and Radix
 * throws rather than degrading when one is used outside a provider. The screen carries
 * an `InfoTip` because DESIGN.md §4 puts a mechanism in a tooltip instead of on the
 * page, so the provider is part of that decision.
 */
export function GovernanceMount(): ReactElement {
  return (
    <BackendGate>
      <TooltipProvider>
        <GovernanceView />
      </TooltipProvider>
    </BackendGate>
  )
}
