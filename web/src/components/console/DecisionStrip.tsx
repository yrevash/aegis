'use client'

import { Bot, Inbox, ShieldAlert, ShieldCheck, ShieldQuestion } from 'lucide-react'
import { createContext, useContext, type ReactElement, type ReactNode } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import type { RiskLevel } from '@/lib/stream'
import type { RunState } from '@/state/runReducer'

import { DEFAULT_RUN_MODE, type RunMode } from './runMode'
import { WidthCell } from './WidthReceipt'

/**
 * The width the composer asked for, as this turn was sent.
 *
 * It travels by context rather than by prop because the strip is mounted by
 * `AnswerBlock`, which is handed a `RunState` and nothing about the request that
 * produced it. The width cell needs both — asked and ran — since stating only the second
 * is exactly the silent-override this console has already been wrong about once. The
 * turn provides it; the default is Auto, which asks for nothing and so can be overridden
 * by nothing.
 */
export const RequestedWidth = createContext<RunMode>(DEFAULT_RUN_MODE)

/**
 * One glance cell: a quiet eyebrow, then the figure it labels.
 *
 * The figure sits in a fixed-height slot so a cell that reads "not measured" while the
 * run streams is exactly as tall as the same cell reading `$0.0041` once it settles.
 * That is the whole point of the slot: this strip mounts the moment a run stops, above
 * an answer that is still arriving, and a cell that grew by four pixels on arrival would
 * push the answer down the screen mid-sentence.
 */
