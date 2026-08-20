'use client'

import { ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import type { McpConsole, McpRisk } from '@/lib/api/mcp'
import { cn } from '@/lib/utils'

import { RISKS, gatesAt } from './mcpConsole'

/** The three tiers as a bar segment: one hue, three intensities, darker = tighter. */
const TIER_FILL: Record<McpRisk, string> = {
  low: 'bg-blue-200',
  medium: 'bg-blue-400',
  high: 'bg-blue-700',
}

const TIER_ICON = {
  low: ShieldCheck,
  medium: ShieldQuestion,
  high: ShieldAlert,
} as const

/**
 * Where every tool this deployment knows about sits, and what that costs a call.
 *
 * This card used to carry four counts as well, and they now live in the four-step strip
 * above it — where they mean something, because each one is the count for a step. What
 * is left is the one thing the strip cannot say: the **shape** of the estate. One bar,
 * one hue at three intensities, darker for tighter, so the ranking survives
 * colour-blindness, and every segment carries its count and its word beside it because
 * a status is never told by hue alone (DESIGN.md §2).
 *
 * The row under it is the translation a reader actually needs — not "seven at high" but
 * "seven wait for a person, none run unattended".
 */
export function McpPosture({ data }: { data: McpConsole }): ReactElement {
  const everyTool = [
    ...data.tools.map((tool) => ({ risk: tool.risk, admitted: tool.personas.length })),
    ...data.aegisTools.map((tool) => ({ risk: tool.risk, admitted: tool.personas.length })),
  ]
  const tiers = RISKS.map((risk) => ({
    risk,
    count: everyTool.filter((tool) => tool.risk === risk).length,
  }))
  const total = everyTool.length
  const gated = everyTool.filter(
    (tool) => tool.admitted > 0 && gatesAt(tool.risk, data.gateRisk),
  ).length
  const unattended = everyTool.filter(
    (tool) => tool.admitted > 0 && !gatesAt(tool.risk, data.gateRisk),
  ).length
  const inert = everyTool.filter((tool) => tool.admitted === 0).length

  return (
    <Card>
      <CardHeader
        eyebrow={`GET /v1/mcp/console · gate floor ${data.gateRisk}`}
        title="Every tool this deployment knows, by tier"
        actions={
          <InfoTip label="How the tier bar is drawn">
            One hue at three intensities, darker for tighter, so the ranking survives
            colour-blindness. External tools and Aegis&rsquo;s own are counted together,
            because the gate does not distinguish between them at call time.
          </InfoTip>
        }
      />
      <CardBody className="flex flex-col gap-4">
        {total === 0 ? (
          <p className="text-sm text-muted-foreground">
            No tool is registered yet, external or otherwise.
          </p>
        ) : (
          <>
            <div className="flex h-3 w-full overflow-hidden rounded-full bg-surface-2">
              {tiers.map(({ risk, count }) =>
                count === 0 ? null : (
                  <span
                    key={risk}
                    className={cn(TIER_FILL[risk], 'h-full')}
                    style={{ width: `${(count / total) * 100}%` }}
                    aria-hidden
                  />
                ),
              )}
            </div>
            <ul className="flex flex-wrap gap-x-5 gap-y-1.5">
              {tiers.map(({ risk, count }) => {
                const Icon = TIER_ICON[risk]
                return (
                  <li key={risk} className="flex items-center gap-1.5 text-xs">
                    <span className={cn('size-2 rounded-full', TIER_FILL[risk])} aria-hidden />
                    <Icon className="size-3 text-muted-foreground" aria-hidden />
                    <span className="font-mono text-foreground">{risk}</span>
                    <Figure className="text-muted-foreground">{count}</Figure>
                    <span className="text-muted-foreground">
                      {gatesAt(risk, data.gateRisk) ? 'waits for a person' : 'runs unattended'}
                    </span>
                  </li>
                )
              })}
            </ul>

            <dl className="grid grid-cols-3 gap-px overflow-hidden rounded-lg border border-border bg-border">
              <Outcome
                label="wait for a person"
                value={gated}
                tip="Somebody is admitted, and the tier is at or above this deployment’s gate floor, so a call pauses for an approval before anything happens."
              />
              <Outcome
                label="run unattended"
                value={unattended}
                tip="Somebody is admitted, and the tier is below the gate floor — a call runs and its result enters the answer with nobody seeing it first."
              />
              <Outcome
                label="inert"
                value={inert}
                tip="No persona is admitted, so no agent is ever offered the tool. Discovered is not the same as callable."
              />
            </dl>
          </>
        )}

        <Receipt
          origin="GET /v1/mcp/console"
          detail={`gate floor ${data.gateRisk} · grants and lowered tiers live in the serving process and reset to high on restart, so the degradation is toward the gate and never around it`}
        />
      </CardBody>
    </Card>
  )
}

/** One consequence count — what the tiers above actually mean for a call. */
function Outcome({
  label,
  value,
  tip,
}: {
  label: string
  value: number
  tip: string
}): ReactElement {
  return (
    <div className="bg-card px-4 py-3">
      <dt className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        <InfoTip label={`About “${label}”`}>{tip}</InfoTip>
      </dt>
      <dd className="mt-0.5">
        <Figure size="stat" className="text-foreground">
          {value}
        </Figure>
      </dd>
    </div>
  )
}
