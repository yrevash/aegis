'use client'

import { Building2, Coins, KeyRound, Users } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement, type ReactNode } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { getBudgets, getTenants, getUsers } from '@/lib/api/client'
import { cn } from '@/lib/utils'
import type { AdminUser, Budget, Tenant } from '@/lib/api/types'

import { AccessDrawer } from './AccessDrawer'
import { refusalSentence, type AdminTier } from './adminForms'

/** A cap is money, so it is formatted as money — grouped, with a currency mark. */
const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

import { BudgetForm } from './BudgetForm'
import { CreateTenantForm } from './CreateTenantForm'
import { CreateUserForm } from './CreateUserForm'

/** Which write the drawer is showing. */
export type AccessWrite = 'tenant' | 'user' | 'cap'

/** The drawer's three tabs, in the order an operator does them. */
const WRITE_TABS: { id: AccessWrite; label: string }[] = [
  { id: 'tenant', label: 'New tenant' },
  { id: 'user', label: 'New user' },
  { id: 'cap', label: 'Set a cap' },
]

/**
 * The state this screen governs, and — behind a drawer — the three writes that change it.
 *
 * The readings share one loader on purpose. A form that posts and shows a toast has not
 * finished: the operator has to **see the new state** — the tenant in the list, the
 * budget on the row it governs — and a console that will not show you what you just
 * did is the defect Phase 6's audit found on the settings screen. So every successful
 * write reloads all three readings, and a created tenant is immediately selectable in
 * the user form's picker and the budget form's scope picker.
 *
 * **What changed is where the forms live.** They used to be three always-open cards
 * stacked above the tables, so the screen opened on empty inputs and the answer to
 * *"who can do what here?"* began below the fold. They are now the same three forms in
 * {@link AccessDrawer}, reached from one control in the page header. Nothing about the
 * writes themselves moved: the same endpoints, the same validation, the same refusal
 * sentences, and `NotYours` still renders in the tab a tenant admin may not use — a
 * hidden refusal is a hidden rule.
 *
 * `GET /admin/tenants` is `require_platform_admin`. A tenant admin is never sent it —
 * a card whose data was fetched only to be refused would render an error that means
 * nothing to the person reading it.
 */
