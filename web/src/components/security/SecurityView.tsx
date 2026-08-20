'use client'

import {
  CircleCheck,
  CircleSlash,
  Lock,
  TriangleAlert,
  type LucideIcon,
} from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
import { getSecurityPosture } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { PostureMatrix } from './PostureMatrix'
import type {
  PostureEntry,
  PostureSignals,
  PostureStatus,
  SecurityPostureResponse,
} from '@/lib/api/platform'

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
            {entry.detail ? <InfoTip label={`How ${entry.control} holds this down`}>{entry.detail}</InfoTip> : null}
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
            One row per threat, the Aegis control holding it down, and a status derived from what
            is actually wired — introspected from the running process, never from a claim in a
            file. `partial` is amber and is never dressed as green.
          </InfoTip>
        }
      />

      {error ? (
        <ErrorState error={error} />
      ) : data == null ? (
        <LoadingState rows={6} label="Reading the security posture…" />
      ) : (
        <>
          {/*
            The board first, the record second. Five stat tiles used to sit here
            and none of them answered the question the page exists for — how much
            of the agentic threat surface is actually held down. The matrix answers
            it in one glance and the table below is still the auditable row-by-row.
          */}
          <Card>
            <CardHeader
              eyebrow="aegis.security · /security/posture"
              title="The agentic threat surface"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Lock className="size-3" aria-hidden />
                  <Figure>{data.entries.length}</Figure> threats · mode{' '}
                  <Figure>{data.signals.mode}</Figure>
                </Badge>
              }
            />
            <CardBody className="pt-0">
              <PostureMatrix entries={data.entries} />
            </CardBody>
          </Card>

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

/** One wiring signal → an honest tone + rendered value. */
function signalTone(good: boolean): BadgeTone {
  return good ? 'ok' : 'risk'
}

/**
 * The introspected posture signals, rendered as a compact fact grid.
 *
 * `nemo guardrails` and `budget enforcement` live here rather than in the summary band:
 * both are facts about how this deployment is wired, which is exactly what this grid
 * holds, and neither is a number that belongs on a stat tile.
 */
function SignalGrid({ signals }: { signals: PostureSignals }): ReactElement {
  const facts: Array<{ label: string; value: string; tone: BadgeTone }> = [
    { label: 'pii engine', value: signals.pii_engine, tone: signalTone(!!signals.pii_engine) },
    {
      label: 'rls',
      value: signals.rls_fail_closed ? `fail-closed · ${signals.rls_enforced_on}` : 'fail-open',
      tone: signalTone(signals.rls_fail_closed),
    },
    { label: 'rls tables', value: String(signals.rls_tables), tone: 'neutral' },
    {
      label: 'jwt',
      value: signals.jwt_dev_secret ? `dev secret · ${signals.jwt_algorithm}` : signals.jwt_algorithm,
      tone: signalTone(!signals.jwt_dev_secret),
    },
    {
      label: 'nemo guardrails',
      value: signals.nemo_available ? 'available' : 'not installed',
      tone: signals.nemo_available ? 'ok' : 'neutral',
    },
    {
      label: 'budget enforcement',
      value:
        signals.budget_hook_wired && !signals.budget_fail_open ? 'fail-closed' : 'fail-open',
      tone: signalTone(signals.budget_hook_wired && !signals.budget_fail_open),
    },
    { label: 'gate min risk', value: signals.gate_min_risk, tone: 'neutral' },
    { label: 'max plan iterations', value: String(signals.max_plan_iterations), tone: 'neutral' },
    { label: 'hazard categories', value: String(signals.hazard_categories), tone: 'neutral' },
    {
      label: 'model-layer guardrails',
      value: signals.model_layer_wired ? 'wired' : 'not wired',
      tone: signalTone(signals.model_layer_wired),
    },
  ]
  return (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {facts.map((f) => (
        <div
          key={f.label}
          className="flex flex-col gap-1.5 rounded-lg border border-border bg-surface-2/40 p-3.5"
        >
          <dt className="eyebrow">{f.label}</dt>
          <dd>
            <Badge tone={f.tone} className="w-fit font-mono">
              {f.value}
            </Badge>
          </dd>
        </div>
      ))}
    </dl>
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
