'use client'

import {
  CircleCheck,
  Cpu,
  DatabaseZap,
  Package,
  ScrollText,
  ShieldX,
  Timer,
  Workflow,
  type LucideIcon,
} from 'lucide-react'
import type { ReactElement, ReactNode } from 'react'

import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { SIGNALS } from '@/config/signals'
import type { AuditLogResponse, PatchCheckResponse, StackResponse } from '@/lib/api/types'
import type { CacheStatsResponse } from '@/lib/api/pipeline'
import type { LatencyResponse } from '@/lib/api/platform'
import { cn } from '@/lib/utils'

import { AbsenceMark } from './AbsenceMark'
import { DeepLink } from './ComponentBoard'
import { ago, fmtClock, fmtInt, fmtMs, fmtPercent, splitOrigin, WORKER_STATE, type LivenessResponse } from './readiness'

/** A panel's fetch, in the three states a panel has to draw. */
export type Async<T> =
  | { state: 'loading' }
  | { state: 'error'; error: unknown }
  | { state: 'ready'; data: T }

/**
 * One triage panel: a title, the link to the screen that owns the full answer, and
 * whichever of the three states its fetch is in.
 *
 * The deep link is a required prop rather than an optional one. This screen is a
 * triage surface — every block on it summarises a screen that already exists, and a
 * summary with no way through to the detail is the beginning of a second copy of that
 * screen.
 */
function Block({
  title,
  eyebrow,
  href,
  linkLabel,
  linkIcon,
  children,
  className,
}: {
  title: string
  eyebrow?: string
  href: string
  linkLabel: string
  linkIcon: LucideIcon
  children: ReactNode
  className?: string
}): ReactElement {
  return (
    <Card className={cn('flex min-w-0 flex-col', className)}>
      <CardHeader
        as="h3"
        eyebrow={eyebrow}
        title={title}
        actions={<DeepLink href={href} icon={linkIcon} label={linkLabel} />}
      />
      <CardBody className="flex min-w-0 flex-1 flex-col gap-3 pt-0">{children}</CardBody>
    </Card>
  )
}

/** A thin bounded bar — a ratio that has a real denominator, drawn once. */
function Meter({ ratio, label }: { ratio: number; label: string }): ReactElement {
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
      role="img"
      aria-label={label}
    >
      <div className="h-full rounded-full bg-blue-600" style={{ width: `${Math.min(100, Math.max(0, ratio * 100))}%` }} />
    </div>
  )
}

// ── Worker ───────────────────────────────────────────────────────────────────

/**
 * The durable job worker, spelled out rather than reduced to a dot.
 *
 * Five states reach this tile and two of them are routinely misread. `disabled` is a
 * deployment that never intended to run a worker — ready, not broken — so it wears the
 * neutral tone and says so. `starting` is not yet taking work but is not a fault
 * either. Only `down` and `stopped` mean queued jobs are going nowhere.
 */
export function WorkerBlock({ health, portal }: { health: Async<LivenessResponse>; portal: string }): ReactElement {
  return (
    <Block
      title="Job worker"
      eyebrow="aegis · GET /health"
      // The worker's depth — queue depth, stage timings, recent failures — is the
      // pipeline-health panel, which this deployment renders inside the stack screen.
      href={`/app/${portal}/stack`}
      linkLabel="Pipeline health"
      linkIcon={Workflow}
    >
      {health.state === 'loading' ? (
        <LoadingState rows={2} label="Reading liveness…" />
      ) : health.state === 'error' ? (
        <ErrorState error={health.error} fallback="The liveness probe did not answer, so the worker's state is unread." />
      ) : (
        <WorkerBody health={health.data} />
      )}
    </Block>
  )
}

/**
 * The worker's state as a medallion, not a paragraph.
 *
 * This card was a badge with a sentence under it — *"The supervisor holds a live
 * worker, so queued jobs are being taken."* — which is the shape DESIGN.md §4 sends
 * to an `InfoTip`, and that is exactly where it is now. What is left on the face is
 * a glyph in the state's own hue, the state's word at title size, and the receipt.
 *
 * The hue is never alone: the word sits beside it, because `ok`, `risk` and `block`
 * fail CVD separation against each other by design.
 */
