'use client'

import { Bot, Inbox, ShieldQuestion } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { cn } from '@/lib/utils'
import type { RiskLevel, ToolCall } from '@/lib/stream'

/** The minimal tool-call shape the decision needs (envelope-free friendly). */
export type ProposedToolCall = Pick<ToolCall, 'call_id' | 'tool' | 'args' | 'risk'>

export interface ActionVerdictProps {
  /** Tool calls the agent proposed; `toolCalls[0]` is the headline action. */
  toolCalls: ProposedToolCall[]
  /** Whether the run was queued to the durable approvals inbox. */
  queued?: boolean
  className?: string
}

/** Map a risk level to the matching badge variant (low healthy, high blocks). */
function riskVariant(risk: RiskLevel): 'ok' | 'risk' | 'block' {
  return risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
}

/**
 * The compact action-and-gate readout that anchors the Decision strip (§4.1):
 * the agent's proposed action with its declared risk tier, plus whether that
 * action was parked in the human approvals inbox. The gate is driven by the
 * tool's risk tier and nothing else, so the copy here reads as an outcome, not a
 * mechanism. Renders nothing until an action is proposed or the run is queued.
 */
export function ActionVerdict({
  toolCalls,
  queued = false,
  className,
}: ActionVerdictProps): ReactElement | null {
  const proposed = toolCalls[0] ?? null
  if (proposed === null && !queued) return null

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {proposed ? (
        <>
          <Bot className="size-3.5 shrink-0 text-blue-700" aria-hidden />
          <span className="eyebrow normal-case tracking-normal text-muted-foreground">Action</span>
          <span className="min-w-0 truncate font-mono text-[0.8rem] font-medium text-foreground">
            {proposed.tool}
          </span>
          <Badge tone={riskVariant(proposed.risk)} className="uppercase">
            {proposed.risk} risk
          </Badge>
        </>
      ) : (
        <>
          <ShieldQuestion className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
          <span className="text-[0.8rem] text-muted-foreground">No action proposed</span>
        </>
      )}
      {queued && (
        <Badge tone="risk" className="ml-auto gap-1">
          <Inbox className="size-3" aria-hidden /> Awaiting a human
        </Badge>
      )}
    </div>
  )
}