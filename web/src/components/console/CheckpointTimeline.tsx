'use client'

import { Database, MemoryStick, Pause, Play, RotateCw } from 'lucide-react'
import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactElement,
  type ReactNode,
} from 'react'

import { Absence } from '@/components/primitives/Receipt'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { getCheckpointHistory, type CheckpointHistory } from '@/lib/api/checkpoints'
import { cn } from '@/lib/utils'

/**
 * The run's LangGraph checkpoints — the durable-execution evidence, drawn.
 *
 * The human gate is a real `langgraph.types.interrupt`: the graph *pauses on a
 * checkpointer* and is resumed out of band. With `AGENT_CHECKPOINTER=postgres` the pause
 * survives a backend restart. None of that was visible anywhere, so the one question a
 * reviewer actually asks — *did the resume continue from the gate, or re-run the graph?*
 * — had no answer on screen. This is the answer: one tick per persisted checkpoint, the
 * gate marked where it parked, and the continuation drawn hanging off that same tick.
 *
 * Per DESIGN.md §4 the state leads with a mark and the per-item evidence sits one layer
 * down: the strip answers *how many and where did it pause*, and selecting a tick (mouse
 * or arrow keys) reveals that checkpoint's ids and nodes underneath. Nothing is drawn
 * that the server did not send — a run with one checkpoint shows one tick, and a run with
 * none shows a stated absence naming why.
 */