function WorkerBody({ health }: { health: LivenessResponse }): ReactElement {
  const known = WORKER_STATE[health.worker]
  const signal = SIGNALS[known?.tone ?? 'neutral']
  return (
    <>
      <div className="flex min-w-0 items-center gap-3">
        <span className={cn('grid size-12 shrink-0 place-items-center rounded-full', signal.bg)} aria-hidden>
          <Cpu className={cn('size-6', signal.text)} />
        </span>
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="min-w-0 truncate text-base leading-6 font-semibold text-foreground">
            {known?.word ?? health.worker}
          </span>
          <InfoTip label="What this worker state means">
            {known?.line ??
              `The supervisor reported "${health.worker}", a state this console has no wording for.`}
          </InfoTip>
        </div>
      </div>
      {/*
        The origin is the identifier; `status ok` is a second fact about the same
        probe and it wrapped the receipt onto a third line under a tile whose
        subject is one word. It joins the detail, which `Receipt` already puts in
        its own tooltip — reachable, not printed (DESIGN.md §4).
      */}
      <Receipt
        className="mt-auto"
        origin={`${health.product} ${health.version}`}
        detail={`status ${health.status} · the API process's own supervisor state, not a probe of a remote worker`}
      />
    </>
  )
}

// ── Latency ──────────────────────────────────────────────────────────────────

/** Run percentiles and how full the rolling window that produced them is. */
export function LatencyBlock({ latency, portal }: { latency: Async<LatencyResponse>; portal: string }): ReactElement {
  return (
    <Block title="Run latency" eyebrow="aegis · GET /latency" href={`/app/${portal}/latency`} linkLabel="Per-node latency" linkIcon={Timer}>
      {latency.state === 'loading' ? (
        <LoadingState rows={2} label="Reading latency…" />
      ) : latency.state === 'error' ? (
        <ErrorState error={latency.error} fallback="The latency window could not be read." />
      ) : (
        <LatencyBody data={latency.data} />
      )}
    </Block>
  )
}

/**
 * The latency window: three bars when there are readings, one marked absence when
 * there are not — and in both cases the *window itself*, drawn.
 *
 * The empty case used to be the whole card: a four-line block explaining that no
 * run had been recorded and what would fill the window. It was the longest text on
 * the overview, on a tile whose subject is three numbers. It is an
 * {@link AbsenceMark} now — one line, the reason and the remediation one hover or
 * one tab away.
 *
 * The fill meter is drawn in the empty case too, which it was not before. `0 of 512
 * slots` is a **measured** zero, not a fabricated one: the buffer really is empty
 * and its capacity really is 512, so the bar is the honest picture of exactly the
 * fact the sentence was spending four lines on.
 */
function LatencyBody({ data }: { data: LatencyResponse }): ReactElement {
  const empty = data.empty || data.run_p50_ms == null
  const capacity = data.window_capacity
  const percentiles = [
    ['p50', data.run_p50_ms],
    ['p95', data.run_p95_ms],
    ['max', data.run_max_ms],
  ] as const
  // The shared scale is the largest reading present, so the three bars are read by
  // aligned length against each other — the bullet form DESIGN.md §2 asks for when
  // several bounded values sit side by side. A missing reading gets no bar at all
  // rather than a zero-length one, which would draw "fast" where we mean "unknown".
  const scale = percentiles.reduce((m, [, v]) => (v == null ? m : Math.max(m, v)), 0)
  return (
    <>
      {empty ? (
        <AbsenceMark
          figure="Run percentiles"
          why="No run has been recorded in this process's window yet, so there is nothing to take a percentile of."
          needed="one completed run — the window fills from GET /latency"
        />
      ) : (
        <dl className="min-w-0 space-y-2">
          {percentiles.map(([label, value]) => (
            <div key={label} className="flex min-w-0 items-center gap-3">
              <dt className="eyebrow w-7 shrink-0">{label}</dt>
              <dd className="flex min-w-0 flex-1 items-center gap-3">
                {value == null || scale <= 0 ? (
                  <span className="min-w-0 flex-1 text-xs text-muted-foreground italic">no reading</span>
                ) : (
                  <>
                    <span className="h-2 min-w-0 flex-1 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                      <span
                        className="block h-full rounded-full bg-blue-600"
                        style={{ width: `${Math.max(2, (value / scale) * 100)}%` }}
                      />
                    </span>
                    <Figure className="shrink-0 text-foreground">{fmtMs(value)}</Figure>
                  </>
                )}
              </dd>
            </div>
          ))}
        </dl>
      )}
      {capacity == null || capacity <= 0 ? (
        <AbsenceMark
          figure="Window fill"
          why="This process reported no window capacity, so the run count has no denominator."
          needed="a window_capacity on GET /latency"
        />
      ) : (
        <div className="min-w-0 space-y-1.5">
          <Meter ratio={data.run_count / capacity} label={`${data.run_count} of ${capacity} window slots filled`} />
          <p className="tabular font-mono text-xs text-muted-foreground">
            {fmtInt(data.run_count)} of {fmtInt(capacity)} slots
            {data.slowest_node ? ` · slowest ${data.slowest_node}` : ''}
          </p>
        </div>
      )}
      <Receipt className="mt-auto" origin={data.source} detail="a per-process rolling buffer with no timestamps" />
    </>
  )
}

