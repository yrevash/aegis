'use client'

import {
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
  Download,
  RefreshCw,
  Search,
  ShieldAlert,
  ShieldCheck,
  WifiOff,
} from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { checkPatches, getAdvisories, getSbom } from '@/lib/api/client'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/primitives/button'
import { Card, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { Scene, SceneState } from '@/components/illustration/Scene'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type {
  AdvisoryAuditResponse,
  AdvisoryPackage,
  PatchCheckResponse,
  PatchResult,
} from '@/lib/api/types'

import {
  PATCH_STATUS_LABEL,
  POSTURE_LABEL,
  filterByName,
  patchPosture,
  sortByStatus,
  summarizePatches,
  type PatchPosture,
  type PatchStatus,
  type PatchSummary,
} from './stackDisplay'

/**
 * DevOps — Patch Check.
 *
 * Compares each installed pin against the latest published release. The honesty
 * rule here is load-bearing: a patch claim you cannot verify is worse than none,
 * so when the registry is unreachable (`online: false`) the banner says so
 * plainly and the posture never resolves to "up to date" — an offline check is
 * always shown as unverified.
 *
 * Counting / ordering / the offline posture rule live in the recharts-free
 * `stackDisplay` module (unit-tested); this file fetches and renders.
 *
 * **The offline path is a stated absence, not a grey chart.** `PatchResult.status`
 * only becomes `current`/`outdated` after a real registry answer, so an offline
 * check makes every row `unknown` — and the freshness split was rendering that as
 * one flat neutral strip above a table of "registry did not answer". A chart with
 * no information in it reads as breakage, which is the opposite of what this
 * screen is for, so the split is replaced by an {@link Absence} in the slot it
 * would have occupied.
 */

type LoadState =
  | { status: 'idle' }
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: PatchCheckResponse }

/**
 * Module-level formatters (DESIGN.md §3 / §4). Building an `Intl` instance per
 * render is the expensive half of formatting, and a per-call `toLocaleString`
 * also drifts: two timestamps on one screen must not disagree about their shape.
 */
const COUNT = new Intl.NumberFormat('en-US')
const STAMP = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** An ISO scalar rendered in the one timestamp shape, or stated as unparseable. */
function stamp(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? 'unparseable timestamp' : STAMP.format(at)
}

const STATUS_TONE: Record<PatchStatus, string> = {
  current: 'text-ok-ink',
  outdated: 'text-block-ink',
  unknown: 'text-muted-foreground',
}

const POSTURE_VARIANT: Record<PatchPosture, 'risk' | 'ok' | 'neutral'> = {
  'action-needed': 'risk',
  current: 'ok',
  unverified: 'neutral',
}

