'use client'

import { ShieldCheck, Wrench } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { refusalSentence } from '@/components/settings/settingsCatalogue'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { getToolRoster, type ToolRosterResponse } from '@/lib/api/console'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * The effective tool roster — "6 of 9", and why the other three.
 *
 * A **projection of the settings above it**, which is why it lives on this screen and
 * why it re-reads on `refreshKey`: `agent.gate_min_risk` is literally the gate floor
 * this panel prints, so a write that the server accepted has by definition moved it.
 * Which keys feed the roster is the server's business — a hand-kept list of "keys that
 * matter" here would be one more thing to forget the day a fifteenth setting lands.
 *
 * Read-only, deliberately: pinning a subset for one run needs a per-run field the query
 * request does not carry, and a pin control that changed nothing would be the exact
 * defect this screen exists to remove.
 */

/** How a tool's deciding layer is labelled and toned. */
const DECIDED_BY: Record<string, { label: string; tone: BadgeTone }> = {
  platform: { label: 'Available', tone: 'ok' },
  persona: { label: 'Not in your persona', tone: 'neutral' },
  tenant: { label: 'Human approval required', tone: 'risk' },
}

export function ToolRosterCard({ refreshKey }: { refreshKey: number }): ReactElement {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [roster, setRoster] = useState<ToolRosterResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!hydrated) return
    let alive = true
    getToolRoster(token)
      .then((data) => {
        if (alive) {
          setRoster(data)
          setError(null)
        }
      })
      .catch((failure: unknown) => {
        if (alive) setError(refusalSentence(failure))
      })
    return () => {
      alive = false
    }
  }, [token, hydrated, refreshKey])

  return (
    <Card className="rounded-lg">
      <CardHeader
        title="Tools"
        eyebrow="platform ∩ persona, then the tenant’s gate floor"
        actions={
          roster === null ? null : (
            <Badge tone="neutral" className="gap-1.5">
              <Wrench aria-hidden className="size-3" />
              <Figure>{roster.allowed_count}</Figure> of <Figure>{roster.total}</Figure> available
            </Badge>
          )
        }
      />
      <CardBody>
        {error !== null ? (
          <ErrorState error={error} />
        ) : roster === null ? (
          <LoadingState rows={3} label="Reading the roster…" />
        ) : (
          <>
            <p className="mb-3 flex flex-wrap items-center gap-1.5 text-[0.74rem] text-muted-foreground">
              <ShieldCheck aria-hidden className="size-3.5" />
              Persona <Figure className="text-foreground">{roster.persona}</Figure> · human gate at{' '}
              <Figure className="text-foreground">{roster.gate_min_risk}</Figure> risk and above
            </p>
            <div className="w-full overflow-x-auto">
              <table className="w-full min-w-[34rem] text-left text-sm">
                <thead>
                  <tr className="border-b border-border">
                    <th scope="col" className="eyebrow pb-2 pr-4 font-normal">
                      Tool
                    </th>
                    <th scope="col" className="eyebrow pb-2 pr-4 font-normal">
                      Risk
                    </th>
                    <th scope="col" className="eyebrow pb-2 font-normal">
                      Status
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {roster.rows.map((tool) => {
                    const decided = DECIDED_BY[tool.decided_by] ?? {
                      label: tool.decided_by,
                      tone: 'neutral' as BadgeTone,
                    }
                    return (
                      <tr key={tool.name} className="border-t border-border align-top">
                        <td className="py-3 pr-4">
                          <Figure className="text-foreground">{tool.name}</Figure>
                          <p className="mt-1 max-w-lg text-[0.74rem] leading-snug text-muted-foreground">
                            {tool.description}
                          </p>
                        </td>
                        <td className="py-3 pr-4 text-sm text-muted-foreground">{tool.risk}</td>
                        <td className="py-3">
                          <Badge tone={decided.tone}>{decided.label}</Badge>
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
            <Receipt
              origin="GET /v1/console/tools"
              detail="platform allowlist ∩ persona, then anything at or above the tenant’s gate floor is marked for a human"
              className="mt-4"
            />
          </>
        )}
      </CardBody>
    </Card>
  )
}