// ── Caches ───────────────────────────────────────────────────────────────────

/**
 * Hit rate per cache — or the honest absence.
 *
 * `hit_rate` is `null`, never `0`, before the first lookup, and the two are different
 * facts: a cache nothing has asked for has not failed to answer. A cache that was
 * never constructed in this process is a third fact again, and says that instead.
 */
export function CacheBlock({ caches, portal }: { caches: Async<CacheStatsResponse>; portal: string }): ReactElement {
  return (
    <Block title="Cache hit rate" eyebrow="aegis · GET /platform/caches" href={`/app/${portal}/cache`} linkLabel="Cache detail" linkIcon={DatabaseZap}>
      {caches.state === 'loading' ? (
        <LoadingState rows={3} label="Reading cache counters…" />
      ) : caches.state === 'error' ? (
        <ErrorState error={caches.error} fallback="The cache counters could not be read." />
      ) : (
        <CacheBody data={caches.data} />
      )}
    </Block>
  )
}

function CacheBody({ data }: { data: CacheStatsResponse }): ReactElement {
  // `source` arrives as `identifier — what the counters are`. The identifier is the
  // receipt; the clause after it explains a mechanism, which is a tooltip (§4). Both
  // stay on the page — see {@link splitOrigin}.
  const { origin, note } = splitOrigin(data.source)
  const detail = [note, data.caveat].filter((part) => part != null && part !== '').join(' · ')
  return (
    <>
      <ul className="min-w-0 space-y-3">
        {data.caches.map((cache) => (
          <li key={cache.key} className="min-w-0">
            <div className="flex min-w-0 items-baseline justify-between gap-3">
              <span className="min-w-0 truncate text-sm text-foreground">{cache.name}</span>
              {cache.hit_rate == null ? (
                <span className="shrink-0 text-xs text-muted-foreground italic">
                  {cache.registered ? 'no lookups yet' : 'not built here'}
                </span>
              ) : (
                <Figure className="shrink-0 text-foreground">{fmtPercent(cache.hit_rate)}</Figure>
              )}
            </div>
            {cache.hit_rate == null ? null : (
              <div className="mt-1.5">
                <Meter ratio={cache.hit_rate} label={`${cache.name} hit rate`} />
              </div>
            )}
          </li>
        ))}
      </ul>
      <Receipt className="mt-auto" origin={origin} detail={detail === '' ? null : detail} />
    </>
  )
}

// ── Patch posture ────────────────────────────────────────────────────────────

/** How much of the running stack has a newer release, and how much could not be told. */
export function PatchBlock({
  patches,
  stack,
  now,
  portal,
}: {
  patches: Async<PatchCheckResponse>
  stack: Async<StackResponse>
  now: number
  portal: string
}): ReactElement {
  return (
    <Block title="Patch posture" eyebrow="aegis · POST /stack/patch-check" href={`/app/${portal}/patch`} linkLabel="Patch check" linkIcon={Package}>
      {patches.state === 'loading' ? (
        <LoadingState rows={2} label="Checking the registry…" />
      ) : patches.state === 'error' ? (
        <ErrorState error={patches.error} fallback="The patch check did not complete." />
      ) : (
        <PatchBody data={patches.data} stack={stack} now={now} portal={portal} />
      )}
    </Block>
  )
}

function PatchBody({
  data,
  stack,
  now,
  portal,
}: {
  data: PatchCheckResponse
  stack: Async<StackResponse>
  now: number
  portal: string
}): ReactElement {
  const results = data.results ?? []
  const outdated = results.filter((r) => r.status === 'outdated').length
  const unknown = results.filter((r) => r.status === 'unknown').length
  const current = results.filter((r) => r.status === 'current').length
  return (
    <>
      <dl className="grid min-w-0 grid-cols-3 gap-3">
        {(
          [
            ['outdated', outdated, outdated > 0 ? 'text-risk-ink' : 'text-foreground'],
            ['current', current, 'text-foreground'],
            ['unknown', unknown, 'text-foreground'],
          ] as const
        ).map(([label, value, tone]) => (
          <div key={label} className="min-w-0">
            <dt className="eyebrow">{label}</dt>
            <dd className="mt-1">
              <Figure size="stat" className={tone}>
                {fmtInt(value)}
              </Figure>
            </dd>
          </div>
        ))}
      </dl>
      <p className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        {data.online ? (
          <>
            <CircleCheck className="size-3.5 shrink-0 text-ok-ink" aria-hidden />
            Registry answered
          </>
        ) : (
          <>
            <ShieldX className="size-3.5 shrink-0 text-risk-ink" aria-hidden />
            Registry unreachable — verdicts are best-effort
          </>
        )}
        <InfoTip label="How to read the patch verdicts">{data.note}</InfoTip>
        {stack.state === 'ready' ? (
          <DeepLink href={`/app/${portal}/stack`} icon={Package} label={`${(stack.data.components ?? []).length} in the stack`} />
        ) : null}
      </p>
      <Receipt className="mt-auto" label="Checked" origin={`${ago(data.checked_at, now)} · ${fmtClock(data.checked_at)}`} />
    </>
  )
}