export function AdminControls({
  token,
  tier,
  ownTenantId,
  onUsersChanged,
  openWrite,
  onCloseWrite,
  onOpenWrite,
  children,
}: {
  token: string | null
  tier: AdminTier
  ownTenantId: number | null
  /** Told after a user is created, so the roster below refetches. */
  onUsersChanged: () => void
  /** The write the drawer is showing, or `null` when it is closed. */
  openWrite: AccessWrite | null
  onCloseWrite: () => void
  /** Switch the drawer to another write — the tabs, and the empty-state shortcuts. */
  onOpenWrite: (write: AccessWrite) => void
  /**
   * What belongs between the counting strip and the tenant / cap tables — the roster
   * and the seats. They are the *subject* of this screen, so they sit directly under
   * the counts; the tables they depend on sit beneath them.
   */
  children?: ReactNode
}): ReactElement {
  const [tenants, setTenants] = useState<Tenant[]>([])
  const [users, setUsers] = useState<AdminUser[]>([])
  const [budgets, setBudgets] = useState<Budget[]>([])
  const [loading, setLoading] = useState(true)
  const [readFailure, setReadFailure] = useState<string | null>(null)

  const seesTenants = tier === 'platform'

  const reload = useCallback((): Promise<void> => {
    return Promise.all([
      seesTenants ? getTenants(token).then((r) => r.rows) : Promise.resolve<Tenant[]>([]),
      getUsers(token).then((r) => r.rows),
      getBudgets(token).then((r) => r.rows),
    ]).then(
      ([nextTenants, nextUsers, nextBudgets]) => {
        setTenants(nextTenants)
        setUsers(nextUsers)
        setBudgets(nextBudgets)
        setReadFailure(null)
        setLoading(false)
      },
      (error: unknown) => {
        setReadFailure(refusalSentence(error))
        setLoading(false)
      },
    )
  }, [token, seesTenants])

  useEffect(() => {
    void reload()
  }, [reload])

  const afterUserCreated = (): void => {
    void reload()
    onUsersChanged()
  }

  /** Caps that name one user rather than a whole tenant — the narrowest money rule. */
  const userCaps = budgets.filter((b) => b.scope_type === 'user').length

  return (
    <div className="flex flex-col gap-6">
      {readFailure != null && <ErrorState error={readFailure} retry={() => void reload()} />}

      {/* ── What this screen governs, counted ─────────────────────────────────── */}
      <div
        className={cn(
          'grid grid-cols-2 gap-4 [&>*]:min-w-0',
          seesTenants ? 'lg:grid-cols-3' : 'lg:grid-cols-2',
        )}
      >
        {seesTenants && (
          <StatCard
            label="Tenants"
            value={loading ? '—' : String(tenants.length)}
            icon={Building2}
            source="aegis.admin · /admin/tenants"
            className="rounded-lg"
          />
        )}
        <StatCard
          label="Users in scope"
          value={loading ? '—' : String(users.length)}
          icon={Users}
          source="aegis.admin · /admin/users · tenant-scoped server-side"
          className="rounded-lg"
        />
        <StatCard
          label="Caps in force"
          value={loading ? '—' : String(budgets.length)}
          icon={Coins}
          source={`aegis.admin · /admin/budgets · ${userCaps} name one user`}
          className="rounded-lg"
        />
      </div>

      {children}

      {/* ── The tables the writes change ──────────────────────────────────────── */}
      <div className={cn('grid gap-6 [&>*]:min-w-0', seesTenants && 'xl:grid-cols-2')}>
        {seesTenants && (
          <TenantList
            tenants={tenants}
            budgets={budgets}
            loading={loading}
            onCreate={() => onOpenWrite('tenant')}
          />
        )}
        <BudgetList
          budgets={budgets}
          tenants={tenants}
          users={users}
          loading={loading}
          onCreate={() => onOpenWrite('cap')}
        />
      </div>

      <AccessDrawer
        open={openWrite !== null}
        onClose={onCloseWrite}
        title="Manage access"
        subtitle="Onboard a client, provision a seat, or set what either may spend."
      >
        <div
          role="tablist"
          aria-label="Which write"
          className="mb-4 flex flex-wrap gap-1.5 rounded-lg border border-border bg-surface p-1"
        >
          {WRITE_TABS.map((tab) => {
            const active = openWrite === tab.id
            return (
              <button
                key={tab.id}
                type="button"
                role="tab"
                id={`access-tab-${tab.id}`}
                aria-selected={active}
                aria-controls={`access-tabpanel-${tab.id}`}
                onClick={() => onOpenWrite(tab.id)}
                className={cn(
                  'flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition-colors duration-[--dur-fast] outline-none motion-reduce:transition-none focus-visible:ring-2 focus-visible:ring-ring',
                  active
                    ? 'bg-blue-600 text-white'
                    : 'text-muted-foreground hover:bg-surface-2 hover:text-foreground',
                )}
              >
                {tab.label}
              </button>
            )
          })}
        </div>

        <div
          role="tabpanel"
          id={`access-tabpanel-${openWrite ?? 'tenant'}`}
          aria-labelledby={`access-tab-${openWrite ?? 'tenant'}`}
        >
          {openWrite === 'tenant' && (
            <CreateTenantForm token={token} tier={tier} onCreated={() => void reload()} />
          )}
          {openWrite === 'user' && (
            <CreateUserForm
              token={token}
              tier={tier}
              tenants={tenants}
              ownTenantId={ownTenantId}
              onCreated={afterUserCreated}
            />
          )}
          {openWrite === 'cap' && (
            <BudgetForm
              token={token}
              tier={tier}
              tenants={tenants}
              users={users}
              budgets={budgets}
              onSaved={() => void reload()}
            />
          )}
        </div>
      </AccessDrawer>
    </div>
  )
}

