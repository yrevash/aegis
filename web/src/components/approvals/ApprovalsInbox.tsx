'use client'

import { CheckCircle2, Loader2, WifiOff } from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { getApprovals, postApprovalDecision } from '@/lib/api/client'
import { DonutChart, type DonutDatum } from '@/components/charts/DonutChart'
import { BentoGrid, BentoTile, CountUp } from '@/components/shared'
import { InfoTip } from '@/components/primitives/InfoTip'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import type { ApprovalDecision, ApprovalRow } from '@/lib/api/types'

import { ApprovalQueueCard } from './ApprovalQueueCard'
import { applyDecision, pendingRows } from './inbox'
import { slaCountdown } from './sla'

/** Load state for the approvals fetch. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: ApprovalRow[] }

interface ApprovalsInboxProps {
  token: string | null
}

const RISK_ORDER: Array<{ key: 'high' | 'medium' | 'low'; color: DonutDatum['color'] }> = [
  { key: 'high', color: 'block' },
  { key: 'medium', color: 'risk' },
  { key: 'low', color: 'ok' },
]

/** Format a positive millisecond span as a compact "Xh Ym" / "Ym" duration. */
function formatWait(ms: number): string {
  if (ms <= 0) return '0m'
  const totalMin = Math.round(ms / 60_000)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
  if (h > 0) return `${h}h ${m}m`
  return `${m}m`
}

/** One count-up stat in the queue-summary hero. */
function SummaryStat({
  label,
  value,
  format,
  info,
  tone,
}: {
  label: string
  value: number
  format?: (n: number) => string
  info?: string
  tone?: 'default' | 'warn'
}): ReactElement {
  return (
    <div>
      <div className="flex items-center gap-1.5">
        <span className="eyebrow">{label}</span>
        {info && <InfoTip label={`About ${label}`}>{info}</InfoTip>}
      </div>
      <CountUp
        value={value}
        format={format}
        className={tone === 'warn' && value > 0 ? 't-metric mt-1 block text-block-ink' : 't-metric mt-1 block text-foreground'}
      />
    </div>
  )
}

/**
 * The **Approvals** surface (Aegis Tools/MCP) — a calm triage board for the
 * durable human gate. A summary hero reads the queue state (pending, overdue,
 * average wait, risk mix) at a glance; below it, a responsive card grid shows
 * each request with its action, risk, SLA and confidence, and a clear approve /
 * reject. The ML internals live behind a per-card disclosure.
 */