function Cell({
  label,
  info,
  children,
}: {
  label: string
  info?: ReactNode
  children: ReactNode
}): ReactElement {
  return (
    <div className="min-w-0 bg-card px-4 py-3">
      <div className="flex items-center gap-1.5">
        <span className="eyebrow truncate">{label}</span>
        {info != null && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </div>
      <div className="mt-1.5 flex min-h-7 min-w-0 items-baseline gap-1.5">{children}</div>
    </div>
  )
}

/**
 * A figure this run did not record, stated in the slot the figure would have taken.
 *
 * DESIGN.md §1: a figure that cannot be sourced is never drawn as a number. An em-dash
 * was doing that job here, and an em-dash is not a sentence — a reader cannot tell it
 * apart from a zero that failed to render.
 */
function NotMeasured({ what }: { what: string }): ReactElement {
  return (
    <span className="text-[0.8125rem] leading-5 text-muted-foreground" title={what}>
      not measured
    </span>
  )
}

/** Low / medium / high, as the reserved status set — never colour on its own. */
function riskVariant(risk: RiskLevel): 'ok' | 'risk' | 'block' {
  return risk === 'high' ? 'block' : risk === 'medium' ? 'risk' : 'ok'
}

/**
 * Every call this run proposed, one row each — not just the first of them.
 *
 * The headline-only readout beside it (`ActionVerdict`) names `toolCalls[0]`, which is
 * true of a run that proposed one call and quietly wrong about a run that proposed
 * three. A gate that shows one action while three are pending is the exact defect this
 * project has already fixed once on the approval card, so the strip lists them.
 */
function ProposedActions({
  calls,
  queued,
}: {
  calls: RunState['toolCalls']
  queued: boolean
}): ReactElement {
  return (
    <div className="border-t border-border px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Bot aria-hidden className="size-3.5 shrink-0 text-blue-700" />
        <span className="eyebrow">
          {calls.length > 1 ? `Actions · ${calls.length}` : 'Action'}
        </span>
        {queued && (
          <Badge tone="risk" className="ml-auto gap-1">
            <Inbox aria-hidden className="size-3" /> Awaiting a human
          </Badge>
        )}
      </div>

      {calls.length === 0 ? (
        <p className="mt-1.5 flex items-center gap-1.5 text-[0.8rem] text-muted-foreground">
          <ShieldQuestion aria-hidden className="size-3.5 shrink-0" />
          No action proposed.
        </p>
      ) : (
        <ul className="mt-1.5 flex flex-col gap-1">
          {calls.map((call) => (
            <li key={call.call_id} className="flex min-w-0 items-center gap-2">
              <Figure className="min-w-0 flex-1 truncate font-medium text-foreground">
                {call.tool}
              </Figure>
              <Badge tone={riskVariant(call.risk)} className="shrink-0 uppercase">
                {call.risk} risk
              </Badge>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}

/**
 * The decision strip — the outcome of a run read in a single glance.
 *
 * Whether the guardrails held, how many sources backed the answer, what it cost, how
 * wide it ran, and every call it proposed. It is one bordered strip with hairline-divided
 * cells rather than four floating cards: four cards inside a tab inside a turn is the
 * third nested surface DESIGN.md §4 refuses, and the cells here are one reading, not
 * four panels.
 *
 * **Width is the fourth cell rather than its own section.** It was a bordered panel of
 * its own between the lanes and the answer — the sixth place a settled turn stated
 * routing. It is the same kind of fact as the other three and it is read in the same
 * glance, so it is read here. See {@link WidthCell}, which keeps the null-routing rule
 * intact: a run that emitted no `routing` event still states that, in the slot the width
 * would have occupied.
 *
 * **Nothing counts up.** The figures were counted-up on arrival and cross-faded on every
 * phase change, which is the one motion DESIGN.md §6 names outright — a spend figure
 * that animates is a spend figure that looks approximate. They are set in {@link Figure}
 * now: mono, tabular, and still.
 */
export function DecisionStrip({ state }: { state: RunState }): ReactElement {
  const { guardrails, retrievalScores, candidates, usage, toolCalls } = state
  const requested = useContext(RequestedWidth)

  const fired = guardrails.filter((g) => g.verdict !== 'pass').length
  const checks = guardrails.length
  const sources = retrievalScores.length || candidates
  const cost = usage?.cost_usd ?? null

  const hasVerdict = toolCalls.length > 0 || state.approvalQueued !== null

  return (
    <section
      aria-label="Decision"
      className="@container/decision overflow-hidden rounded-lg border border-border bg-card"
    >
      {/* Container, not viewport: the strip sits in the thread column, which is about
          560px at 1440 with the rails out — `sm:grid-cols-3` fired at a 640px window and
          split it into three 180px cells regardless.

          The hairlines are a `gap-px` over a border-coloured ground rather than
          `divide-x`, because `divide-*` keys off DOM order and not grid position: with
          four cells wrapped into two columns it drew a stray rule down the middle of the
          second row. This draws the grid's own gaps, at any column count. */}
      <div className="grid grid-cols-1 gap-px bg-border @[26rem]/decision:grid-cols-2 @[46rem]/decision:grid-cols-4">
        <Cell
          label="Guardrails"
          info="Injection, PII and schema checks, on the request and on the answer."
        >
          {checks === 0 ? (
            <NotMeasured what="No guardrail reported a verdict on this run." />
          ) : fired > 0 ? (
            <>
              <ShieldAlert aria-hidden className="size-4 shrink-0 self-center text-block-ink" />
              <Figure size="stat" className="text-block-ink">
                {fired}
              </Figure>
              <span className="text-sm font-medium text-muted-foreground">
                fired of {checks}
              </span>
            </>
          ) : (
            <>
              <ShieldCheck aria-hidden className="size-4 shrink-0 self-center text-ok-ink" />
              <Figure size="stat" className="text-ok-ink">
                {checks}
              </Figure>
              <span className="text-sm font-medium text-muted-foreground">held</span>
            </>
          )}
        </Cell>

        <Cell
          label="Sources"
          info="Documents recalled, reranked, and used to ground the answer."
        >
          {sources > 0 ? (
            <>
              <Figure size="stat" className="text-blue-700">
                {sources}
              </Figure>
              <span className="text-sm font-medium text-muted-foreground">
                {sources === 1 ? 'document' : 'documents'}
              </span>
            </>
          ) : (
            <NotMeasured what="This run retrieved nothing, so no document backs the answer." />
          )}
        </Cell>

        <Cell
          label="Cost"
          info="Token spend for this run, after caching and small-model routing."
        >
          {cost != null ? (
            <Figure size="stat" unit="USD">
              ${cost.toFixed(4)}
            </Figure>
          ) : (
            <NotMeasured what="No usage was reported for this run." />
          )}
        </Cell>

        <Cell
          label="Width"
          info="How wide the supervisor ran this turn, and who decided — the router, you, the tenant default, or the tenant’s parallel-agent cap."
        >
          <WidthCell state={state} requested={requested} />
        </Cell>
      </div>

      {hasVerdict && (
        <ProposedActions calls={toolCalls} queued={state.approvalQueued !== null} />
      )}

      <div className="px-4 pt-0 pb-3">
        <Receipt
          label="Measured from"
          origin={
            state.traceId === null ? 'this run’s event stream' : `run ${state.traceId.slice(0, 12)}`
          }
          detail="guardrail · retrieval · usage events"
        />
      </div>
    </section>
  )
}
