'use client'

import { useEffect, useMemo, useRef, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { SIGNALS } from '@/config/signals'
import { cn } from '@/lib/utils'
import type { RunState } from '@/state/runReducer'

import { deriveActivity, type ActivityItem } from './agentLanes'
import { formatDuration, type Stage } from './stageTimeline'

/**
 * The sequence number a stage opened on, recovered from its key.
 *
 * `stageTimeline` keys a stage `<node>#<seq>` and exposes no `seq` of its own; that
 * module is pure and tested, so the seq is read back here rather than widened there.
 * A node name never contains `#`, so the last one is the separator.
 */
function seqOfStage(stage: Stage): number {
  const seq = Number(stage.key.slice(stage.key.lastIndexOf('#') + 1))
  return Number.isFinite(seq) ? seq : Number.MAX_SAFE_INTEGER
}

/** One line of the feed: either a supervisor event, or the stage in flight. */
type Entry =
  | { kind: 'event'; seq: number; item: ActivityItem }
  | { kind: 'stage'; seq: number; stage: Stage }

/**
 * The run feed — what the supervisor did, and what it is doing right now, in one list.
 *
 * ## Why the stage spine folded into this
 *
 * These were two panels animating side by side: a fourteen-row stage timeline that was
 * always open at roughly 380px, and this rail. They report the same run at two
 * granularities, so a reader watching a fan-out had two competing places to look and no
 * focal point — the single biggest cause of the "too cluttered" verdict.
 *
 * They are one list now. The events that carry **no** agent identity — retrieval, the
 * graph delta, guardrail verdicts, routing, self-checks, memory recall — are the
 * supervisor's, and the **stage in flight** is threaded into them at the sequence number
 * it opened on. Nothing is lost: every finished stage, with its bar and its own
 * `duration_ms`, lives one disclosure away in "All stages".
 *
 * Events that belong to an agent are still excluded — they are that lane's card's, and a
 * retrieval line must never land in an agent's card just because it arrived while that
 * agent was talking.
 */
export function ActivityRail({
  state,
  current = null,
  liveMs = null,
}: {
  state: RunState
  /** The stage still in flight, or `null`. Threaded in as the feed's live line. */
  current?: Stage | null
  /** How long that stage has been running, by this browser's clock. */
  liveMs?: number | null
}): ReactElement {
  const items = deriveActivity(state)
  const endRef = useRef<HTMLLIElement>(null)

  const entries = useMemo<Entry[]>(() => {
    const rows: Entry[] = items.map((item) => ({ kind: 'event', seq: item.seq, item }))
    if (current !== null) rows.push({ kind: 'stage', seq: seqOfStage(current), stage: current })
    return rows.sort((a, b) => a.seq - b.seq)
  }, [items, current])

  // Keep the newest line in view while the run streams.
  useEffect(() => {
    endRef.current?.scrollIntoView({ block: 'nearest' })
  }, [entries.length])

  return (
    <section aria-label="Activity" className="flex min-h-0 min-w-0 flex-col gap-1.5">
      <h4 className="eyebrow">{state.running ? 'Now' : 'Activity'}</h4>

      {entries.length === 0 ? (
        <p className="text-[0.75rem] text-muted-foreground">
          {state.running
            ? 'Retrieval, guardrails and memory report here as they run.'
            : 'Nothing reported outside the agent lanes.'}
        </p>
      ) : (
        <ul className="flex max-h-72 min-h-0 min-w-0 flex-col gap-1 overflow-y-auto pr-1">
          {entries.map((entry, index) => {
            const last = index === entries.length - 1
            if (entry.kind === 'stage') {
              const token = SIGNALS[entry.stage.signal]
              return (
                <li
                  key={entry.stage.key}
                  ref={last ? endRef : undefined}
                  className="animate-trace-in flex min-w-0 flex-col gap-0.5 rounded-md bg-blue-50 px-1.5 py-1"
                >
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span
                      aria-hidden
                      className="animate-pip mt-1 size-1.5 shrink-0 rounded-full"
                      style={{
                        backgroundColor: token.hex,
                        ['--pip-color' as string]: token.hex,
                      }}
                    />
                    <p className="min-w-0 flex-1 truncate text-[0.78rem] font-medium text-blue-700">
                      {entry.stage.label}
                    </p>
                    <Figure
                      className="shrink-0 text-[0.7rem] text-blue-700"
                      label={liveMs === null ? undefined : 'elapsed so far'}
                    >
                      {liveMs === null ? 'starting' : formatDuration(liveMs)}
                    </Figure>
                  </div>
                  {entry.stage.what !== '' && (
                    <p className="pl-3.5 text-[0.72rem] leading-snug text-muted-foreground">
                      {entry.stage.what}
                    </p>
                  )}
                  {/* The rails this stage runs, in order. Shown while it runs, because
                      this is the three-to-eight seconds the console used to spend on a
                      spinner — and a reader who can see what is being screened reads a
                      governed product rather than a slow one. */}
                  {entry.stage.chain.length > 0 && (
                    <p className="flex flex-wrap items-center gap-1 pl-3.5">
                      {entry.stage.chain.map((layer) => (
                        <span
                          key={layer}
                          className="rounded-full border border-border bg-surface px-1.5 py-0.5 font-mono text-[0.62rem] text-muted-foreground"
                        >
                          {layer}
                        </span>
                      ))}
                    </p>
                  )}
                </li>
              )
            }

            const token = SIGNALS[entry.item.signal]
            return (
              <li
                key={entry.item.seq}
                ref={last ? endRef : undefined}
                className="animate-trace-in flex min-w-0 items-baseline gap-2 rounded-md px-1.5 py-1"
              >
                <span
                  aria-hidden
                  className="mt-1 size-1.5 shrink-0 rounded-full"
                  style={{ backgroundColor: token.hex }}
                />
                <div className="min-w-0">
                  <p className={cn('text-[0.78rem] font-medium', token.text)}>
                    {entry.item.title}
                  </p>
                  {entry.item.detail !== '' && (
                    <p className="truncate text-[0.72rem] text-muted-foreground">
                      {entry.item.detail}
                    </p>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
      )}
    </section>
  )
}
