'use client'

import {
  CircleCheck,
  CircleSlash,
  Lock,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useMemo, useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { RankedBars } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { SceneState } from '@/components/illustration/Scene'
import { errorSentence } from '@/lib/api/apiError'
import { getSecurityPosture } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import { PostureMatrix } from './PostureMatrix'
import type {
  PostureEntry,
  PostureSignals,
  PostureStatus,
  SecurityPostureResponse,
} from '@/lib/api/platform'

/** One shape for every count on the screen, built once (DESIGN.md §3). */
const COUNT = new Intl.NumberFormat('en-US')

/**
 * Status → an honest tone + label + icon. `partial` is amber and NEVER dressed
 * as green — a control that only half-holds a threat down reads as partial.
 */
const STATUS_META: Record<
  PostureStatus,
  { tone: BadgeTone; label: string; icon: LucideIcon }
> = {
  enforced: { tone: 'ok', label: 'enforced', icon: CircleCheck },
  partial: { tone: 'risk', label: 'partial', icon: TriangleAlert },
  not_covered: { tone: 'block', label: 'not covered', icon: CircleSlash },
}

/** Coerce the (possibly widened) status string to a known band, defaulting honest. */
function bandOf(status: PostureStatus | string): PostureStatus {
  return status === 'enforced' || status === 'partial' || status === 'not_covered'
    ? status
    : 'not_covered'
}

/** A status pill — soft-tinted band with an icon, matching the trust taxonomy. */
function StatusPill({ status }: { status: PostureStatus | string }): ReactElement {
  const meta = STATUS_META[bandOf(status)]
  const Icon = meta.icon
  return (
    <Badge tone={meta.tone} className="gap-1.5">
      <Icon className="size-3" aria-hidden />
      {meta.label}
    </Badge>
  )
}

/** One threat row: threat id + name, the Aegis control (module · mechanism), status. */
function PostureRow({ entry }: { entry: PostureEntry }): ReactElement {
  return (
    <TR className="align-top">
      <TD className="whitespace-nowrap">
        <div className="flex flex-col gap-0.5">
          <Figure className="font-semibold tracking-wide text-muted-foreground uppercase">
            {entry.threat_id}
          </Figure>
          <span className="text-sm font-medium text-foreground">{entry.name}</span>
        </div>
      </TD>
      <TD>
        <div className="flex max-w-xl flex-col gap-1">
          <span className="flex items-center gap-1.5 text-sm font-medium text-foreground">
            {entry.control}
            {/*
              The per-row explanation used to be a third line of prose on every
              row — seventeen paragraphs stacked into a table, which is the exact
              shape DESIGN.md §4 sends to a tooltip. Nothing is deleted: the same
              sentence is one keystroke away, and the row is now scannable.
            */}
            {entry.detail ? (
              <InfoTip label={`How ${entry.control} holds this down`}>
                {entry.detail}
                {/*
                  `refs[]` was in the type and read by nothing. They are the exact
                  code symbols the status was introspected from — the receipt for
                  this row, and the reason the panel footer can claim the statuses
                  are derived rather than declared. Origin, then stop.
                */}
                {entry.refs.length > 0 ? (
                  <span className="mt-1.5 block font-mono text-[0.7rem] break-words text-muted-foreground">
                    {entry.refs.join(' · ')}
                  </span>
                ) : null}
              </InfoTip>
            ) : null}
          </span>
          <Figure className="text-muted-foreground">
            {`${entry.module} · ${entry.mechanism}`}
          </Figure>
        </div>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <StatusPill status={entry.status} />
      </TD>
    </TR>
  )
}

/**
 * Security posture — the `aegis.security` read-surface. Every OWASP-Agentic
 * threat is mapped to the concrete Aegis control holding it down, with an honest
 * status derived from real wiring signals: enforced, partial (amber — never
 * dressed as green) or not covered. Every band ships with an icon and a word, so
 * the verdict never rests on hue alone.
 */
function SecurityView(): ReactElement {
  // Read the live session token. `AuthProvider` restores the persisted session in
  // an effect that runs *after* this component's own effect on a reload, so a
  // constant `null` here would fetch with no bearer, 401 — and, being constant in
  // the dependency array, never retry once the real token arrived.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [data, setData] = useState<SecurityPostureResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  /**
   * Threats mapped to each Aegis module.
   *
   * `entry.module` is printed as text on every row and aggregated nowhere, so the
   * question the page cannot answer today is which part of the system is carrying
   * the surface. It counts every entry regardless of band — "mapped to", not "held
   * down by", which the matrix above already answers.
   */
  const byModule = useMemo(() => {
    const tally = new Map<string, number>()
    for (const entry of data?.entries ?? []) {
      tally.set(entry.module, (tally.get(entry.module) ?? 0) + 1)
    }
    return [...tally.entries()].map(([name, value]) => ({ name, value }))
  }, [data])

  useEffect(() => {
    // Wait for the persisted session to hydrate; fetching now would send no bearer.
    if (!hydrated) return
    let alive = true
    getSecurityPosture(token)
      .then((d) => {
        if (alive) {
          setData(d)
          setError(null)
        }
      })
      .catch((failure: unknown) => {
        if (alive) {
          setError(
            errorSentence(
              failure,
              'The security posture did not load. Check the backend is reachable, then retry.',
            ),
          )
        }
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="OWASP-Agentic · posture"
        title="Security"
        actions={
          <InfoTip label="Where these statuses come from">
            Every status is introspected from what the running process actually wired, never from
            a claim in a file.
          </InfoTip>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : data == null ? (
        <LoadingState rows={6} label="Reading the security posture…" />
      ) : (
        <>
          {data.entries.length === 0 ? (
            /*
              The screen had no absence state at all: an empty `entries[]` drew an
              empty board above an empty table, twice-silent. It is stated once
              here, and the wiring signals below still render — they are a separate
              fact and they arrive whether or not any threat row does.
            */
            <Card>
              <CardBody>
                <SceneState name="security" size="md">
                  <Absence
                    className="text-left"
                    figure="The agentic threat surface"
                    why="The posture endpoint answered with no threat rows."
                    needed="At least one threat → control mapping registered in aegis.security.posture."
                  />
                </SceneState>
              </CardBody>
            </Card>
          ) : (
            <>
              {/*
                The board first, the record second. Five stat tiles used to sit
                here and none of them answered the question the page exists for —
                how much of the agentic threat surface is actually held down. The
                matrix answers it in one glance and the table below is still the
                auditable row-by-row. The module roll-up shares the row: the same
                entries, read down the other axis.
              */}
              <div className="grid min-w-0 items-start gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
                <Card className="min-w-0">
                  <CardHeader
                    eyebrow="aegis.security · /security/posture"
                    title="The agentic threat surface"
                    actions={
                      <Badge tone="neutral" className="gap-1.5">
                        <Lock className="size-3" aria-hidden />
                        <Figure>{COUNT.format(data.entries.length)}</Figure> threats · mode{' '}
                        <Figure>{data.signals.mode}</Figure>
                      </Badge>
                    }
                  />
                  <CardBody className="pt-0">
                    <PostureMatrix entries={data.entries} />
                  </CardBody>
                </Card>

                <Card className="flex min-w-0 flex-col">
                  <CardHeader
                    eyebrow="entries[].module"
                    title="Threats carried per module"
                    actions={
                      <Badge tone="neutral" className="font-mono">
                        <Figure>{COUNT.format(byModule.length)}</Figure>
                      </Badge>
                    }
                  />
                  <CardBody className="flex min-h-0 flex-1 flex-col gap-4 pt-0">
                    <RankedBars
                      label="Threat rows mapped to each Aegis module"
                      data={byModule}
                      valueFormatter={(v) => COUNT.format(v)}
                      color="graph"
                      maxRows={6}
                    />
                    <Receipt
                      className="mt-auto"
                      origin="entries[].module"
                      detail="rows mapped to a module, not the bands they resolved to"
                    />
                  </CardBody>
                </Card>
              </div>

              <DataPanel
                eyebrow="threat → control"
                title="Every threat, and what holds it"
                maxHeight={560}
                footer={
                  <Receipt
                    origin="GET /security/posture"
                    detail="each status is derived from an introspected wiring signal below, never from a declaration in this file"
                    className="w-full border-t-0 pt-0"
                  />
                }
              >
                <Table className="min-w-[560px]">
                  <THead>
                    <TH className="text-left">Threat</TH>
                    <TH className="text-left">Aegis control</TH>
                    <TH className="text-right">Status</TH>
                  </THead>
                  <TBody>
                    {data.entries.map((entry) => (
                      <PostureRow key={entry.threat_id} entry={entry} />
                    ))}
                  </TBody>
                </Table>
              </DataPanel>
            </>
          )}

          <Card>
            <CardHeader eyebrow="aegis.security · signals" title="Wiring signals" />
            <CardBody className="pt-0">
              <SignalGrid signals={data.signals} />
            </CardBody>
          </Card>
        </>
      )}
    </div>
  )
}

/** A wiring fact's verdict — the ink it is set in and the icon it always ships with. */
const VERDICT = {
  ok: { ink: 'text-ok-ink', icon: CircleCheck },
  risk: { ink: 'text-risk-ink', icon: TriangleAlert },
  neutral: { ink: 'text-foreground', icon: null },
} as const

type Verdict = keyof typeof VERDICT

/** One wiring signal → an honest verdict. */
function signalVerdict(good: boolean): Verdict {
  return good ? 'ok' : 'risk'
}

/**
 * The introspected posture signals: seven wiring facts and the three numbers.
 *
 * It was ten identical bordered boxes, each holding one `Badge` of one to three
 * words — roughly a third of the page's area spent on about twenty words, which is
 * the "excess cards" shape DESIGN.md §9 names. The facts are a ruled two-column
 * list in one inset well now, and the three real numerals on the whole screen
 * (`rls_tables`, `max_plan_iterations`, `hazard_categories`) are set as figures
 * beside them rather than as text in a pill. Below three data points a chart is
 * whitespace, so these stay stated counts.
 *
 * `nemo guardrails` and `budget enforcement` live here rather than in a summary
 * band: both are facts about how this deployment is wired, not numbers.
 *
 * Every non-neutral fact carries an icon as well as its hue and its word — a
 * status that rests on colour alone is the failure DESIGN.md §2 forbids, and the
 * tinted badge it used to sit in carried no icon at all.
 */
function SignalGrid({ signals }: { signals: PostureSignals }): ReactElement {
  const facts: Array<{ label: string; value: string; verdict: Verdict }> = [
    {
      label: 'pii engine',
      value: signals.pii_engine,
      verdict: signalVerdict(!!signals.pii_engine),
    },
    {
      label: 'rls',
      value: signals.rls_fail_closed ? `fail-closed · ${signals.rls_enforced_on}` : 'fail-open',
      verdict: signalVerdict(signals.rls_fail_closed),
    },
    {
      label: 'jwt',
      value: signals.jwt_dev_secret ? `dev secret · ${signals.jwt_algorithm}` : signals.jwt_algorithm,
      verdict: signalVerdict(!signals.jwt_dev_secret),
    },
    {
      label: 'nemo guardrails',
      value: signals.nemo_available ? 'available' : 'not installed',
      verdict: signals.nemo_available ? 'ok' : 'neutral',
    },
    {
      label: 'budget enforcement',
      value: signals.budget_hook_wired && !signals.budget_fail_open ? 'fail-closed' : 'fail-open',
      verdict: signalVerdict(signals.budget_hook_wired && !signals.budget_fail_open),
    },
    {
      label: 'model-layer guardrails',
      value: signals.model_layer_wired ? 'wired' : 'not wired',
      verdict: signalVerdict(signals.model_layer_wired),
    },
    { label: 'gate min risk', value: signals.gate_min_risk, verdict: 'neutral' },
  ]

  const figures: Array<{ label: string; value: number }> = [
    { label: 'rls tables', value: signals.rls_tables },
    { label: 'max plan iterations', value: signals.max_plan_iterations },
    { label: 'hazard categories', value: signals.hazard_categories },
  ]

  return (
    <div className="grid min-w-0 items-start gap-4 lg:grid-cols-[minmax(0,1fr)_auto]">
      <dl className="grid min-w-0 grid-cols-1 gap-x-8 gap-y-1 rounded-lg border border-border bg-surface-2/40 p-4 sm:grid-cols-2">
        {facts.map((f) => {
          const meta = VERDICT[f.verdict]
          const Icon = meta.icon
          return (
            /* Below `sm` the value stacks under its label: inline, a long label
               like "model-layer guardrails" squeezed "not wired" to "not wi…" at
               390px, which is the one place a truncated status is a lie. */
            <div
              key={f.label}
              className="flex min-w-0 flex-col gap-0.5 py-1 sm:flex-row sm:items-center sm:justify-between sm:gap-3"
            >
              <dt className="eyebrow shrink-0">{f.label}</dt>
              <dd className={cn('flex min-w-0 items-center gap-1.5', meta.ink)}>
                {Icon ? <Icon className="size-3.5 shrink-0" aria-hidden /> : null}
                <Figure className="min-w-0">
                  {/* Engine names and algorithms are identifiers — a narrow column
                      clips them rather than widening the page. */}
                  <span className="min-w-0 truncate" title={f.value} translate="no">
                    {f.value}
                  </span>
                </Figure>
              </dd>
            </div>
          )
        })}
      </dl>

      <dl className="grid min-w-0 grid-cols-3 gap-4 lg:w-64">
        {figures.map((f) => (
          <div key={f.label} className="min-w-0">
            <dt className="eyebrow mb-1">{f.label}</dt>
            <dd>
              <Figure size="stat" className="text-foreground">
                {COUNT.format(f.value)}
              </Figure>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/** Client entry for the Security section — gated on a reachable backend. */
export function SecurityMount(): ReactElement {
  return (
    <BackendGate>
      <SecurityView />
    </BackendGate>
  )
}
