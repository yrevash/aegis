'use client'

/**
 * The signature element — the winning sentence rendered as a live pipeline.
 *
 * "Every autonomous action is grounded, guarded, approved, and fully traced."
 * Each clause is a stage that lights in its own signal hue the moment the run
 * reaches it, so the jury watches the trust stack assemble in real time. This is
 * the one bold element; everything else is quiet.
 *
 * Every stage must be decidable from the run's own event stream. Two former
 * stages ("uncertainty-bounded", "explainable") read a conformal interval and a
 * SHAP list off an ML step that the agent graph no longer runs — they could only
 * ever have stayed dark, so they are gone rather than dark.
 */

import { Check } from 'lucide-react'
import type { CSSProperties, ReactElement } from 'react'

import { isBeatSignal, type Beat } from '@/components/console/motion'
import { SIGNALS, type Signal } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

interface Stage {
  key: string
  label: string
  signal: Signal
  done: (s: RunState) => boolean
}

const STAGES: Stage[] = [
  {
    key: 'grounded',
    label: 'Grounded',
    signal: 'graph',
    // Lights once retrieval actually returned ranked passages for the answer.
    done: (s) => s.retrievalScores.length > 0 || s.provenance != null,
  },
  {
    key: 'guarded',
    label: 'Guarded',
    signal: 'block',
    done: (s) => s.guardrails.some((g) => g.stage === 'output') || s.finishedStatus === 'blocked',
  },
  {
    key: 'approved',
    label: 'Human-approved',
    signal: 'risk',
    // Only lights when the run actually paused at the gate AND a human approval
    // let a tool succeed — a rejected action (tool_result ok=false) stays dark.
    done: (s) => s.awaitedApproval && s.toolResults.some((r) => r.ok),
  },
  {
    key: 'traced',
    label: 'Fully traced',
    signal: 'agent',
    done: (s) => s.finishedStatus != null,
  },
]

interface TrustBarProps {
  state: RunState
  /** Current active-beat, so the matching subsystem chip pulses in lockstep. */
  beat?: Beat | null
  /** True when no run is active — drives the idle shimmer sweep. */
  idle?: boolean
}

/** Renders the trust pipeline, lighting stages as the run completes them. */
export function TrustBar({ state, beat = null, idle = false }: TrustBarProps): ReactElement {
  return (
    <div className="relative flex flex-wrap items-center gap-x-2 gap-y-2 overflow-hidden rounded-lg border border-border bg-card px-3 py-2.5">
      {/* Idle attract-loop: a slow shimmer sweep across the dormant bar. */}
      {idle && (
        <span
          aria-hidden
          className="animate-trust-shimmer pointer-events-none absolute inset-y-0 -left-1/3 w-1/3 bg-gradient-to-r from-transparent via-blue-400/10 to-transparent"
        />
      )}
      <span className="eyebrow mr-1">Trust stack</span>
      {STAGES.map((stage, i) => {
        const done = stage.done(state)
        const token = SIGNALS[stage.signal]
        const beating = isBeatSignal(beat, stage.signal)
        return (
          <div key={stage.key} className="flex items-center gap-2">
            <div
              className={cn(
                'relative flex items-center gap-1.5 rounded-lg border px-2.5 py-1 transition-[background-color,border-color,color] duration-500 motion-reduce:transition-none',
                done ? cn(token.border, token.bg) : 'border-border bg-surface-2',
              )}
            >
              {/* Active-beat ring — re-keyed on seq so it re-fires each event. */}
              {beating && beat && (
                <span
                  key={beat.seq}
                  aria-hidden
                  className="animate-beat pointer-events-none absolute inset-0 rounded-lg"
                  style={{ ['--beat']: token.hex } as CSSProperties}
                />
              )}
              <span
                className={cn(
                  'grid size-4 place-items-center rounded-full border text-[0.6rem] transition-colors',
                  done ? cn(token.border, token.text) : 'border-border text-muted-foreground/60',
                )}
                style={done ? { boxShadow: `0 0 10px -2px ${token.hex}` } : undefined}
              >
                {done ? <Check className="size-2.5" strokeWidth={3} aria-hidden /> : i + 1}
              </span>
              <span
                className={cn(
                  'text-xs font-medium whitespace-nowrap transition-colors',
                  done ? token.text : 'text-muted-foreground',
                )}
              >
                {stage.label}
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <span
                className={cn(
                  'h-px w-3 transition-colors duration-500',
                  done ? 'bg-border' : 'bg-border/40',
                )}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}