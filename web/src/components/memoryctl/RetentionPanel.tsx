'use client'

import { CircleCheck, Loader2, Timer, Trash2 } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import {
  forgetMemorySubject,
  getMemoryRetention,
  sweepMemoryRetention,
} from '@/lib/api/client'
import type { MemorySubjectRow } from '@/lib/api/memory'

import { atRiskBars } from '@/components/memory/memoryCharts'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { useAsync } from '@/components/memory/useAsync'

import { erasureReceipt, erasureWarning, retentionClause, sourceLabel } from './memoryControl'

/**
 * How long memory is kept, what is already past that, and the two ways to remove it.
 *
 * Retention is the half of this task that nobody asks for and everybody needs. The rest
 * of the memory subsystem is built to *keep*: a fact is superseded rather than
 * overwritten, the forgetting sweep archives rather than deletes, and every write leaves
 * an audit row. Without a horizon the store grows for the life of the deployment, which
 * is a cost problem and a privacy problem wearing one shape — "we keep every word you
 * ever typed, indefinitely" is not a posture a tenant can be shown.
 *
 * So this panel says three things a screen has to say for retention to count as a
 * control rather than a background job:
 *
 * - **The horizon, and where it came from.** The `(value, source)` badge is §7.4's rule
 *   and it applies to a number that is merely displayed exactly as much as to one that
 *   is edited: a value with no provenance is a value nobody can reason about.
 * - **What is sitting past it right now**, in row counts, before anything is pressed.
 * - **What the sweep will not touch** — the fact-write log survives, because a retention
 *   policy that erased its own evidence would be the opposite of the thing it is for,
 *   and an operator who assumed otherwise would be wrong in the dangerous direction.
 *
 * Erasure is the other button and a different promise: retention removes what has aged
 * out, erasure removes everything about one person on request. Both are real deletes and
 * both report what went.
 */