// ── Recent platform activity ─────────────────────────────────────────────────

/** What changed on the platform lately — the newest rows of the append-only trail. */
export function AuditBlock({
  audit,
  now,
  portal,
  className,
}: {
  audit: Async<AuditLogResponse>
  now: number
  portal: string
  className?: string
}): ReactElement {
  return (
    <DataPanel
      className={cn('min-w-0', className)}
      as="h3"
      eyebrow="aegis · GET /audit"
      title="Recent platform activity"
      collapsible
      actions={<DeepLink href={`/app/${portal}/audit`} icon={ScrollText} label="Full audit trail" />}
    >
      {audit.state === 'loading' ? (
        <LoadingState rows={4} label="Reading the audit trail…" />
      ) : audit.state === 'error' ? (
        <ErrorState error={audit.error} fallback="The audit trail could not be read." />
      ) : audit.data.rows.length === 0 ? (
        <SceneState name="empty" size="sm">
          <p className="text-sm font-semibold text-foreground">Nothing recorded yet</p>
          <p className="text-sm text-muted-foreground">
            Every audited action on this platform lands here, newest first.
          </p>
        </SceneState>
      ) : (
        <Table>
          <THead>
            <TH className="w-px whitespace-nowrap">When</TH>
            <TH>Action</TH>
            <TH>Actor</TH>
            <TH className="w-px whitespace-nowrap">Outcome</TH>
          </THead>
          <TBody>
            {audit.data.rows.map((row) => (
              <TR key={row.id}>
                <TD className="whitespace-nowrap">
                  <Figure className="text-muted-foreground">{ago(row.ts, now)}</Figure>
                </TD>
                <TD>
                  {/* Not `break-all`: an action id is one token, and breaking it mid-word
                      renders `auth.login` as `auth.l ogin`. The panel scrolls instead. */}
                  <span className="tabular font-mono text-xs whitespace-nowrap text-foreground">{row.action}</span>
                </TD>
                <TD>
                  <span className="text-sm break-words text-muted-foreground">{row.actor ?? 'system'}</span>
                </TD>
                <TD>
                  <Badge tone={row.outcome === 'blocked' ? 'block' : 'ok'} className="gap-1.5">
                    {row.outcome === 'blocked' ? (
                      <ShieldX className="size-3.5" aria-hidden />
                    ) : (
                      <CircleCheck className="size-3.5" aria-hidden />
                    )}
                    {row.outcome}
                  </Badge>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}

// ── The log gap ──────────────────────────────────────────────────────────────

/**
 * The thing this screen deliberately does not have.
 *
 * The brief asked for live server logs. Aegis serves no application-log stream — the
 * only SSE log in the codebase is the per-document ingest log, which is a different
 * thing wearing a similar name — so there is nothing here to render and tailing a
 * file from the browser would be inventing a capability. It is stated in the slot the
 * panel would have occupied, because a silence an operator cannot see is one they
 * fill in themselves.
 *
 * **It is a mark, not a paragraph.** Stated full-width between the triage band and
 * the audit trail, it was the longest single block of prose on the screen — a
 * three-line essay about a panel that does not exist, sitting above the panels that
 * do. The claim is unchanged and the security reasoning is intact; it is one hover
 * or one tab away rather than always-on (§4, §5).
 */
export function LogsAbsence({ className }: { className?: string }): ReactElement {
  return (
    <AbsenceMark
      className={cn('min-w-0', className)}
      figure="Live server logs"
      why="Aegis serves no application-log stream; the only SSE log in the codebase is the per-document ingest log, which is a different thing wearing a similar name."
      needed="a redacted, RBAC-scoped log endpoint — a security decision before a rendering one"
    />
  )
}