/** Every tenant with the cap that governs it — the proof a create landed. */
function TenantList({
  tenants,
  budgets,
  loading,
  onCreate,
}: {
  tenants: readonly Tenant[]
  budgets: readonly Budget[]
  loading: boolean
  onCreate: () => void
}): ReactElement {
  return (
    <DataPanel
      className="rounded-lg"
      eyebrow="aegis.admin · /admin/tenants"
      title="Tenants"
      maxHeight={320}
      actions={
        <Badge tone="neutral" className="gap-1.5">
          <Building2 className="size-3" aria-hidden />
          <Figure>{tenants.length}</Figure>
        </Badge>
      }
    >
      {loading ? (
        <LoadingState rows={3} label="Reading the tenants…" />
      ) : tenants.length === 0 ? (
        <EmptyState
          icon={Building2}
          title="No tenants yet"
          body="Every tenant on this platform appears here with the cap that governs it. A tenant cannot be created without a spend cap."
          action={
            <Button type="button" size="sm" onClick={onCreate}>
              Create the first tenant
            </Button>
          }
        />
      ) : (
        <Table>
          <THead>
            <TH className="text-left">Tenant</TH>
            <TH className="text-left">Status</TH>
            <TH className="text-right">Spend cap</TH>
          </THead>
          <TBody>
            {tenants.map((t) => {
              const cap = budgets.find((b) => b.scope_type === 'tenant' && b.scope_id === t.id)
              return (
                <TR key={t.id}>
                  <TD className="text-sm font-medium text-foreground">
                    {t.name} <Figure className="text-muted-foreground/70">{`#${t.id}`}</Figure>
                  </TD>
                  <TD>
                    <Badge tone={t.status === 'active' ? 'ok' : 'neutral'}>{t.status}</Badge>
                  </TD>
                  <TD className="whitespace-nowrap text-right">
                    <Figure className="text-foreground">
                      {cap?.usd_cap != null
                        ? `${USD.format(cap.usd_cap)} a ${cap.window}`
                        : 'uncapped'}
                    </Figure>
                  </TD>
                </TR>
              )
            })}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}

/** Every cap in force, named by what it governs. */
function BudgetList({
  budgets,
  tenants,
  users,
  loading,
  onCreate,
}: {
  budgets: readonly Budget[]
  tenants: readonly Tenant[]
  users: readonly AdminUser[]
  loading: boolean
  onCreate: () => void
}): ReactElement {
  return (
    <DataPanel
      className="rounded-lg"
      eyebrow="aegis.admin · /admin/budgets"
      title="Caps in force"
      maxHeight={320}
      actions={
        <Badge tone="neutral" className="gap-1.5">
          <Coins className="size-3" aria-hidden />
          <Figure>{budgets.length}</Figure>
        </Badge>
      }
    >
      {loading ? (
        <LoadingState rows={3} label="Reading the caps…" />
      ) : budgets.length === 0 ? (
        <EmptyState
          icon={Coins}
          title="Nothing is capped"
          body="Every call runs unlimited until a budget says otherwise. A cap can name a tenant or a single user."
          action={
            <Button type="button" size="sm" onClick={onCreate}>
              Set the first cap
            </Button>
          }
        />
      ) : (
        <Table>
          <THead>
            <TH className="text-left">Governs</TH>
            <TH className="text-left">Window</TH>
            <TH className="text-right">Spend</TH>
            <TH className="text-right">Tokens</TH>
            <TH className="text-right">Rpm · tpm</TH>
          </THead>
          <TBody>
            {budgets.map((b) => (
              <TR key={`${b.scope_type}-${b.scope_id}-${b.window}`}>
                <TD className="text-sm text-foreground">
                  <span className="flex items-center gap-1.5">
                    {b.scope_type === 'user' ? (
                      <KeyRound className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                    ) : (
                      <Building2 className="size-3 shrink-0 text-muted-foreground" aria-hidden />
                    )}
                    {b.scope_type === 'tenant'
                      ? (tenants.find((t) => t.id === b.scope_id)?.name ?? `#${b.scope_id}`)
                      : (users.find((u) => u.id === b.scope_id)?.username ?? `#${b.scope_id}`)}
                  </span>
                </TD>
                <TD className="text-muted-foreground">{b.window}</TD>
                <TD className="whitespace-nowrap text-right">
                  <Figure className="text-foreground">
                    {b.usd_cap != null ? USD.format(b.usd_cap) : '—'}
                  </Figure>
                </TD>
                <TD className="whitespace-nowrap text-right">
                  <Figure className="text-foreground">
                    {b.token_cap != null ? b.token_cap.toLocaleString('en-US') : '—'}
                  </Figure>
                </TD>
                <TD className="whitespace-nowrap text-right">
                  <Figure className="text-foreground">
                    {`${b.rpm ?? '—'} · ${b.tpm ?? '—'}`}
                  </Figure>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}
