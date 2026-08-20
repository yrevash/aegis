'use client'

import { Timer, Trash2 } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import {
  forgetMemorySubject,
  getMemoryRetention,
  sweepMemoryRetention,
} from '@/lib/api/client'
import type { MemorySubjectRow } from '@/lib/api/memory'

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
  const clause = data ? retentionClause(data.at_risk) : null

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Timer className="size-4 text-blue-700" aria-hidden />
        <h3 className="t-title text-foreground">How long this is kept</h3>
      </div>

      {retention.state.status === 'loading' && <LoadingRow label="Reading the horizon…" />}
      {retention.state.status === 'error' && <ErrorRow message={retention.state.message} />}

      {data !== null && (
        <>
          <dl className="grid grid-cols-2 gap-3">
            <div className="rounded-lg border border-border bg-surface-2/40 px-3 py-2">
              <dt className="eyebrow">Conversation turns</dt>
              <dd className="tabular mt-0.5 font-mono text-sm text-foreground">
                {data.episodic_days > 0 ? `${data.episodic_days} days` : 'kept indefinitely'}
              </dd>
            </div>
            <div className="rounded-lg border border-border bg-surface-2/40 px-3 py-2">
              <dt className="eyebrow">Superseded facts</dt>
              <dd className="tabular mt-0.5 font-mono text-sm text-foreground">
                {data.closed_fact_days > 0
                  ? `${data.closed_fact_days} days`
                  : 'kept indefinitely'}
              </dd>
            </div>
          </dl>

          <p className="text-sm text-muted-foreground">
            <Badge tone="neutral" className="mr-1.5 text-[0.56rem]">
              {sourceLabel(data.source)}
            </Badge>
            {clause === null
              ? 'Nothing has aged past the horizon yet.'
              : `${clause} are past the horizon and will go on the next daily pass.`}{' '}
            Facts the agent currently believes are never removed by age, and the
            write log is kept whatever happens — it is the record of who changed what.
          </p>

          {canSweep && (
            <div>
              <Button size="sm" variant="outline" disabled={busy} onClick={() => void sweep()}>
                <Timer className="size-3.5" aria-hidden /> Apply the horizon now
              </Button>
            </div>
          )}
        </>
      )}

      <div className="border-t border-border pt-3">
        {confirmErase ? (
          <div className="flex flex-col gap-2">
            <p className="text-sm text-foreground">{erasureWarning(subjectRow)}</p>
            <div className="flex items-center gap-2">
              <Button size="sm" variant="destructive" disabled={busy} onClick={() => void erase()}>
                <Trash2 className="size-3.5" aria-hidden /> Erase everything
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
        <p role="alert" className="text-sm text-destructive">
          {failure}
        </p>
      )}
      {note !== null && (
        <p role="status" className="text-sm text-ok-ink">
          {note}
        </p>
      )}
    </div>
  )
}
