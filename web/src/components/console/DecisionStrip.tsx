'use client'

import type { ReactElement, ReactNode } from 'react'

import { ActionVerdict } from '@/components/ml/DualVerdict'
import { CountUp, RevealOnScroll } from '@/components/shared'
import { Card } from '@/components/primitives/card'
import { InfoTip } from '@/components/primitives/InfoTip'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

/** One glance stat cell. Numbers lead; the label is a quiet eyebrow. */
function Cell({
  label,
  info,
  tone,
  children,
  index,
  phaseKey,
}: {
  label: string
  info?: ReactNode
  tone?: string
  children: ReactNode
  index: number
  phaseKey: string
}): ReactElement {
  return (
    <RevealOnScroll className="min-w-0" delayMs={index * 40}>
      <Card className="h-full min-w-0 p-3.5">
        <div className="flex items-center gap-1.5">
          <span className="eyebrow truncate">{label}</span>
          {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
        </div>
        {/* Re-key on the run phase so the figure cross-fades thinking → decided. */}
        <div key={phaseKey} className="animate-reveal mt-1.5 min-w-0">
          <span className={cn('t-metric block truncate leading-none', tone)}>{children}</span>
        </div>
      </Card>
    </RevealOnScroll>
  )
}

/**
 * The Decision strip (§4.1 hero): the outcome read in a single glance — whether
 * the guardrails held, how many sources backed the answer, and what it cost —
 * with the proposed action and its gate beneath. Numbers count up on arrival and
 * cross-fade as the run moves from thinking to decided, so the jury reads the
 * verdict before opening any panel. No jargon on the face; the honest terms live
 * in each ⓘ.
 */
export function DecisionStrip({ state }: { state: RunState }): ReactElement {
  const { guardrails, retrievalScores, candidates, usage, toolCalls } = state

  const fired = guardrails.filter((g) => g.verdict !== 'pass').length
  const checks = guardrails.length
  const sources = retrievalScores.length || candidates
  const cost = usage?.cost_usd ?? null

  const decided = state.finishedStatus != null
  const phaseKey = decided ? 'decided' : state.running ? 'thinking' : 'idle'

  const hasVerdict = toolCalls.length > 0 || state.approvalQueued !== null
  const dash = <span className="text-muted-foreground/50">&mdash;</span>

  return (
    <div className="space-y-3">
      {/* Metric tiles wrap 2-up and only reach 3-up at the widest breakpoint, so
          the display figures (e.g. the run cost) never clip inside the narrower
          centre column at mid-desktop widths. */}
      <div className="grid grid-cols-2 gap-3 2xl:grid-cols-3">
        <Cell
          label="Guardrails"
          index={0}
          phaseKey={phaseKey}
          tone={fired > 0 ? 'text-block-ink' : 'text-ok-ink'}
          info="Injection, PII, and schema checks on every request."
        >
          {checks > 0 ? (
            <span className="inline-flex items-baseline gap-1.5">
              {fired > 0 ? (
                <>
                  <CountUp value={fired} />
                  <span className="text-sm font-medium text-muted-foreground">fired</span>
                </>
              ) : (
                <>
                  <span aria-hidden>&#10003;</span>
                  <span className="text-sm font-medium text-muted-foreground">
                    <CountUp value={checks} /> checks
                  </span>
                </>
              )}
            </span>
          ) : (
            dash
          )}
        </Cell>

        <Cell
          label="Sources"
          index={1}
          phaseKey={phaseKey}
          tone="text-graph-ink"
          info="Documents recalled, reranked, and used to ground the answer."
        >
          {sources > 0 ? <CountUp value={sources} /> : dash}
        </Cell>

        <Cell
          label="Cost"
          index={2}
          phaseKey={phaseKey}
          tone="text-foreground"
          info="Token spend for this run, after caching and small-model routing."
        >
          {cost != null ? (
            <CountUp value={cost} format={(n) => `$${n.toFixed(4)}`} />
          ) : (
            dash
          )}
        </Cell>
      </div>

      {hasVerdict && (
        <div className="rounded-lg border border-border/70 bg-surface-2/40 px-3.5 py-2.5">
          <ActionVerdict toolCalls={toolCalls} queued={state.approvalQueued !== null} />
        </div>
      )}
    </div>
  )
}