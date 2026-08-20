'use client'

import { Building2, Coins } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardHeader, CardBody } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { getBudgets, getTenants, getUsers } from '@/lib/api/client'
import type { AdminUser, Budget, Tenant } from '@/lib/api/types'

import { refusalSentence, type AdminTier } from './adminForms'

/** A cap is money, so it is formatted as money — grouped, with a currency mark. */
const USD = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })
import { BudgetForm } from './BudgetForm'
import { CreateTenantForm } from './CreateTenantForm'
import { CreateUserForm } from './CreateUserForm'

/**
 * The three write controls, over the state they change.
 *
 * They share one loader on purpose. A form that posts and shows a toast has not
 * finished: the operator has to **see the new state** — the tenant in the list, the
 * budget on the row it governs — and a console that will not show you what you just
 * did is the defect Phase 6's audit found on the settings screen. So every successful
 * write reloads all three readings, and a created tenant is immediately selectable in
 * the user form's picker and the budget form's scope picker.
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
}: {
  token: string | null
  tier: AdminTier
  ownTenantId: number | null
  /** Told after a user is created, so the roster below refetches. */
  onUsersChanged: () => void
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

  return (
    <div className="flex flex-col gap-4">
      {readFailure != null && <ErrorState error={readFailure} retry={() => void reload()} />}

      <CreateTenantForm token={token} tier={tier} onCreated={() => void reload()} />

      {seesTenants && <TenantList tenants={tenants} budgets={budgets} loading={loading} />}

      <CreateUserForm
        token={token}
        tier={tier}
        tenants={tenants}
        ownTenantId={ownTenantId}
        onCreated={afterUserCreated}
      />

      <BudgetForm
        token={token}
        tier={tier}
        tenants={tenants}
        users={users}
        budgets={budgets}
        onSaved={() => void reload()}
      />

      <BudgetList budgets={budgets} tenants={tenants} users={users} loading={loading} />
    </div>
  )
}

/** Every tenant with the cap that governs it — the proof a create landed. */
function TenantList({
  tenants,
  budgets,
  loading,
}: {
  tenants: readonly Tenant[]
  budgets: readonly Budget[]
  loading: boolean
}): ReactElement {
  return (
    <Card>
      <CardHeader
        title="Tenants"
        actions={
          <Badge tone="neutral">
            <Figure>{tenants.length}</Figure>
          </Badge>
        }
      />
      <CardBody>
        {loading ? (
          <LoadingState rows={3} label="Reading the tenants…" />
        ) : tenants.length === 0 ? (
          <EmptyState
            icon={Building2}
            title="No tenants yet"
            body="Every tenant on this platform appears here with the cap that governs it. The form above creates the first one — and a tenant cannot be created without a spend cap."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  <th scope="col" className="eyebrow pb-2 font-normal">
                    Tenant
                  </th>
                  <th scope="col" className="eyebrow pb-2 font-normal">
                    Status
                  </th>
                  <th scope="col" className="eyebrow pb-2 font-normal">
                    Spend cap
                  </th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => {
                  const cap = budgets.find((b) => b.scope_type === 'tenant' && b.scope_id === t.id)
                  return (
                    <tr key={t.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2.5 font-medium text-foreground">
                        {t.name}{' '}
                        <Figure className="text-muted-foreground/70">{`#${t.id}`}</Figure>
                      </td>
                      <td className="py-2.5 text-muted-foreground">{t.status}</td>
                      <td className="py-2.5">
                        <Figure className="text-foreground">
                          {cap?.usd_cap != null
                            ? `${USD.format(cap.usd_cap)} a ${cap.window}`
                            : 'uncapped'}
                        </Figure>
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

/** Every cap in force, named by what it governs. */
function BudgetList({
  budgets,
  tenants,
  users,
  loading,
}: {
  budgets: readonly Budget[]
  tenants: readonly Tenant[]
  users: readonly AdminUser[]
  loading: boolean
}): ReactElement {
  return (
    <Card>
      <CardHeader
        title="Caps in force"
        actions={
          <Badge tone="neutral">
            <Figure>{budgets.length}</Figure>
          </Badge>
        }
      />
      <CardBody>
        {loading ? (
          <LoadingState rows={3} label="Reading the caps…" />
        ) : budgets.length === 0 ? (
          <EmptyState
            icon={Coins}
            title="Nothing is capped"
            body="Every call runs unlimited until a budget says otherwise. Set one with the form above — a cap can name a tenant or a single user."
          />
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  {['Governs', 'Window', 'Spend', 'Tokens', 'Rpm · tpm'].map((head) => (
                    <th key={head} scope="col" className="eyebrow pb-2 font-normal">
                      {head}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {budgets.map((b) => (
                  <tr key={`${b.scope_type}-${b.scope_id}-${b.window}`} className="border-b border-border/40 last:border-0">
                    <td className="py-2.5 text-foreground">
                      <Badge tone="neutral">{b.scope_type}</Badge>{' '}
                      {b.scope_type === 'tenant'
                        ? (tenants.find((t) => t.id === b.scope_id)?.name ?? `#${b.scope_id}`)
                        : (users.find((u) => u.id === b.scope_id)?.username ?? `#${b.scope_id}`)}
                    </td>
                    <td className="py-2.5 text-muted-foreground">{b.window}</td>
                    <td className="py-2.5">
                      <Figure className="text-foreground">
                        {b.usd_cap != null ? USD.format(b.usd_cap) : '—'}
                      </Figure>
                    </td>
                    <td className="py-2.5">
                      <Figure className="text-foreground">{b.token_cap ?? '—'}</Figure>
                    </td>
                    <td className="py-2.5">
                      <Figure className="text-foreground">
                        {`${b.rpm ?? '—'} · ${b.tpm ?? '—'}`}
                      </Figure>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  )
}

