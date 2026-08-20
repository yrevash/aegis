'use client'

import { AlertTriangle, ShieldAlert, ShieldCheck, ShieldQuestion, Users } from 'lucide-react'
import type { ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
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
 * What a risk tier *does* on this deployment — the one thing the screen has to teach.
 *
 * A reader who does not already know MCP cannot get anything from a bar reading
 * "low 1 · medium 1 · high 1". A tier is not a label and it is not a severity score:
 * it is the rule that decides whether a call stops and waits for a person. So the card
 * states that rule for all three tiers, in the present tense, whether or not any tool
 * happens to sit at one — and the count is a *column* of that table rather than its
 * subject.
 *
 * Everything is derived from `agent.gate_min_risk`, the deployment's own floor, so the
 * sentences stay true when a tenant tightens it. Hard-coding "high stops" is the way
 * this card would quietly start lying.
 *
 * The three figures underneath are the consequence for the estate as it stands now:
 * how many tools an agent may actually call and a person will see, how many run with
 * nobody watching, and how many were discovered and admitted to nobody. External tools
 * and Aegis's own are counted together, because the gate does not distinguish between
 * them at call time.
 */
export function McpPosture({ data }: { data: McpConsole }): ReactElement {
  const everyTool = [
    ...data.tools.map((tool) => ({ risk: tool.risk, admitted: tool.personas.length })),
    ...data.aegisTools.map((tool) => ({ risk: tool.risk, admitted: tool.personas.length })),
  ]
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
        title="What a risk tier does to a call, here"
        actions={
          <InfoTip label="Where the rule comes from">
            Every sentence below is read off this deployment&rsquo;s own gate floor,
            <span className="font-mono"> agent.gate_min_risk</span>, which is a setting an
            administrator can tighten. Nothing on this card is hard-coded, so it stays true
            when the floor moves.
          </InfoTip>
        }
      />
      <CardBody className="flex flex-col gap-4">
        <ul className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border md:grid-cols-3">
          {RISKS.map((risk) => (
            <TierRow
              key={risk}
              risk={risk}
              gateRisk={data.gateRisk}
              count={everyTool.filter((tool) => tool.risk === risk).length}
              total={total}
            />
          ))}
        </ul>

        {total === 0 ? (
          <p className="text-sm text-muted-foreground">
            No tool is registered yet, external or otherwise, so nothing sits at any tier.
          </p>
        ) : (
          <dl className="grid grid-cols-1 gap-px overflow-hidden rounded-lg border border-border bg-border sm:grid-cols-3">
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
        )}

        <Receipt
          origin="GET /v1/mcp/console"
          detail={`gate floor ${data.gateRisk} · grants and lowered tiers live in the serving process and reset to high on restart, so the degradation is toward the gate and never around it`}
        />
      </CardBody>
    </Card>
  )
}

/** One tier: what it costs a call, and how much of the estate sits there. */
function TierRow({
  risk,
  gateRisk,
  count,
  total,
}: {
  risk: McpRisk
  gateRisk: McpRisk
  count: number
  total: number
}): ReactElement {
  const Icon = TIER_ICON[risk]
  const stops = gatesAt(risk, gateRisk)
  const tone: BadgeTone = risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
  const share = total > 0 ? (count / total) * 100 : 0

  return (
    <li className="flex flex-col gap-2 bg-card p-4">
      <span className="flex flex-wrap items-center gap-1.5">
        <Badge tone={tone} className="gap-1 whitespace-nowrap">
          <Icon className="size-3" aria-hidden />
          {risk}
        </Badge>
        {stops ? (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
            <ShieldQuestion className="size-3.5 text-muted-foreground" aria-hidden />
            waits for a person
          </span>
        ) : (
          <span className="inline-flex items-center gap-1 text-xs font-medium text-block-ink">
            <AlertTriangle className="size-3.5" aria-hidden />
            runs unattended
          </span>
        )}
      </span>

      <p className="text-xs leading-5 text-muted-foreground">
        {stops
          ? `At or above the floor (${gateRisk}), so an agent calling one of these stops and an approval is raised before anything happens.`
          : `Below the floor (${gateRisk}), so an agent calls it and the result enters the answer with nobody seeing it first.`}
      </p>

      <div className="mt-auto flex items-center gap-2">
        <Figure size="stat" className="text-foreground">
          {count}
        </Figure>
        <span className="text-xs text-muted-foreground">
          {count === 1 ? 'tool sits here' : 'tools sit here'}
        </span>
      </div>
      <div
        className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={`${count} of ${total} tools at ${risk}`}
      >
        <span className={cn('block h-full', TIER_FILL[risk])} style={{ width: `${share}%` }} />
      </div>
    </li>
  )
}

/** One consequence count — what the tiers above actually mean for the estate now. */
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
        <Users className="size-3" aria-hidden />
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