export function RetentionPanel({
  token,
  subject,
  subjectRow,
  canSweep,
  refreshKey,
  onChanged,
}: {
  token: string | null
  subject: string
  subjectRow: MemorySubjectRow | null
  /** Whether this principal may run the sweep. Administrators only — the server agrees. */
  canSweep: boolean
  refreshKey: number
  onChanged: () => void
}): ReactElement {
  const retention = useAsync(
    () => getMemoryRetention(token, subject),
    [token, subject, refreshKey],
  )
  const [busy, setBusy] = useState(false)
  const [confirmSweep, setConfirmSweep] = useState(false)
  const [confirmErase, setConfirmErase] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  async function run(action: () => Promise<string>): Promise<void> {
    setBusy(true)
    setFailure(null)
    setNote(null)
    try {
      setNote(await action())
      onChanged()
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : 'That did not go through. Try it again.')
    } finally {
      setBusy(false)
    }
  }

  const sweep = (): Promise<void> =>
    run(async () => {
      const result = await sweepMemoryRetention(token, subject)
      setConfirmSweep(false)
      const clause = retentionClause(result.removed)
      return clause === null
        ? 'Nothing was past the horizon, so nothing was removed.'
        : `Removed ${clause}.`
    })

  const erase = (): Promise<void> =>
    run(async () => {
      const result = await forgetMemorySubject(token, subject)
      setConfirmErase(false)
      return erasureReceipt(result)
    })

  const data = retention.state.status === 'ready' ? retention.state.data : null
  const atRisk = data ? atRiskBars(data.at_risk) : []
  const atRiskTotal = atRisk.reduce((sum, bar) => sum + bar.rows, 0)
  // What a sweep would take right now, in counts, for the confirmation below.
  const sweepClause = data ? retentionClause(data.at_risk) : null

  return (
    <div className="@container flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <Timer className="size-4 shrink-0 text-blue-700" aria-hidden />
        <h3 className="t-title text-foreground">How long this is kept</h3>
        <InfoTip label="What a sweep never removes">
          Facts the agent still believes are never removed by age, and the write log is kept
          whatever happens.
        </InfoTip>
        {data !== null && (
          <Badge tone="neutral" className="ml-auto">
            {sourceLabel(data.source)}
          </Badge>
        )}
      </div>

      {retention.state.status === 'loading' && <LoadingRow label="Reading the horizon…" />}
      {retention.state.status === 'error' && <ErrorRow message={retention.state.message} />}

      {data !== null && (
        <>
          <div className="grid gap-4 @3xl:grid-cols-[minmax(0,1fr)_minmax(0,1.3fr)]">
            <dl className="grid min-w-0 grid-cols-2 gap-3 self-start">
              <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 px-3 py-2">
                <dt className="eyebrow">Conversation turns</dt>
                <dd className="mt-0.5 text-foreground">
                  {data.episodic_days > 0 ? (
                    <Figure size="stat" unit="days">
                      {data.episodic_days}
                    </Figure>
                  ) : (
                    <span className="text-sm text-muted-foreground">kept indefinitely</span>
                  )}
                </dd>
              </div>
              <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 px-3 py-2">
                <dt className="eyebrow">Superseded facts</dt>
                <dd className="mt-0.5 text-foreground">
                  {data.closed_fact_days > 0 ? (
                    <Figure size="stat" unit="days">
                      {data.closed_fact_days}
                    </Figure>
                  ) : (
                    <span className="text-sm text-muted-foreground">kept indefinitely</span>
                  )}
                </dd>
              </div>
            </dl>

            <div className="min-w-0">
              <p className="eyebrow mb-1.5">past the horizon now</p>
              {atRiskTotal > 0 ? (
                <BarChart
                  allowDecimals={false}
                  data={atRisk}
                  index="tier"
                  category="rows"
                  color="graph"
                  valueFormatter={(v) => `${v} ${v === 1 ? 'row' : 'rows'}`}
                  height={172}
                />
              ) : (
                // A status hue never travels alone: icon and word both.
                <p className="flex items-center gap-2 py-6 text-sm text-foreground">
                  <CircleCheck className="size-4 shrink-0 text-ok-ink" aria-hidden />
                  Nothing has aged past the horizon
                </p>
              )}
            </div>
          </div>

          {/*
            **The sweep asks first, and names what it will take.**

            It is a real delete — the same class of act as the erase button below
            it — and it shipped as a single unguarded click. The counts are
            already on screen, so the confirmation can say exactly what goes
            rather than asking "are you sure?" about an unnamed quantity, which is
            this file's own rule (see `memoryControl.retentionClause`).
          */}
          {canSweep &&
            (confirmSweep ? (
              <div className="flex flex-col gap-2">
                <p role="alert" className="text-sm break-words text-foreground">
                  {sweepClause === null
                    ? 'Nothing is past the horizon right now, so this would remove nothing.'
                    : `This removes ${sweepClause}. The rows are deleted, not hidden; the write log is kept.`}
                </p>
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="destructive"
                    disabled={busy}
                    onClick={() => void sweep()}
                  >
                    {busy ? (
                      <Loader2
                        className="size-3.5 animate-spin motion-reduce:animate-none"
                        aria-hidden
                      />
                    ) : (
                      <Timer className="size-3.5" aria-hidden />
                    )}
                    {busy ? 'Applying…' : 'Apply the horizon'}
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => setConfirmSweep(false)}>
                    Keep it
                  </Button>
                </div>
              </div>
            ) : (
              <div>
                <Button size="sm" variant="outline" onClick={() => setConfirmSweep(true)}>
                  <Timer className="size-3.5" aria-hidden /> Apply the horizon now
                </Button>
              </div>
            ))}
        </>
      )}

      <div className="border-t border-border pt-3">
        {confirmErase ? (
          <div className="flex flex-col gap-2">
            <p role="alert" className="text-sm break-words text-foreground">
              {erasureWarning(subjectRow)}
            </p>
            <div className="flex flex-wrap items-center gap-2">
              <Button size="sm" variant="destructive" disabled={busy} onClick={() => void erase()}>
                {busy ? (
                  <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
                ) : (
                  <Trash2 className="size-3.5" aria-hidden />
                )}
                {busy ? 'Erasing…' : 'Erase everything'}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setConfirmErase(false)}>
                Keep it
              </Button>
            </div>
          </div>
        ) : (
          <Button size="sm" variant="outline" onClick={() => setConfirmErase(true)}>
            <Trash2 className="size-3.5" aria-hidden /> Erase this record
          </Button>
        )}
      </div>

      {failure !== null && (
        <p role="alert" className="text-sm break-words text-destructive">
          {failure}
        </p>
      )}
      {note !== null && (
        <p role="status" aria-live="polite" className="text-sm break-words text-ok-ink">
          {note}
        </p>
      )}

      {data !== null && (
        <Receipt
          origin="GET /memory/retention"
          detail={`${data.scope} scope${data.keeps_audit ? ' · write log never swept' : ''}`}
        />
      )}
    </div>
  )
}
