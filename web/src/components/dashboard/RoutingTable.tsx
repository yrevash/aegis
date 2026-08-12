'use client'

import { Route } from 'lucide-react'
import type { ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'

/**
 * The effective model routing map (role → model) from `GET /metrics`. Admin-only
 * — it exposes how heterogeneous routing sends cheap work to small models, the
 * mechanism behind the small-model-share KPI.
 */
export function RoutingTable({ routing }: { routing: Record<string, string> }): ReactElement {
  const rows = Object.entries(routing)
  return (
    <Card>
      <CardHeader className="flex-row items-center gap-2 space-y-0">
        <Route className="size-4 text-agent" />
        <CardTitle>Model routing</CardTitle>
      </CardHeader>
      <CardContent>
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-border/70 text-left">
              <th className="eyebrow pb-2 font-normal">Role</th>
              <th className="eyebrow pb-2 text-right font-normal">Deployment</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([role, model]) => (
              <tr key={role} className="border-b border-border/40 last:border-0">
                <td className="py-2 font-medium text-foreground capitalize">{role}</td>
                <td className="py-2 text-right font-mono text-[0.78rem] text-agent-ink">{model}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </CardContent>
    </Card>
  )
}
