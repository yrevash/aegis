'use client'

import { ShieldCheck, Wrench } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { refusalSentence } from '@/components/settings/settingsCatalogue'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
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
    <Card>
      <CardHeader
        title="Tools"
        eyebrow="platform ∩ persona, then the tenant's gate floor"
        actions={
          roster === null ? null : (
            <Badge tone="agent">
              <Wrench aria-hidden className="size-3" />
              {roster.allowed_count} of {roster.total} available
            </Badge>
          )
        }
      />
      <CardBody>
        {error !== null ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : roster === null ? (
          <p className="py-8 text-center text-sm text-muted-foreground">Reading the roster…</p>
        ) : (
          <>
            <p className="mb-3 flex flex-wrap items-center gap-1.5 text-[0.74rem] text-muted-foreground">
              <ShieldCheck aria-hidden className="size-3.5" />
              Persona <span className="font-mono text-foreground">{roster.persona}</span> · human
              gate at <span className="font-mono text-foreground">{roster.gate_min_risk}</span> risk
              and above
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                    <th className="pb-2 pr-4 font-medium">Tool</th>
                    <th className="pb-2 pr-4 font-medium">Risk</th>
                    <th className="pb-2 font-medium">Status</th>
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
                          <p className="font-mono text-[0.78rem] text-foreground">{tool.name}</p>
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
          </>
        )}
      </CardBody>
    </Card>
  )
}
