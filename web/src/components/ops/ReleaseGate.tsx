'use client'

import { Check, Loader2, RotateCcw, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { postOpsReleaseDecision, postOpsRollback } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { ChartColor } from '@/components/charts/palette'
import type { OpsReleaseApprovalRow } from '@/lib/api/ops'

import { PROMPT_KEY, RISK_TONE, formatAgo } from './opsShared'

const RISK_COLOR: Record<string, ChartColor> = { low: 'ok', medium: 'risk', high: 'block' }
const RISK_ORDER = ['high', 'medium', 'low']

/** Per-row decision outcome, held in the session after the human acts. */
type Decided = { outcome: string; activeVersion: number | null }

function ReleaseRow({
  row,
  onChanged,
}: {
  row: OpsReleaseApprovalRow
  onChanged: () => void
}): ReactElement {
  // The decision is an RBAC-scoped write — send the real session bearer.
  const { session } = useAuth()
  const token = session?.token ?? null
  const [busy, setBusy] = useState<null | 'approve' | 'reject'>(null)
  const [decided, setDecided] = useState<Decided | null>(null)
  const [error, setError] = useState<string | null>(null)

  const decide = async (approved: boolean): Promise<void> => {
    setBusy(approved ? 'approve' : 'reject')
    setError(null)
    try {
      const res = await postOpsReleaseDecision(token, row.approval_id, approved)
      setDecided({ outcome: res.outcome, activeVersion: res.active_version })
      onChanged()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Decision failed')
    } finally {
      setBusy(null)
    }
  }

  return (
    <li className="min-w-0 rounded-lg border border-border bg-card p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={RISK_TONE[row.risk] ?? 'risk'}>
          <ShieldAlert className="size-3" aria-hidden /> {row.risk} risk
        </Badge>
        {row.prompt_key && (
          <Figure className="min-w-0 break-words text-foreground">{row.prompt_key}</Figure>
        )}
        {row.draft_version_id != null && (
          <Figure className="text-muted-foreground">draft #{row.draft_version_id}</Figure>
        )}
        {row.reason && (
          <InfoTip label={`Why this ${row.risk}-risk draft needs sign-off`}>{row.reason}</InfoTip>
        )}
        <span className="eyebrow ml-auto">{formatAgo(row.created_at)}</span>
      </div>

      {decided ? (
        <div
          role="status"
          aria-live="polite"
          className={cn(
            'mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium',
            decided.outcome === 'promoted' ? 'bg-ok/15 text-ok-ink' : 'bg-surface-2 text-muted-foreground',
          )}
        >
          {decided.outcome === 'promoted' ? (
            <ShieldCheck className="size-3.5" aria-hidden />
          ) : (
            <X className="size-3.5" aria-hidden />
          )}
          {decided.outcome === 'promoted'
            ? `Shipped — now live${decided.activeVersion != null ? ` (v${decided.activeVersion})` : ''}.`
            : 'Rejected — draft archived, live version unchanged.'}
        </div>
      ) : (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => void decide(true)}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg bg-foreground h-10 touch-manipulation px-3 text-[0.78rem] font-medium text-background transition-opacity hover:opacity-90 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
          >
            {busy === 'approve' ? (
              <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
            ) : (
              <Check className="size-3.5" aria-hidden />
            )}
            {busy === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            onClick={() => void decide(false)}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border h-10 touch-manipulation px-3 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
          >
            <X className="size-3.5" aria-hidden /> Reject
          </button>
          {error && (
            <span role="alert" className="min-w-0 text-xs break-words text-danger">
              {error}
            </span>
          )}
        </div>
      )}
    </li>
  )
}

interface Props {
  rows: OpsReleaseApprovalRow[]
  loading: boolean
  error: string | null
  onChanged: () => void
}

/**
 * The **Release gate** — prompt changes awaiting a human. Low-risk improvements
 * that clear the quality margin and touch nothing sensitive auto-ship and never
 * land here; what remains are the higher-risk edits that need sign-off. A donut
 * shows the risk mix; each row approves (ship) or rejects (archive), with a
 * one-click rollback to the last-good version.
 */
export function ReleaseGate({ rows, loading, error, onChanged }: Props): ReactElement {
  // The rollback is an RBAC-scoped write — send the real session bearer.
  const { session } = useAuth()
  const token = session?.token ?? null
  const [rollback, setRollback] = useState<null | { reverted: boolean; version: number | null }>(null)
  const [rollingBack, setRollingBack] = useState(false)
  const [rollbackError, setRollbackError] = useState<string | null>(null)

  const riskMix = useMemo<DonutDatum[]>(() => {
    const counts = new Map<string, number>()
    for (const r of rows) counts.set(r.risk, (counts.get(r.risk) ?? 0) + 1)
    return RISK_ORDER.filter((k) => counts.has(k)).map((k) => ({
      name: `${k} risk`,
      value: counts.get(k) ?? 0,
      color: RISK_COLOR[k] ?? 'risk',
    }))
  }, [rows])

  const doRollback = async (): Promise<void> => {
    setRollingBack(true)
    setRollbackError(null)
    try {
      const res = await postOpsRollback(token, PROMPT_KEY)
      setRollback({ reverted: res.reverted, version: res.active_version })
      onChanged()
    } catch (e) {
      // Surface the failure instead of letting it reject unhandled and vanish.
      setRollbackError(e instanceof Error ? e.message : 'Rollback failed')
    } finally {
      setRollingBack(false)
    }
  }

  return (
    <Card>
      <CardHeader
        eyebrow="GET /ops/releases/pending"
        title="Release gate"
        actions={
          <div className="flex items-center gap-2">
            {!loading && !error ? <Badge tone="risk">{rows.length} awaiting</Badge> : null}
            <InfoTip label="About the release gate">
              Low-risk edits that clear the eval margin auto-ship; what lands here is the
              policy- or guardrail-touching remainder.
            </InfoTip>
          </div>
        }
      />
      <CardBody className="@container min-w-0 space-y-4">
        {error ? (
          <ErrorState error={error} />
        ) : loading ? (
          <LoadingState rows={3} label="Loading the release queue…" />
        ) : rows.length === 0 ? (
          <EmptyState
            icon={ShieldCheck}
            title="Nothing awaiting approval"
            body="The loop is caught up — every draft either auto-shipped or was decided."
          />
        ) : (
          <div className="grid min-w-0 gap-4 @md:grid-cols-[auto_minmax(0,1fr)] @md:items-center">
            <div className="mx-auto w-[150px]">
              <DonutChart
                data={riskMix}
                height={150}
                centerLabel={String(rows.length)}
                centerSub="awaiting"
                valueFormatter={(v) => `${v}`}
              />
            </div>
            <ul className="space-y-2.5">
              {rows.map((r) => (
                <ReleaseRow key={r.approval_id} row={r} onChanged={onChanged} />
              ))}
            </ul>
          </div>
        )}

        {/* One-click rollback. */}
        <div className="flex min-w-0 flex-wrap items-center gap-3 border-t border-border/60 pt-3">
          <Figure className="min-w-0 break-words text-muted-foreground">{PROMPT_KEY}</Figure>
          {rollback ? (
            <span className="ml-auto flex items-center gap-1.5 text-xs font-medium text-ok-ink">
              <RotateCcw className="size-3.5" aria-hidden />
              Reverted to {rollback.version == null ? 'the last good version' : `v${rollback.version}`}
            </span>
          ) : (
            <button
              type="button"
              onClick={() => void doRollback()}
              disabled={rollingBack}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border h-10 touch-manipulation px-3 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:opacity-50"
            >
                {rollingBack ? (
                <Loader2 className="size-3.5 animate-spin motion-reduce:animate-none" aria-hidden />
              ) : (
                <RotateCcw className="size-3.5" aria-hidden />
              )}
              {rollingBack ? 'Rolling back…' : 'Roll back to last-good'}
            </button>
          )}
          {rollbackError && (
            <p role="alert" className="w-full text-xs break-words text-danger">
              {rollbackError}
            </p>
          )}
        </div>
      </CardBody>
    </Card>
  )
}
