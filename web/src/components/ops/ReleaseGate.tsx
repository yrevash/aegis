'use client'

import { Check, Loader2, RotateCcw, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
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
    <li className="rounded-xl border border-border bg-card p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge tone={RISK_TONE[row.risk] ?? 'risk'}>
          <ShieldAlert className="size-3" /> {row.risk} risk
        </Badge>
        {row.prompt_key && <span className="font-mono text-[0.7rem] text-foreground">{row.prompt_key}</span>}
        {row.draft_version_id != null && (
          <span className="font-mono text-[0.62rem] text-muted-foreground">draft #{row.draft_version_id}</span>
        )}
        <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(row.created_at)}</span>
      </div>
      {row.reason && <p className="mt-2 text-xs leading-snug text-muted-foreground">{row.reason}</p>}

      {decided ? (
        <div
          className={cn(
            'mt-3 flex items-center gap-2 rounded-lg px-3 py-2 text-xs font-medium',
            decided.outcome === 'promoted' ? 'bg-ok/15 text-ok-ink' : 'bg-surface-2 text-muted-foreground',
          )}
        >
          {decided.outcome === 'promoted' ? <ShieldCheck className="size-3.5" /> : <X className="size-3.5" />}
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
            className="inline-flex items-center gap-1.5 rounded-lg bg-foreground px-3 py-1.5 text-[0.78rem] font-medium text-background transition-opacity hover:opacity-90 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            {busy === 'approve' ? <Loader2 className="size-3.5 animate-spin" /> : <Check className="size-3.5" />}
            {busy === 'approve' ? 'Approving…' : 'Approve'}
          </button>
          <button
            type="button"
            onClick={() => void decide(false)}
            disabled={busy !== null}
            className="inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
          >
            <X className="size-3.5" /> Reject
          </button>
          {error && <span className="text-xs text-danger">{error}</span>}
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
        description="Low-risk auto-ships; policy- or guardrail-touching edits stage here for explicit human sign-off."
        actions={!loading && !error ? <Badge tone="risk">{rows.length} awaiting</Badge> : null}
      />
      <CardBody className="space-y-4">
        {error ? (
          <p className="py-8 text-center text-sm text-danger">{error}</p>
        ) : loading ? (
          <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="size-4 animate-spin" /> Loading the release queue…
          </div>
        ) : rows.length === 0 ? (
          <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border/70 py-10 text-center text-sm text-muted-foreground">
            <ShieldCheck className="size-6 text-ok-ink" />
            <p>Nothing awaiting approval — the loop is caught up.</p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-[auto_1fr] sm:items-center">
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
        <div className="flex flex-wrap items-center gap-3 border-t border-border/60 pt-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-foreground">Emergency rollback</p>
            <p className="font-mono text-[0.62rem] text-muted-foreground">{PROMPT_KEY}</p>
          </div>
          {rollback ? (
            <span className="ml-auto flex items-center gap-1.5 text-xs font-medium text-ok-ink">
              <RotateCcw className="size-3.5" />
              Reverted to v{rollback.version ?? '—'}
            </span>
          ) : (
            <button
              type="button"
              onClick={() => void doRollback()}
              disabled={rollingBack}
              className="ml-auto inline-flex items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50"
            >
              <RotateCcw className="size-3.5" /> {rollingBack ? 'Rolling back…' : 'Roll back to last-good'}
            </button>
          )}
          {rollbackError && <p className="w-full text-xs text-danger">{rollbackError}</p>}
        </div>
      </CardBody>
    </Card>
  )
}
