import { Building2, Loader2 } from 'lucide-react'
import type { ReactElement } from 'react'

import { getTenants } from '@/api/client'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import type { TenantsResponse } from '@/types/api'

import { StatusDot } from './StatusDot'
import { useAsync } from './useAsync'

/** Local date from an ISO timestamp. */
function formatDate(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleDateString()
}

/** Platform view of tenants (enterprise clients) and their status. */
export function TenantsView({ token }: { token: string | null }): ReactElement {
  const { state } = useAsync<TenantsResponse>(() => getTenants(token), [token])

  const activeCount =
    state.status === 'ready' ? state.data.rows.filter((t) => t.status === 'active').length : null

  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Building2 className="size-4 text-agent-ink" />
        <CardTitle>Tenants</CardTitle>
        {state.status === 'ready' && (
          <div className="ml-auto flex items-center gap-2">
            {activeCount != null && (
              <span className="font-mono text-[0.68rem] text-muted-foreground">
                {activeCount} active
              </span>
            )}
            <Badge variant="secondary">{state.data.rows.length} total</Badge>
          </div>
        )}
      </CardHeader>
      <CardContent className="overflow-x-auto">
        {state.status === 'loading' && (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading tenants…
          </div>
        )}
        {state.status === 'error' && (
          <div className="py-10 text-sm text-block-ink">Could not load tenants. {state.message}</div>
        )}
        {state.status === 'ready' && (
          <table className="w-full min-w-[520px] text-sm">
            <thead>
              <tr className="border-b border-border/70 text-left">
                {['Tenant', 'Status', 'Created', 'ID'].map((h) => (
                  <th key={h} className="eyebrow pb-2 font-normal">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {state.data.rows.map((t, i) => (
                <tr
                  key={t.id}
                  className="animate-trace-in border-b border-border/40 transition-colors last:border-0 hover:bg-surface-2/50"
                  style={{ animationDelay: `${Math.min(i, 8) * 40}ms` }}
                >
                  <td className="py-2.5 font-medium text-foreground">{t.name}</td>
                  <td className="py-2.5">
                    <StatusDot ok={t.status === 'active'} label={t.status} />
                  </td>
                  <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">
                    {formatDate(t.created_at)}
                  </td>
                  <td className="py-2.5 font-mono text-[0.72rem] text-muted-foreground">#{t.id}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </CardContent>
    </Card>
  )
}
