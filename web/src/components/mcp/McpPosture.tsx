'use client'

import { Network, ShieldAlert, ShieldCheck, ShieldQuestion, Wrench } from 'lucide-react'
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
 * The posture strip — four counts and one bar, above a page that had none.
 *
 * The MCP console carried the highest prose count in the product and zero
 * quantitative marks: everything a reader needed had to be inferred from
 * paragraphs. These are the four questions the page is actually asked — *how many
 * peers, how many of their tools did we find, how many can anybody call, and
 * where does the gate sit* — answered as figures, with the paragraphs relocated
 * into their tips (DESIGN.md §4).
 *
 * The bar under them is the tier distribution across **every** tool this
 * deployment knows about, external and Aegis's own, with a marker where the gate
 * floor falls. One hue at three intensities, darker for tighter, so it survives
 * colour-blindness — and each segment carries its count and its word, because a
 * status is never told by hue alone.
 */
export function McpPosture({ data }: { data: McpConsole }): ReactElement {
  const enabled = data.servers.filter((server) => server.enabled).length
  const callable = data.tools.filter(
    (tool) => tool.callableNow && tool.personas.length > 0,
  ).length
  const everyTool = [
    ...data.tools.map((tool) => tool.risk),
    ...data.aegisTools.map((tool) => tool.risk),
  ]
  const tiers = RISKS.map((risk) => ({
    risk,
    count: everyTool.filter((value) => value === risk).length,
  }))
  const total = everyTool.length
  const ungated = tiers
    .filter(({ risk }) => !gatesAt(risk, data.gateRisk))
    .reduce((n, { count }) => n + count, 0)

  return (
    <Card>
      <CardHeader
        eyebrow="GET /v1/mcp/console"
        title="Reach, and what is behind the gate"
        actions={
          <InfoTip label="What declaring a server does and does not grant">
            Declaring a server says where to look for tools. It grants nothing: every tool a
            peer advertises arrives at HIGH risk and callable by nobody until a platform
            admin admits it, per named tool. Whatever a tool returns passes the TOOL_RESULT
            rail before it reaches any prompt.
          </InfoTip>
        }
      />
      <CardBody className="flex flex-col gap-5">
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-4">
          <Tile
            icon={Network}
            label="Peers declared"
            value={data.servers.length}
            note={`${enabled} enabled`}
            tip="An external MCP server this deployment knows the address of. A disabled peer keeps its configuration, but its tools leave the agent's payload entirely — a model cannot see them and cannot try them."
          />
          <Tile
            icon={Wrench}
            label="Tools discovered"
            value={data.tools.length}
            note="from tools/list"
            tip="Reading a peer's tool list opens a connection to a third party, so it happens when you press Test and not when this page loads. Nothing here is discovered on render."
          />
          <Tile
            icon={ShieldCheck}
            label="Callable now"
            value={callable}
            note={`of ${data.tools.length} external`}
            tip="A tool is callable only when its server is enabled and at least one persona is admitted to it. Everything else is discovered and inert."
          />
          <Tile
            icon={ShieldAlert}
            label="Gate floor"
            value={data.gateRisk}
            note={`${ungated} of ${total} run unattended`}
            tip="agent.gate_min_risk. A tool at or above this tier stops for a human approval; anything below it runs without a human seeing it first."
          />
        </div>

        <div>
          <p className="eyebrow mb-2 inline-flex items-center gap-1">
            risk tier · every tool this deployment knows
            <InfoTip label="How the tier bar is drawn">
              One hue at three intensities, darker for tighter, so the ranking survives
              colour-blindness. The dashed marker is this deployment&rsquo;s gate floor —
              everything to its left of it runs unattended.
            </InfoTip>
          </p>
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
              <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
                {tiers.map(({ risk, count }) => {
                  const Icon = TIER_ICON[risk]
                  const gated = gatesAt(risk, data.gateRisk)
                  return (
                    <li key={risk} className="flex items-center gap-1.5 text-xs">
                      <span className={cn('size-2 rounded-full', TIER_FILL[risk])} aria-hidden />
                      <Icon className="size-3 text-muted-foreground" aria-hidden />
                      <span className="font-mono text-foreground">{risk}</span>
                      <Figure className="text-muted-foreground">{count}</Figure>
                      <span className="text-muted-foreground">
                        {gated ? 'stops at the gate' : 'unattended'}
                      </span>
                    </li>
                  )
                })}
              </ul>
            </>
          )}
        </div>

        <Receipt
          origin="GET /v1/mcp/console"
          detail={`gate floor ${data.gateRisk} · grants and lowered tiers live in the serving process and reset to high on restart, so the degradation is toward the gate and never around it`}
        />
      </CardBody>
    </Card>
  )
}

/** One count in the posture strip — label above the figure, and the prose in a tip. */
function Tile({
  icon: Icon,
  label,
  value,
  note,
  tip,
}: {
  icon: typeof Network
  label: string
  value: number | string
  note: string
  tip: string
}): ReactElement {
  return (
    <div className="bg-card p-4">
      <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
        <Icon className="size-3.5" aria-hidden />
        {label}
        <InfoTip label={`About ${label}`}>{tip}</InfoTip>
      </p>
      <Figure size="display" className="mt-1 block text-foreground">
        {value}
      </Figure>
      <p className="mt-1 text-xs text-muted-foreground">{note}</p>
    </div>
  )
}
