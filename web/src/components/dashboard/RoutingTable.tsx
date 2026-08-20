'use client'

import { Route } from 'lucide-react'
import type { ReactElement } from 'react'

import { Card, CardContent, CardHeader, CardTitle } from '@/components/primitives/card'
import { Figure } from '@/components/primitives/Figure'

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
        <Route className="size-4 text-blue-700" aria-hidden />
        <CardTitle>Model routing</CardTitle>
      </CardHeader>
      <CardContent>
        {/* A deployment id is an unbroken identifier of unbounded length, so the
            table scrolls inside its own container rather than widening the page
            (DESIGN.md §5 — the page body never scrolls horizontally). */}
        <div className="w-full overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border/70 text-left">
                <th scope="col" className="eyebrow pb-2 font-normal">
                  Role
                </th>
                <th scope="col" className="eyebrow pb-2 text-right font-normal">
                  Deployment
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map(([role, model]) => (
                <tr key={role} className="border-b border-border/40 last:border-0">
                  <th scope="row" className="py-2 text-left font-medium text-foreground capitalize">
                    {role}
                  </th>
                  <td className="py-2 text-right whitespace-nowrap">
                    <Figure className="text-[0.78rem]">
                      <span translate="no">{model}</span>
                    </Figure>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}
