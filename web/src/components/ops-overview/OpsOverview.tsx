'use client'

import { RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { PageHeader } from '@/components/primitives/PageHeader'
import { Button } from '@/components/primitives/button'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Card, CardBody } from '@/components/ui/Card'
import { checkPatches, getAudit, getLatency, getStack } from '@/lib/api/client'
import { getCacheStats, type CacheStatsResponse } from '@/lib/api/pipeline'
import type { LatencyResponse } from '@/lib/api/platform'
import type { AuditLogResponse, PatchCheckResponse, StackResponse } from '@/lib/api/types'
import { useAuth } from '@/lib/auth/AuthContext'

import { ComponentBoard, ReadinessVerdict } from './ComponentBoard'
import {
  AuditBlock,
  CacheBlock,
  LatencyBlock,
  LogsAbsence,
  PatchBlock,
  WorkerBlock,
  type Async,
} from './OpsBlocks'
import { ago, getLiveness, getReadyz, type LivenessResponse, type ReadyzResponse } from './readiness'

/** How often the readiness probe is re-read. */
const POLL_MS = 10_000

/** How often the "how long ago" captions re-compute. One timer for the whole screen. */
const TICK_MS = 1_000

/**
 * Resolve one fetch into the three states a panel draws, without losing the last
 * good answer to a transient failure of the *next* poll.
 *
 * A refresh that fails should not blank a board that is currently correct — an
 * operator staring at an outage does not need the console to also disappear. So a
 * failure only replaces a `ready` state when there was nothing there to keep.
 */
function settle<T>(previous: Async<T>, outcome: { ok: true; data: T } | { ok: false; error: unknown }): Async<T> {
  if (outcome.ok) return { state: 'ready', data: outcome.data }
  if (previous.state === 'ready') return previous
  return { state: 'error', error: outcome.error }
}

/** Run a promise and normalise both outcomes, so one rejection cannot fail a batch. */
async function attempt<T>(run: () => Promise<T>): Promise<{ ok: true; data: T } | { ok: false; error: unknown }> {
  try {
    return { ok: true, data: await run() }
  } catch (error) {
    return { ok: false, error }
  }
}

/**
 * The DevOps overview — the screen an operator keeps open.
 *
 * It answers two questions and nothing else. *Is the platform healthy right now* is
 * the server's own `/readyz` verdict, polled every ten seconds, with every component's
 * status, whether it is required, the evidence behind the verdict and how fresh the
 * reading is. *What needs me today* is the band below it: the job worker's state, the
 * run latency window, cache hit rates, patch posture, and what changed on the platform
 * lately — each one a summary that links through to the screen that owns the detail,
 * because a triage surface that grows its own copy of a deep dive stops being triage.
 *
 * **There is no log panel, and that is deliberate.** See {@link LogsAbsence}.
 */
