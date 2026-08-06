import { Loader2, Users } from 'lucide-react'
import { useState, type ReactElement, type ReactNode } from 'react'

import { getTenants, getUsers } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { TenantsResponse, UsersResponse } from '@/types/api'

import { StatusDot } from './StatusDot'
import { useAsync } from './useAsync'

/** Role → badge variant. */
function roleVariant(role: string): 'ml' | 'risk' | 'secondary' {
  if (role === 'platform_admin') return 'ml'
  if (role === 'tenant_admin') return 'risk'
  return 'secondary'
}

/** Role → readable label (the raw role stays in the badge for auditors). */
function roleLabel(role: string): string {
  if (role === 'platform_admin') return 'platform admin'
  if (role === 'tenant_admin') return 'tenant admin'
  return role
}

/**
 * Users — the members of each tenant, filtered by tenant scope. Mirrors
 * `GET /admin/users?tenant_id=`; roles carry the access each member has.
 */
export function UsersView({ token }: { token: string | null }): ReactElement {
  const [tenantId, setTenantId] = useState<number | null>(null)
  const tenants = useAsync<TenantsResponse>(() => getTenants(token), [token])
  const users = useAsync<UsersResponse>(() => getUsers(token, tenantId), [token, tenantId])

  const activeCount =
    users.state.status === 'ready' ? users.state.data.rows.filter((u) => u.is_active).length : null

  return (
    <Card>
      <CardHeader className="flex-row flex-wrap items-center gap-2 space-y-0">
        <Users className="size-4 text-graph-ink" />
        <CardTitle>Users</CardTitle>
        {activeCount != null && (
          <span className="font-mono text-[0.68rem] text-muted-foreground">{activeCount} active</span>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-1">
          <FilterChip active={tenantId === null} onClick={() => setTenantId(null)}>
            All
          </FilterChip>
          {tenants.state.status === 'ready' &&
            tenants.state.data.rows.map((t) => (
              <FilterChip key={t.id} active={tenantId === t.id} onClick={() => setTenantId(t.id)}>
                {t.name}
              </FilterChip>
            ))}
        </div>
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {users.state.status === 'loading' && (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading users…
          </div>
        )}
        {users.state.status === 'error' && (
          <div className="py-10 text-sm text-block-ink">Could not load users. {users.state.message}</div>
        )}
        {users.state.status === 'ready' && users.state.data.rows.length === 0 && (
          <div className="py-10 text-sm text-muted-foreground">No users in this scope.</div>
        )}
        {users.state.status === 'ready' && users.state.data.rows.length > 0 && (
          <table className="w-full min-w-[620px] text-sm">
            <thead>
              <tr className="border-b border-border/70 text-left">
                {['User', 'Email', 'Role', 'Tenant', 'Status'].map((h) => (
                  <th key={h} className="eyebrow pb-2 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {users.state.data.rows.map((u, i) => (
                <tr
                  key={u.id}
                  className="animate-trace-in border-b border-border/40 transition-colors last:border-0 hover:bg-surface-2/50"
                  style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
                >
                  <td className="py-2.5 font-medium text-foreground">{u.username}</td>
                  <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">
                    {u.email ?? '—'}
                  </td>
                  <td className="py-2.5">
                    <Badge variant={roleVariant(u.role)}>{roleLabel(u.role)}</Badge>
                  </td>
                  <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">
                    {u.tenant_id != null ? `#${u.tenant_id}` : '—'}
                  </td>
                  <td className="py-2.5">
                    <StatusDot ok={u.is_active} label={u.is_active ? 'active' : 'disabled'} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}

/** A small filter pill for the tenant scope selector. */
function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: ReactNode
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        'rounded-md border px-2 py-0.5 font-mono text-[0.68rem] transition-colors',
        active
          ? 'border-primary bg-primary text-primary-foreground'
          : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
      )}
    >
      {children}
    </button>
  )
}
