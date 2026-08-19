'use client'

import { Loader2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { getBudgets, getTenants, getUsers } from '@/lib/api/client'
import type { AdminUser, Budget, Tenant } from '@/lib/api/types'

import { refusalSentence, type AdminTier } from './adminForms'
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
      {readFailure != null && (
        <p role="alert" className="rounded-lg border border-risk/40 bg-risk/5 px-3 py-2 text-[0.78rem] text-risk-ink">
          {readFailure}
        </p>
      )}

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
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <CardTitle>Tenants</CardTitle>
        <Badge variant="secondary">{tenants.length}</Badge>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Waiting what="tenants" />
        ) : tenants.length === 0 ? (
          <p className="py-6 text-sm text-muted-foreground">No tenants yet. Create the first one above.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[520px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  <th className="eyebrow pb-2 font-normal">Tenant</th>
                  <th className="eyebrow pb-2 font-normal">Status</th>
                  <th className="eyebrow pb-2 font-normal">Spend cap</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((t) => {
                  const cap = budgets.find((b) => b.scope_type === 'tenant' && b.scope_id === t.id)
                  return (
                    <tr key={t.id} className="border-b border-border/40 last:border-0">
                      <td className="py-2.5 font-medium text-foreground">
                        {t.name}
                        <span className="ml-1.5 font-mono text-[0.62rem] text-muted-foreground/70">#{t.id}</span>
                      </td>
                      <td className="py-2.5 text-muted-foreground">{t.status}</td>
                      <td className="py-2.5 font-mono text-[0.72rem] text-foreground">
                        {cap?.usd_cap != null ? `$${cap.usd_cap} a ${cap.window}` : 'uncapped'}
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
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
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <CardTitle>Caps in force</CardTitle>
        <Badge variant="secondary">{budgets.length}</Badge>
      </CardHeader>
      <CardContent>
        {loading ? (
          <Waiting what="budgets" />
        ) : budgets.length === 0 ? (
          <p className="py-6 text-sm text-muted-foreground">
            Nothing is capped. Every call runs unlimited until a budget says otherwise.
          </p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full min-w-[640px] text-sm">
              <thead>
                <tr className="border-b border-border/70 text-left">
                  <th className="eyebrow pb-2 font-normal">Governs</th>
                  <th className="eyebrow pb-2 font-normal">Window</th>
                  <th className="eyebrow pb-2 font-normal">Spend</th>
                  <th className="eyebrow pb-2 font-normal">Tokens</th>
                  <th className="eyebrow pb-2 font-normal">Rpm · tpm</th>
                </tr>
              </thead>
              <tbody>
                {budgets.map((b) => (
                  <tr key={`${b.scope_type}-${b.scope_id}-${b.window}`} className="border-b border-border/40 last:border-0">
                    <td className="py-2.5 text-foreground">
                      <Badge variant="outline">{b.scope_type}</Badge>{' '}
                      {b.scope_type === 'tenant'
                        ? (tenants.find((t) => t.id === b.scope_id)?.name ?? `#${b.scope_id}`)
                        : (users.find((u) => u.id === b.scope_id)?.username ?? `#${b.scope_id}`)}
                    </td>
                    <td className="py-2.5 text-muted-foreground">{b.window}</td>
                    <td className="py-2.5 font-mono text-[0.72rem] text-foreground">
                      {b.usd_cap != null ? `$${b.usd_cap}` : '—'}
                    </td>
                    <td className="py-2.5 font-mono text-[0.72rem] text-foreground">{b.token_cap ?? '—'}</td>
                    <td className="py-2.5 font-mono text-[0.72rem] text-foreground">
                      {b.rpm ?? '—'} · {b.tpm ?? '—'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </CardContent>
    </Card>
  )
}

/** The one waiting line both lists use. */
function Waiting({ what }: { what: string }): ReactElement {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
      <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden /> Loading {what}…
    </div>
  )
}
