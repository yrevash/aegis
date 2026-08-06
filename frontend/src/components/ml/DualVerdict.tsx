import { Bot, ShieldQuestion } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { MLGate } from '@/state/runReducer'
import type { Abstained, RiskLevel, ToolCall } from '@/types/stream'

import { AutonomyBand } from './AutonomyBand'

/** The minimal tool-call shape the decision needs (envelope-free friendly). */
export type ProposedToolCall = Pick<ToolCall, 'call_id' | 'tool' | 'args' | 'risk'>

export interface ActionVerdictProps {
  /** Tool calls the agent proposed; `toolCalls[0]` is the headline action. */
  toolCalls: ProposedToolCall[]
  /** Bounded-autonomy gate readout (drives the autonomous / gate badge). */
  mlGate: MLGate | null
  /** Abstain outcome, if any — drives the band to "Abstain". */
  abstained?: Abstained | null
  /** Whether the run was queued to the durable inbox — drives the band to "Defer". */
  queued?: boolean
  className?: string
}

/** Map a risk level to the matching badge variant (low healthy, high blocks). */
function riskVariant(risk: RiskLevel): 'ok' | 'risk' | 'block' {
  return risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
}

/**
 * The compact action-and-gate readout that anchors the Decision strip (§4.1):
 * the agent's proposed action with its risk tier, and the graded autonomy band
 * (autonomous / awaiting a human / abstained). The human gate is driven by the
 * action's TOOL RISK — ML is a supporting signal, never the flow decider — so
 * the copy here reads as an outcome, not a mechanism. Renders nothing until an
 * action is proposed or the gate resolves.
 */
export function ActionVerdict({
  toolCalls,
  mlGate,
  abstained = null,
  queued = false,
  className,
}: ActionVerdictProps): ReactElement | null {
  const proposed = toolCalls[0] ?? null
  if (proposed === null && mlGate === null && abstained === null && !queued) return null

  return (
    <div className={cn('flex flex-wrap items-center gap-2', className)}>
      {proposed ? (
        <>
          <Bot className="size-3.5 shrink-0 text-agent-ink" />
          <span className="eyebrow normal-case tracking-normal text-muted-foreground">Action</span>
          <span className="min-w-0 truncate font-mono text-[0.8rem] font-medium text-foreground">
            {proposed.tool}
          </span>
          <Badge variant={riskVariant(proposed.risk)} className="uppercase">
            {proposed.risk} risk
          </Badge>
        </>
      ) : (
        <>
          <ShieldQuestion className="size-3.5 shrink-0 text-muted-foreground" />
          <span className="text-[0.8rem] text-muted-foreground">No action proposed</span>
        </>
      )}
      <AutonomyBand mlGate={mlGate} abstained={abstained} queued={queued} className="ml-auto" />
    </div>
  )
}