export function PatchCheck({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'idle' })
  const [filter, setFilter] = useState('')

  const run = useCallback(() => {
    let alive = true
    setLoad({ status: 'loading' })
    checkPatches(undefined, token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch((e: unknown) =>
        alive &&
        setLoad({ status: 'error', message: e instanceof Error ? e.message : 'Patch check failed' }),
      )
    return () => {
      alive = false
    }
  }, [token])

  useEffect(() => run(), [run])

  const summary = load.status === 'ready' ? summarizePatches(load.data.results) : null
  const posture =
    load.status === 'ready' && summary ? patchPosture(summary, load.data.online) : null
  const rows =
    load.status === 'ready' ? sortByStatus(filterByName(load.data.results, filter)) : []

  if (load.status === 'idle' || load.status === 'loading') {
    return (
      <Card>
        <CardBody>
          <LoadingState rows={5} label="Checking package freshness…" />
        </CardBody>
      </Card>
    )
  }

  if (load.status === 'error') {
    return (
      <Card>
        <CardBody>
          <ErrorState
            error={load.message}
            fallback="The patch check could not be run."
            retry={() => run()}
          />
        </CardBody>
      </Card>
    )
  }

  if (summary == null || posture == null) return <span />

  const online = load.data.online

  if (summary.total === 0) {
    /*
      A check that returned nothing gets one card, not the verdict apparatus.
      Rendered through the normal path it read "up to date · verified against the
      registry" over an empty bar and three zeroes — a clean bill of health for a
      stack nobody looked at, which is the exact claim this screen exists to
      refuse. `patchPosture` still resolves an empty online summary to `current`;
      that is a latent honesty gap in `stackDisplay`, and it is reported rather
      than changed here because this pass is presentation only.
    */
    return (
      <Card>
        <CardBody className="flex flex-col gap-5">
          <div className="flex items-center">
            <Button size="sm" className="ml-auto" onClick={() => run()}>
              <RefreshCw className="size-3.5" aria-hidden />
              Re-check
            </Button>
          </div>
          <SceneState name="empty" size="md">
            <Absence
              className="text-left"
              figure="Package freshness"
              why="The check ran and returned no packages."
              needed="At least one resolvable dependency pin in the running process."
            />
          </SceneState>
          <Receipt label="Checked" origin={stamp(load.data.checked_at)} />
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* The verdict, and the freshness split that reconciles it. */}
      <Card>
        <CardBody className="flex flex-col gap-5">
          <div className="flex flex-wrap items-center gap-3">
            <Badge tone={POSTURE_VARIANT[posture]} className="gap-1.5">
              {posture === 'action-needed' ? (
                <AlertTriangle className="size-3 shrink-0" aria-hidden />
              ) : posture === 'current' ? (
                <CheckCircle2 className="size-3 shrink-0" aria-hidden />
              ) : online ? (
                <CircleSlash className="size-3 shrink-0" aria-hidden />
              ) : (
                <WifiOff className="size-3 shrink-0" aria-hidden />
              )}
              {POSTURE_LABEL[posture]}
            </Badge>
            {/*
              Only the reachable case gets a sentence here. When the registry did
              not answer, the stated absence below says so in the slot the split
              would have occupied — saying it twice is the text bomb this pass
              exists to remove.
            */}
            {online && (
              <span className="inline-flex items-center gap-1.5 text-[0.8125rem] text-ok-ink">
                <CheckCircle2 className="size-3.5 shrink-0" aria-hidden />
                Verified against the registry
              </span>
            )}
            <InfoTip label="Why an offline check is never “current”">
              A patch claim nobody could verify is worse than none, so when the registry does not
              answer the posture stays “unverified” and never resolves to up to date.{' '}
              {load.data.note}
            </InfoTip>
            <Button
              size="sm"
              className="ml-auto"
              onClick={() => run()}
              disabled={load.status !== 'ready'}
            >
              <RefreshCw className="size-3.5" aria-hidden />
              Re-check
            </Button>
          </div>

          {online ? (
            <FreshnessBar summary={summary} />
          ) : (
            /*
              The defect this replaces: offline, every row is `unknown` by
              construction (the API only emits current/outdated after a real
              registry answer), so the bar rendered as one flat grey strip with
              three zeroes beside it — a chart of nothing, on the one screen whose
              subject is honesty about staleness. It is a stated absence instead.
            */
            <div className="grid min-w-0 items-center gap-5 sm:grid-cols-[auto_minmax(0,1fr)]">
              <Scene name="diagnose" size="sm" className="mx-auto" />
              <Absence
                figure="Freshness split"
                why={`The registry did not answer, so none of the ${COUNT.format(summary.total)} packages below could be compared with a published release.`}
                needed="One reachable registry call — re-check once egress is restored."
              />
            </div>
          )}
        </CardBody>
      </Card>

      <Advisories token={token} />

      <DataPanel
        eyebrow="installed vs latest"
        title="Every pin, worst first"
        maxHeight={560}
        toolbar={
          <label className="flex w-full items-center gap-2 rounded-md border border-border bg-surface-2/40 px-2.5 py-1.5 text-sm focus-within:ring-2 focus-within:ring-ring sm:max-w-xs">
            <Search className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
            <input
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter packages…"
              aria-label="Filter packages by name"
              className="w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted-foreground/60"
            />
          </label>
        }
        actions={
          <Badge tone="neutral" className="gap-1.5">
            <ShieldCheck className="size-3 shrink-0" aria-hidden />
            <Figure>{rows.length}</Figure>
          </Badge>
        }
        footer={
          <Receipt
            label="Checked"
            origin={stamp(load.data.checked_at)}
            detail={online ? 'against the package registry' : 'registry unreachable — statuses are unverified'}
            className="w-full border-t-0 pt-0"
          />
        }
      >
        <Table className="min-w-[620px]">
          <THead>
            <TH className="text-left">Package</TH>
            <TH className="text-left">Installed</TH>
            <TH className="text-left">Latest</TH>
            <TH className="text-left">Status</TH>
            <TH className="text-left">Note</TH>
          </THead>
          <TBody>
            {rows.map((r) => (
              <PatchRow key={r.name} result={r} />
            ))}
            {/* Reachable only as a filter miss now — an empty check never gets
                this far, and it used to announce itself as `No packages match ""`. */}
            {rows.length === 0 && (
              <tr>
                <td colSpan={5} className="px-4 py-6 text-center text-sm text-muted-foreground">
                  No packages match &ldquo;{filter.trim()}&rdquo;.
                </td>
              </tr>
            )}
          </TBody>
        </Table>
      </DataPanel>
    </div>
  )
}

