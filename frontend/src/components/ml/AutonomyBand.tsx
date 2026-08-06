import { CircleSlash, ShieldCheck, UserCheck } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactElement } from 'react'

import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { MLGate } from '@/state/runReducer'
import type { Abstained } from '@/types/stream'

import { autonomyBand, type AutonomyBandKind } from './autonomy'

export interface AutonomyBandProps {
  mlGate: MLGate | null
  abstained: Abstained | null
  queued?: boolean
  className?: string
}

/** Badge variant + icon per band. */
const BAND_META: Record<AutonomyBandKind, { variant: 'ok' | 'risk' | 'ml'; icon: LucideIcon }> = {
  autonomous: { variant: 'ok', icon: ShieldCheck },
  defer: { variant: 'risk', icon: UserCheck },
  abstain: { variant: 'ml', icon: CircleSlash },
}

/**
 * The graded autonomy band as a badge (autonomous / defer / abstain). This is a
 * presentation-only readout: in the live backend the human gate is driven by the
 * action's TOOL RISK TIER, and ML is a solution signal that never gates, defers,
 * or abstains a run. The three-band grading is populated only by the frontend
 * mock; the badge shows it as supporting evidence, not as the flow decision.
 */
export function AutonomyBand({
  mlGate,
  abstained,
  queued = false,
  className,
}: AutonomyBandProps): ReactElement | null {
  const readout = autonomyBand(mlGate, abstained, queued)
  if (readout.band === null) return null
  const meta = BAND_META[readout.band]
  const Icon = meta.icon
  return (
    <Badge variant={meta.variant} className={cn('uppercase', className)} title={readout.reason ?? undefined}>
      <Icon className="size-3" />
      {readout.label}
    </Badge>
  )
}