export function ApprovalsInbox({ token }: ApprovalsInboxProps): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [busyId, setBusyId] = useState<number | null>(null)
  const [leaving, setLeaving] = useState<Set<number>>(new Set())
  // A ticking clock so the SLA countdowns update live.
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(t)
  }, [])

  const refresh = useCallback(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getApprovals(token)
      .then((res) => {
        if (alive) setLoad({ status: 'ready', rows: res.rows })
      })
      .catch((error: unknown) => {
        if (alive) {
          setLoad({
            status: 'error',
            message: error instanceof Error ? error.message : 'Failed to load approvals',
          })
        }
      })
    return () => {
      alive = false
    }
  }, [token])

  useEffect(() => refresh(), [refresh])

  const decide = async (row: ApprovalRow, decision: ApprovalDecision): Promise<void> => {
    setBusyId(row.id)
    try {
      const res = await postApprovalDecision(row.id, decision, token)
      if (res.accepted && load.status === 'ready') {
        // Fade the card out, then remove it from the queue.
        setLeaving((prev) => new Set(prev).add(row.id))
        const rows = load.rows
        setTimeout(() => {
          setLoad({ status: 'ready', rows: applyDecision(rows, row.id, decision) })
          setLeaving((prev) => {
            const next = new Set(prev)
            next.delete(row.id)
            return next
          })
        }, 300)
      }
    } catch {
      /* transient; leave the row in place so the reviewer can retry */
    } finally {
      setBusyId(null)
    }
  }

  const pending = useMemo(
    () => (load.status === 'ready' ? pendingRows(load.rows) : []),
    [load],
  )

  const summary = useMemo(() => {
    const overdue = pending.filter((r) => slaCountdown(r.sla_deadline, now).urgency === 'overdue').length
    const waits = pending.map((r) => now - new Date(r.created_at).getTime()).filter((v) => Number.isFinite(v) && v > 0)
    const avgWaitMs = waits.length > 0 ? waits.reduce((s, v) => s + v, 0) / waits.length : 0
    const riskCounts = new Map<string, number>()
    for (const r of pending) riskCounts.set(r.risk, (riskCounts.get(r.risk) ?? 0) + 1)
    const riskMix: DonutDatum[] = RISK_ORDER.filter((r) => riskCounts.has(r.key)).map((r) => ({
      name: `${r.key} risk`,
      value: riskCounts.get(r.key) ?? 0,
      color: r.color,
    }))
    return { overdue, avgWaitMs, riskMix }
  }, [pending, now])

  return (
    <div className="space-y-6">
      {/* Queue-summary hero — read the queue state before scrolling. */}
      <BentoGrid className="gap-4">
        <BentoTile span={8}>
          <div className="grid grid-cols-3 gap-4">
            <SummaryStat
              label="Pending"
              value={pending.length}
              info="Deferred actions waiting on a human decision."
            />
            <SummaryStat label="Overdue" value={summary.overdue} tone="warn" info="Past their SLA deadline." />
            <SummaryStat
              label="Avg wait"
              value={summary.avgWaitMs}
              format={formatWait}
              info="Average time these requests have been waiting."
            />
          </div>
        </BentoTile>
        <BentoTile span={4}>
          <div className="flex items-center gap-1.5">
            <span className="eyebrow">Risk mix</span>
            <InfoTip label="About the risk mix">
              How the pending queue breaks down by action risk tier — high, medium, low.
            </InfoTip>
          </div>
          {summary.riskMix.length > 0 ? (
            <DonutChart
              data={summary.riskMix}
              height={140}
              centerLabel={String(pending.length)}
              centerSub="pending"
              valueFormatter={(v) => `${v}`}
            />
          ) : (
            <div className="flex h-[140px] items-center justify-center text-sm text-muted-foreground">
              No risk to show
            </div>
          )}
        </BentoTile>
      </BentoGrid>

      {/* The triage grid. */}
      {load.status === 'loading' && (
        <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading pending approvals…
        </div>
      )}

      {load.status === 'error' && (
        <div className="py-10 text-sm text-block-ink">Could not load the approvals queue. {load.message}</div>
      )}

      {load.status === 'ready' && pending.length === 0 && (
        <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-border/70 py-16 text-center">
          <CheckCircle2 className="size-8 text-ok" />
          <p className="t-title text-foreground">You&apos;re all caught up</p>
          <p className="text-sm text-muted-foreground">Deferred actions will queue here with an SLA.</p>
        </div>
      )}

      {load.status === 'ready' && pending.length > 0 && (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {pending.map((row) => (
            <ApprovalQueueCard
              key={row.id}
              row={row}
              now={now}
              busy={busyId === row.id}
              leaving={leaving.has(row.id)}
              onDecide={decide}
            />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * Client entry for the Approvals section. Runs the boot probe once (live-first,
 * mock fallback) before mounting the inbox, so the fetch reads the resolved mode
 * — the offline demo seeds from the mock fixture and carries the honest banner.
 */
export function ApprovalsMount(): ReactElement {
  // The approvals queue and its decisions are RBAC-scoped: hand the child the
  // real session bearer, and hold it back until the session has been restored.
  const { session, hydrated } = useAuth()
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null || !hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="space-y-6">
        <div>
          <p className="eyebrow mb-1">human gate</p>
          <h1 className="t-hero text-foreground">Approvals</h1>
        </div>
        {mode.mode === 'mock' && (
          <div
            role="status"
            className="flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
          >
            <WifiOff className="size-3.5 shrink-0" />
            <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
          </div>
        )}
        <ApprovalsInbox token={session?.token ?? null} />
      </div>
    </TooltipProvider>
  )
}