export function CheckpointTimeline({ runId }: { runId: string | null }): ReactElement | null {
  const [history, setHistory] = useState<CheckpointHistory | null>(null)
  const [failed, setFailed] = useState<string | null>(null)
  const [selected, setSelected] = useState<number | null>(null)
  const [nonce, setNonce] = useState(0)
  const ticks = useRef<(HTMLButtonElement | null)[]>([])
  // One thread renders one of these per settled turn, so every id here is per instance.
  const uid = useId()

  useEffect(() => {
    if (runId === null) return
    let live = true
    setFailed(null)
    getCheckpointHistory(runId)
      .then((served) => {
        if (!live) return
        setHistory(served)
        // Open on the checkpoint that carries the story: the gate if there was one,
        // otherwise the last one written.
        const gate = served.checkpoints.findIndex((row) => row.interrupted)
        setSelected(gate >= 0 ? gate : served.checkpoints.length - 1)
      })
      .catch((error: unknown) => {
        if (live) setFailed(error instanceof Error ? error.message : 'unreadable')
      })
    return () => {
      live = false
    }
  }, [runId, nonce])

  const move = useCallback(
    (event: KeyboardEvent<HTMLButtonElement>, index: number, total: number): void => {
      let to = -1
      if (event.key === 'ArrowRight') to = Math.min(index + 1, total - 1)
      else if (event.key === 'ArrowLeft') to = Math.max(index - 1, 0)
      else if (event.key === 'Home') to = 0
      else if (event.key === 'End') to = total - 1
      else return
      event.preventDefault()
      setSelected(to)
      ticks.current[to]?.focus()
    },
    [],
  )

  if (runId === null) return null

  if (failed !== null) {
    return (
      <Absence
        figure="Checkpoints"
        why={`The checkpoint store could not be read for this run (${failed}).`}
      />
    )
  }
  if (history === null) {
    return (
      <Card>
        <CardHeader title="Checkpoints" />
        <CardBody>
          <div className="h-9 animate-pulse rounded-md bg-surface-2/60" />
        </CardBody>
      </Card>
    )
  }

  const rows = history.checkpoints
  const gateIndex = rows.findIndex((row) => row.interrupted)
  const resumeIndex = rows.findIndex(
    (row) =>
      history.resumed_from !== null && row.parent_checkpoint_id === history.resumed_from,
  )
  const active = selected !== null ? rows[selected] : undefined

  return (
    <Card>
      <CardHeader
        title="Checkpoints"
        actions={
          <div className="flex items-center gap-2">
            <Badge tone={history.durable ? 'ok' : 'neutral'}>
              {history.durable ? (
                <Database aria-hidden className="size-3" />
              ) : (
                <MemoryStick aria-hidden className="size-3" />
              )}
              {history.durable ? 'Postgres' : 'In-memory'}
            </Badge>
            <InfoTip label="What a checkpoint is">
              LangGraph writes one checkpoint per super-step. The human gate is an
              interrupt: the graph pauses on the checkpointer and the approval resumes it
              from that checkpoint. A durable store keeps the pause across a restart.
            </InfoTip>
            <button
              type="button"
              aria-label="Reload checkpoints"
              onClick={() => setNonce((n) => n + 1)}
              className={cn(
                'inline-grid size-7 place-items-center rounded-md text-muted-foreground transition-colors',
                'hover:bg-surface-2 hover:text-foreground',
                'outline-none focus-visible:ring-2 focus-visible:ring-ring',
              )}
            >
              <RotateCw aria-hidden className="size-3.5" />
            </button>
          </div>
        }
      />
      <CardBody className="flex min-w-0 flex-col gap-3">
        {rows.length === 0 ? (
          <Absence
            figure="Checkpoints"
            why={
              history.durable
                ? 'This run wrote no checkpoints to the durable store — it ran before durable execution was switched on.'
                : 'The checkpoint store is in-memory, so this run left nothing readable behind.'
            }
            needed="Set AGENT_CHECKPOINTER=postgres and run the query again."
          />
        ) : (
          <>
            <div className="flex flex-wrap items-center gap-1.5">
              <Badge tone="neutral">
                {rows.length} {rows.length === 1 ? 'checkpoint' : 'checkpoints'}
              </Badge>
              <Badge tone={history.entries === 1 ? 'ok' : 'neutral'}>
                {history.entries === 1 ? '1 entry' : `${history.entries} entries`}
              </Badge>
              {gateIndex >= 0 && (
                <Badge tone="risk">
                  <Pause aria-hidden className="size-3" />
                  paused at step {rows[gateIndex]?.step}
                </Badge>
              )}
              {history.resumed_from !== null && resumeIndex >= 0 && (
                <Badge tone="agent">
                  <Play aria-hidden className="size-3" />
                  resumed from step {rows[gateIndex]?.step}
                </Badge>
              )}
              {history.truncated && <Badge tone="neutral">truncated</Badge>}
            </div>

            {/* The strip owns its own horizontal scroll (DESIGN.md §4): a long run must
                never make the thread column scroll sideways. */}
            <div className="min-w-0 overflow-x-auto pb-1">
              <div
                role="group"
                aria-label="Checkpoint timeline"
                className="flex w-max items-end gap-1"
              >
                {rows.map((row, index) => {
                  const isGate = index === gateIndex
                  const afterGate = gateIndex >= 0 && index > gateIndex
                  const isSelected = index === selected
                  return (
                    <div key={row.checkpoint_id} className="flex shrink-0 flex-col items-center gap-1">
                      <span className="grid h-4 place-items-center">
                        {isGate && <Pause aria-hidden className="size-3.5 text-risk-ink" />}
                        {index === resumeIndex && !isGate && (
                          <Play aria-hidden className="size-3.5 text-blue-700" />
                        )}
                      </span>
                      <button
                        type="button"
                        ref={(node) => {
                          ticks.current[index] = node
                        }}
                        tabIndex={isSelected ? 0 : -1}
                        aria-pressed={isSelected}
                        aria-controls={`${uid}-detail`}
                        aria-label={
                          `Step ${row.step}` +
                          (row.produced_by.length > 0 ? `, after ${row.produced_by.join(' and ')}` : '') +
                          (row.interrupted ? ', paused at the approval gate' : '')
                        }
                        onClick={() => setSelected(index)}
                        onKeyDown={(event) => move(event, index, rows.length)}
                        className={cn(
                          'h-9 w-2.5 rounded-sm transition-colors',
                          'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1',
                          isGate
                            ? 'bg-risk hover:bg-risk-ink'
                            : afterGate
                              ? 'bg-blue-700 hover:bg-blue-800'
                              : 'bg-blue-400 hover:bg-blue-600',
                          isSelected && 'ring-2 ring-ring ring-offset-1',
                        )}
                      />
                    </div>
                  )
                })}
              </div>
            </div>

            {gateIndex >= 0 && (
              <ul className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.6875rem] text-muted-foreground">
                <LegendKey className="bg-blue-400" label="before the gate" />
                <LegendKey className="bg-risk" label="paused here" />
                {resumeIndex >= 0 && <LegendKey className="bg-blue-700" label="after approval" />}
              </ul>
            )}

            {active !== undefined && (
              <dl
                id={`${uid}-detail`}
                aria-live="polite"
                /* Two columns only once a column can hold a 36-character checkpoint id
                   on one line. The threshold is in `rem`, so it rises with the text-size
                   setting — at 125% a 28rem trigger split the panel and then wrapped the
                   id onto a second line one character in. */
                className="grid min-w-0 gap-x-4 gap-y-1.5 rounded-md bg-surface-2/50 p-3 text-xs @[46rem]/trace:grid-cols-2"
              >
                <Detail label="step">
                  <span className="font-mono">{active.step}</span>
                  {active.source === 'input' && <span className="ml-1.5">entered the graph</span>}
                </Detail>
                <Detail label="node">
                  {active.produced_by.length > 0 ? active.produced_by.join(', ') : '—'}
                  {active.next_nodes.length > 0 && (
                    <span className="text-muted-foreground"> → {active.next_nodes.join(', ')}</span>
                  )}
                </Detail>
                <Detail label="checkpoint">
                  <span className="font-mono break-all">{active.checkpoint_id}</span>
                </Detail>
                <Detail label="parent">
                  <span className="font-mono break-all">{active.parent_checkpoint_id ?? '—'}</span>
                </Detail>
              </dl>
            )}
          </>
        )}
      </CardBody>
    </Card>
  )
}

/** One legend key: a swatch and the word that actually distinguishes it (DESIGN.md §2). */
function LegendKey({ className, label }: { className: string; label: string }): ReactElement {
  return (
    <li className="flex items-center gap-1.5">
      <span aria-hidden className={cn('size-2 rounded-[2px]', className)} />
      {label}
    </li>
  )
}

/** One labelled fact in the disclosure. `min-w-0` so a long id wraps rather than pushes. */
function Detail({ label, children }: { label: string; children: ReactNode }): ReactElement {
  return (
    <div className="flex min-w-0 flex-col">
      <dt className="eyebrow">{label}</dt>
      <dd className="min-w-0 text-foreground">{children}</dd>
    </div>
  )
}
