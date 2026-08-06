import { Check, RotateCcw, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { useMemo, useState, type ReactElement } from 'react'

import { postOpsReleaseDecision, postOpsRollback } from '@/api/client'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { ErrorRow, LoadingRow } from '@/components/common/StateRow'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatAgo } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { AsyncState } from '@/components/admin/useAsync'
import type { ChartColor } from '@/components/charts/palette'
import type { OpsPendingReleasesResponse, OpsReleaseApprovalRow } from '@/types/ops'

import { PanelHead } from './PanelHead'

type RiskVariant = 'ok' | 'risk' | 'block'
const RISK_VARIANT: Record<string, RiskVariant> = { low: 'ok', medium: 'risk', high: 'block' }
const RISK_COLOR: Record<string, ChartColor> = { low: 'ok', medium: 'risk', high: 'block' }
const RISK_ORDER = ['high', 'medium', 'low']

/** Per-row decision outcome, held in the session after the human acts. */
type Decided = { outcome: string; activeVersion: number | null }

function ReleaseRow({
  row,
  token,
  onChanged,
}: {
  row: OpsReleaseApprovalRow
  token: string | null
  onChanged: () => void
}): ReactElement {
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

  const variant = RISK_VARIANT[row.risk] ?? 'risk'

  return (
    <li className="rounded-lg border border-border bg-card p-3.5">
      <div className="flex flex-wrap items-center gap-2">
        <Badge variant={variant}>
          <ShieldAlert /> {row.risk} risk
        </Badge>
        {row.prompt_key && <span className="font-mono text-[0.7rem] text-foreground">{row.prompt_key}</span>}
        {row.draft_version_id != null && (
          <span className="font-mono text-[0.62rem] text-muted-foreground">draft #{row.draft_version_id}</span>
        )}
        <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(row.created_at)}</span>
      </div>
      {/* The plain-language reason this change is waiting for a human. */}
      {row.reason && <p className="mt-2 text-xs leading-snug text-muted-foreground">{row.reason}</p>}

      {decided ? (
        <div
          className={cn(
            'mt-3 flex items-center gap-2 rounded-md px-3 py-2 text-xs font-medium',
            decided.outcome === 'promoted' ? 'bg-ok/10 text-ok-ink' : 'bg-surface-2 text-muted-foreground',
          )}
        >
          {decided.outcome === 'promoted' ? <ShieldCheck className="size-3.5" /> : <X className="size-3.5" />}
          {decided.outcome === 'promoted'
            ? `Shipped — now live${decided.activeVersion != null ? ` (v${decided.activeVersion})` : ''}.`
            : 'Rejected — draft archived, live version unchanged.'}
        </div>
      ) : (
        <div className="mt-3 flex items-center gap-2">
          <Button size="sm" onClick={() => void decide(true)} disabled={busy !== null}>
            <Check /> {busy === 'approve' ? 'Approving…' : 'Approve'}
          </Button>
          <Button size="sm" variant="outline" onClick={() => void decide(false)} disabled={busy !== null}>
            <X /> Reject
          </Button>
          {error && <span className="text-xs text-destructive">{error}</span>}
        </div>
      )}
    </li>
  )
}

interface Props {
  state: AsyncState<OpsPendingReleasesResponse>
  token: string | null
  promptKey: string
  onChanged: () => void
}

/**
 * The **Release gate** (§4.4) — prompt changes awaiting a human. Low-risk
 * improvements auto-ship upstream and never land here; what remains are the
 * higher-risk edits that need sign-off. A donut shows the risk mix of the queue;
 * each row can be approved (ship) or rejected (archive), with a one-click
 * rollback to the last-good version. Card-less: a `BentoTile` owns the Card.
 */
export function PendingReleases({ state, token, promptKey, onChanged }: Props): ReactElement {
  const [rollback, setRollback] = useState<null | { reverted: boolean; version: number | null }>(null)
  const [rollingBack, setRollingBack] = useState(false)
  const rows = useMemo(() => (state.status === 'ready' ? state.data.rows : []), [state])

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
    try {
      const res = await postOpsRollback(token, promptKey)
      setRollback({ reverted: res.reverted, version: res.active_version })
      onChanged()
    } finally {
      setRollingBack(false)
    }
  }

  return (
    <>
      <PanelHead
        icon={ShieldAlert}
        tint="bg-risk/15"
        ink="text-risk-ink"
        title="Release gate"
        subtitle="Low-risk auto-ships · high-risk waits"
        info="Changes that clear the quality margin and touch nothing sensitive ship automatically. Policy- or guardrail-touching edits stage here for explicit human sign-off."
        right={
          state.status === 'ready' ? (
            <Badge variant="risk">{rows.length} awaiting</Badge>
          ) : undefined
        }
      />

      <div className="space-y-4">
        {state.status === 'loading' && <LoadingRow label="Loading the release queue…" />}
        {state.status === 'error' && <ErrorRow message={state.message} />}

        {state.status === 'ready' && rows.length === 0 && (
          <div className="flex flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-border/70 py-10 text-center text-sm text-muted-foreground">
            <ShieldCheck className="size-6 text-ok" />
            <p>Nothing awaiting approval — the loop is caught up.</p>
          </div>
        )}

        {state.status === 'ready' && rows.length > 0 && (
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
                <ReleaseRow key={r.approval_id} row={r} token={token} onChanged={onChanged} />
              ))}
            </ul>
          </div>
        )}

        {/* One-click rollback. */}
        <div className="flex flex-wrap items-center gap-3 border-t border-border/60 pt-3">
          <div className="min-w-0">
            <p className="text-xs font-semibold text-foreground">Emergency rollback</p>
            <p className="font-mono text-[0.62rem] text-muted-foreground">{promptKey}</p>
          </div>
          {rollback ? (
            <span className="ml-auto flex items-center gap-1.5 text-xs font-medium text-ok-ink">
              <RotateCcw className="size-3.5" />
              Reverted to v{rollback.version ?? '—'}
            </span>
          ) : (
            <Button
              size="sm"
              variant="outline"
              className="ml-auto"
              onClick={() => void doRollback()}
              disabled={rollingBack}
            >
              <RotateCcw /> {rollingBack ? 'Rolling back…' : 'Roll back to last-good'}
            </Button>
          )}
        </div>
      </div>
    </>
  )
}