/**
 * The freshness split — one bar, three ordered bands, drawn from the same counts
 * the table below reconciles against.
 *
 * The screen used to state those counts as three chips, and a reader had to do
 * the division themselves to know whether "4 outdated" was a rounding error or a
 * third of the stack. Status hues carry it here because this *is* the reserved
 * status set (DESIGN.md §2), and every band ships with its icon and its word.
 *
 * It only ever renders for a check that reached the registry. The offline case
 * used to render this same bar with every band forced neutral — which is a chart
 * with no information in it — and the caller states an {@link Absence} there now.
 */
function FreshnessBar({ summary }: { summary: PatchSummary }): ReactElement {
  const bands = [
    { key: 'outdated', label: 'update available', count: summary.outdated, fill: 'bg-block', ink: 'text-block-ink', Icon: AlertTriangle },
    { key: 'unknown', label: 'unverified', count: summary.unknown, fill: 'bg-surface-2', ink: 'text-muted-foreground', Icon: WifiOff },
    { key: 'current', label: 'current', count: summary.current, fill: 'bg-ok', ink: 'text-ok-ink', Icon: CheckCircle2 },
  ] as const
  const total = Math.max(1, summary.total)
  return (
    <div className="flex flex-col gap-3">
      <div
        className="flex h-4 w-full gap-0.5 overflow-hidden rounded-full bg-surface-2"
        role="img"
        aria-label={bands
          .map((b) => `${b.count} of ${summary.total} ${b.label}`)
          .join('; ')}
      >
        {bands.map((b) =>
          b.count === 0 ? null : (
            <span
              key={b.key}
              className={cn('h-full first:rounded-l-full last:rounded-r-full', b.fill)}
              style={{ width: `${(b.count / total) * 100}%` }}
            />
          ),
        )}
      </div>
      <ul className="flex flex-wrap items-center gap-x-5 gap-y-2">
        {bands.map((b) => (
          <li key={b.key} className="flex items-center gap-1.5">
            <b.Icon className={cn('size-3.5 shrink-0', b.ink)} aria-hidden />
            <Figure size="stat" className="text-foreground">
              {b.count}
            </Figure>
            <span className="text-[0.8125rem] text-muted-foreground">{b.label}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** One package row; outdated rows carry a subtle warning wash. */
function PatchRow({ result }: { result: PatchResult }): ReactElement {
  return (
    <TR className={cn(result.status === 'outdated' && 'bg-block/5')}>
      <TD className="font-medium">{result.name}</TD>
      <TD className="whitespace-nowrap">
        {result.installed === null ? (
          <NotPublished what="not installed" />
        ) : (
          <Figure className="text-muted-foreground">{result.installed}</Figure>
        )}
      </TD>
      <TD className="whitespace-nowrap">
        {result.latest === null ? (
          <NotPublished what="registry did not answer" />
        ) : (
          <Figure className="text-muted-foreground">{result.latest}</Figure>
        )}
      </TD>
      <TD className="whitespace-nowrap">
        <span
          className={cn(
            'inline-flex items-center gap-1.5 text-[0.8125rem] font-medium',
            STATUS_TONE[result.status],
          )}
        >
          {result.status === 'outdated' ? (
            <AlertTriangle className="size-3 shrink-0" aria-hidden />
          ) : result.status === 'current' ? (
            <CheckCircle2 className="size-3 shrink-0" aria-hidden />
          ) : (
            <WifiOff className="size-3 shrink-0" aria-hidden />
          )}
          {PATCH_STATUS_LABEL[result.status]}
        </span>
      </TD>
      <TD className="text-[0.8125rem] text-muted-foreground">
        {result.note ?? <NotPublished what="no note" />}
      </TD>
    </TR>
  )
}

/**
 * A version the check could not establish.
 *
 * It was an em dash, which on this table is actively misleading: an unreachable
 * registry and an uninstalled package produce the same blank, and the whole point
 * of the page is that a patch claim you cannot verify is worse than none.
 */
function NotPublished({ what }: { what: string }): ReactElement {
  return <span className="text-xs text-muted-foreground italic">{what}</span>
}

/** Client entry for the Patch Check section — gated on a reachable backend. */
export function PatchMount(): ReactElement {
  // Hand the child the real session bearer, and hold it back until the persisted
  // session has been restored — mounting with a constant `null` would fetch with
  // no `Authorization` header and never retry.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/40 p-4">
        <LoadingState rows={4} label="Restoring the session…" />
      </div>
    )
  }

  return (
    <BackendGate>
      <div className="space-y-4">
        <PageHeader eyebrow="installed vs latest" title="Patch check" />
        <PatchCheck token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}

/**
 * Published advisories against the versions actually installed — the verdict the
 * freshness check above is not.
 *
 * The distinction is the whole reason this is a second panel rather than a column
 * in the first one. "Three releases behind" and "carries a published CVE" are
 * different facts, they move independently, and a screen that renders one of them
 * where a reader expects the other is how a stack gets signed off as safe because
 * it was recently updated.
 *
 * Two honesty rules travel from the API and are rendered rather than smoothed:
 *
 * 1. **A package is `clean` only after the advisory database actually answered.**
 *    Offline, every row is `unknown` and the panel says so — it never resolves to
 *    "no known vulnerabilities", because an audit that could not run is not an
 *    audit that found nothing.
 * 2. **`passed` is false when anything is unknown**, not only when something is
 *    vulnerable. That is why the verdict badge has three states and not two.
 *
 * The SBOM exports sit here because they answer the same question one step out:
 * the reader who wants their own scanner's opinion rather than ours.
 */
function Advisories({ token }: { token: string | null }): ReactElement {
  const [state, setState] = useState<
    | { status: 'idle' }
    | { status: 'loading' }
    | { status: 'error'; message: string }
    | { status: 'ready'; data: AdvisoryAuditResponse }
  >({ status: 'idle' })

  const audit = useCallback(() => {
    let alive = true
    setState({ status: 'loading' })
    getAdvisories(undefined, token)
      .then((data) => alive && setState({ status: 'ready', data }))
      .catch(
        (e: unknown) =>
          alive &&
          setState({
            status: 'error',
            message: e instanceof Error ? e.message : 'The advisory audit could not be run.',
          }),
      )
    return () => {
      alive = false
    }
  }, [token])

  useEffect(() => audit(), [audit])

  const download = useCallback(
    (format: 'cyclonedx' | 'spdx') => {
      void getSbom(format, token).then((text) => {
        const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
        const link = document.createElement('a')
        link.href = url
        link.download = `aegis-sbom.${format}.json`
        link.click()
        URL.revokeObjectURL(url)
      })
    },
    [token],
  )

  const exports = (
    <div className="flex flex-wrap items-center gap-2">
      <Button size="sm" variant="outline" onClick={() => download('cyclonedx')}>
        <Download className="size-3.5" aria-hidden />
        CycloneDX 1.6
      </Button>
      <Button size="sm" variant="outline" onClick={() => download('spdx')}>
        <Download className="size-3.5" aria-hidden />
        SPDX 2.3
      </Button>
    </div>
  )

  if (state.status === 'idle' || state.status === 'loading') {
    return (
      <Card>
        <CardBody>
          <LoadingState rows={3} label="Asking the advisory database…" />
        </CardBody>
      </Card>
    )
  }

  if (state.status === 'error') {
    return (
      <Card>
        <CardBody className="flex flex-col gap-4">
          <ErrorState
            error={state.message}
            fallback="The advisory audit could not be run."
            retry={() => audit()}
          />
          {exports}
        </CardBody>
      </Card>
    )
  }

  const data: AdvisoryAuditResponse = state.data
  const vulnerable: AdvisoryPackage[] = (data.packages ?? []).filter(
    (p: AdvisoryPackage) => p.status === 'vulnerable',
  )
  const unknown = data.packages_unknown ?? 0
  const tone = data.passed ? 'ok' : vulnerable.length > 0 ? 'risk' : 'neutral'
  const label = data.passed
    ? 'no published advisories'
    : vulnerable.length > 0
      ? `${COUNT.format(vulnerable.length)} package${vulnerable.length === 1 ? '' : 's'} with a published advisory`
      : 'unverified — the advisory database did not answer'

  return (
    <DataPanel
      eyebrow="aegis · POST /stack/advisories"
      title="Published advisories, worst first"
      maxHeight={420}
      toolbar={
        <div className="flex flex-wrap items-center gap-3">
          <Badge tone={tone} className="gap-1.5">
            {data.passed ? (
              <ShieldCheck className="size-3 shrink-0" aria-hidden />
            ) : vulnerable.length > 0 ? (
              <ShieldAlert className="size-3 shrink-0" aria-hidden />
            ) : (
              <WifiOff className="size-3 shrink-0" aria-hidden />
            )}
            {label}
          </Badge>
          <InfoTip label="Why this is not the freshness check above">
            A package can be three releases behind with no advisory against it, and on the newest
            release with four. This asks the advisory database about the exact installed version,
            and a package is only ever “clean” after it really answered. {data.note}
          </InfoTip>
        </div>
      }
      actions={exports}
      footer={
        <Receipt
          label="Audited"
          origin={stamp(data.checked_at)}
          detail={
            data.online
              ? `${COUNT.format(data.packages_audited ?? 0)} distributions against ${data.source}`
              : 'advisory database unreachable — every verdict is unverified'
          }
          className="w-full border-t-0 pt-0"
        />
      }
    >
      {vulnerable.length === 0 ? (
        <div className="px-4 py-6">
          <Absence
            className="text-left"
            figure="Published advisories"
            why={
              data.online
                ? `The advisory database answered for ${COUNT.format(data.packages_audited ?? 0)} installed distributions and had nothing against any of them.`
                : `The advisory database did not answer, so none of the ${COUNT.format(data.packages_audited ?? 0)} installed distributions has a verdict.`
            }
            needed={
              data.online
                ? 'Nothing — this is the passing state.'
                : `One reachable call to ${data.source}. ${COUNT.format(unknown)} packages are unverified until then.`
            }
          />
        </div>
      ) : (
        <Table className="min-w-[620px]">
          <THead>
            <TH className="text-left">Package</TH>
            <TH className="text-left">Installed</TH>
            <TH className="text-left">Severity</TH>
            <TH className="text-left">Advisories</TH>
          </THead>
          <TBody>
            {vulnerable.map((p: AdvisoryPackage) => (
              <AdvisoryRow key={p.name} pkg={p} />
            ))}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}

/** One vulnerable distribution, with the identifiers a reader recognises. */
function AdvisoryRow({ pkg }: { pkg: AdvisoryPackage }): ReactElement {
  // GHSA and PYSEC records alias the same CVE, so the raw list prints one advisory
  // twice and reads as two. The CVE is the identifier a reader looks up.
  const ids: string[] = []
  for (const v of pkg.vulnerabilities ?? []) {
    const id = (v.aliases ?? []).find((a) => a.startsWith('CVE-')) ?? v.id
    if (!ids.includes(id)) ids.push(id)
  }
  const first = (pkg.vulnerabilities ?? [])[0]
  return (
    <TR className="bg-block/5">
      <TD className="font-medium">{pkg.name}</TD>
      <TD className="tabular-nums text-muted-foreground">{pkg.version}</TD>
      <TD className="text-block-ink">{pkg.worst_severity}</TD>
      <TD className="text-muted-foreground">
        <span className="font-mono text-[0.8125rem]">{ids.join(', ')}</span>
        {first?.summary ? <div className="mt-0.5 text-[0.8125rem]">{first.summary}</div> : null}
      </TD>
    </TR>
  )
}