export function OpsOverview({ token }: { token: string | null }): ReactElement {
  const { session } = useAuth()
  const portal = session?.fineRole ?? 'devops'

  const [readyz, setReadyz] = useState<Async<ReadyzResponse>>({ state: 'loading' })
  const [health, setHealth] = useState<Async<LivenessResponse>>({ state: 'loading' })
  const [latency, setLatency] = useState<Async<LatencyResponse>>({ state: 'loading' })
  const [caches, setCaches] = useState<Async<CacheStatsResponse>>({ state: 'loading' })
  const [audit, setAudit] = useState<Async<AuditLogResponse>>({ state: 'loading' })
  const [patches, setPatches] = useState<Async<PatchCheckResponse>>({ state: 'loading' })
  const [stack, setStack] = useState<Async<StackResponse>>({ state: 'loading' })

  /** When the readiness probe last answered — the screen's liveness caption. */
  const [readAt, setReadAt] = useState<number | null>(null)
  const [now, setNow] = useState(() => Date.now())
  /**
   * A manual refresh is a counter, not a boolean, and the boolean beside it is not an
   * effect dependency. A `refreshing` flag that both triggers the effect and is cleared
   * by it re-triggers the effect forever; the nonce changes once per press and the flag
   * only drives the button's label.
   */
  const [nonce, setNonce] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const refresh = useCallback((): void => {
    setRefreshing(true)
    setNonce((n) => n + 1)
  }, [])

  /**
   * The polled pair. Both are unauthenticated root probes and both are cheap, so
   * they ride the same interval; everything else is refreshed by hand or on mount.
   */
  const pollProbes = useCallback(async (signal: AbortSignal): Promise<void> => {
    const [ready, live] = await Promise.all([
      attempt(() => getReadyz(signal)),
      attempt(() => getLiveness(signal)),
    ])
    if (signal.aborted) return
    setReadyz((previous) => settle(previous, ready))
    setHealth((previous) => settle(previous, live))
    if (ready.ok || live.ok) setReadAt(Date.now())
  }, [])

  /** The authenticated panels. Kept off the poll: one is a live registry round trip. */
  const loadPanels = useCallback(
    async (signal: AbortSignal): Promise<void> => {
      const [lat, cache, trail, stackOut] = await Promise.all([
        attempt(() => getLatency(token)),
        attempt(() => getCacheStats(token)),
        attempt(() => getAudit(token, '?limit=12')),
        attempt(() => getStack(token)),
      ])
      if (signal.aborted) return
      setLatency((previous) => settle(previous, lat))
      setCaches((previous) => settle(previous, cache))
      setAudit((previous) => settle(previous, trail))
      setStack((previous) => settle(previous, stackOut))

      const patch = await attempt(() => checkPatches(undefined, token))
      if (signal.aborted) return
      setPatches((previous) => settle(previous, patch))
    },
    [token],
  )

  // ── The readiness poll. One interval, cleared on unmount, and every in-flight
  //    request aborted with it so a late answer cannot write to a dead screen. ──
  useEffect(() => {
    const controller = new AbortController()
    void pollProbes(controller.signal)
    const timer = setInterval(() => {
      void pollProbes(controller.signal)
    }, POLL_MS)
    return () => {
      clearInterval(timer)
      controller.abort()
    }
  }, [pollProbes, nonce])

  // ── The panels: on mount, and again whenever Refresh is pressed. ──
  useEffect(() => {
    const controller = new AbortController()
    void loadPanels(controller.signal).finally(() => {
      if (!controller.signal.aborted) setRefreshing(false)
    })
    return () => controller.abort()
  }, [loadPanels, nonce])

  // ── One clock for every "…ago" caption on the screen. ──
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), TICK_MS)
    return () => clearInterval(timer)
  }, [])

  const lastRead = readAt == null ? 'awaiting first read' : `read ${ago(new Date(readAt).toISOString(), now)}`

  return (
    <div className="flex min-w-0 flex-col gap-6">
      <PageHeader
        eyebrow="aegis · GET /readyz"
        title="Ops overview"
        actions={
          <div className="flex flex-wrap items-center gap-3">
            <p
              role="status"
              aria-live="polite"
              className="tabular flex items-center gap-2 font-mono text-xs text-muted-foreground"
            >
              <span
                aria-hidden
                className="animate-pip size-1.5 rounded-full bg-ok"
                style={{ ['--pip-color' as string]: 'var(--ok)' }}
              />
              {lastRead} · every {POLL_MS / 1000}s
            </p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={refresh}
              disabled={refreshing}
            >
              <RefreshCw className={refreshing ? 'animate-spin' : undefined} aria-hidden />
              {refreshing ? 'Refreshing…' : 'Refresh'}
            </Button>
          </div>
        }
      />

      {readyz.state === 'loading' ? (
        <Card>
          <CardBody>
            <LoadingState rows={3} label="Probing every dependency…" />
          </CardBody>
        </Card>
      ) : readyz.state === 'error' ? (
        <ErrorState
          error={readyz.error}
          fallback="The readiness probe did not answer, so this deployment's health is unread."
          retry={refresh}
        />
      ) : (
        <>
          <ReadinessVerdict data={readyz.data} />
          <div className="grid min-w-0 items-start gap-4 lg:grid-cols-3">
            <ComponentBoard data={readyz.data} now={now} portal={portal} className="lg:col-span-2" />
            <div className="flex min-w-0 flex-col gap-4">
              <WorkerBlock health={health} portal={portal} />
              <LatencyBlock latency={latency} portal={portal} />
            </div>
          </div>
        </>
      )}

      {/* The three standing questions that are not readiness: what the caches are
          doing, how far behind the dependencies are, and the one thing this screen
          deliberately does not have. */}
      <div className="grid min-w-0 items-start gap-4 lg:grid-cols-3">
        <CacheBlock caches={caches} portal={portal} />
        <PatchBlock patches={patches} stack={stack} now={now} portal={portal} />
        <LogsAbsence />
      </div>

      <AuditBlock audit={audit} now={now} portal={portal} />
    </div>
  )
}
