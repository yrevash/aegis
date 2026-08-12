'use client'

import {
  CircleCheck,
  CircleSlash,
  Coins,
  Loader2,
  Lock,
  ShieldAlert,
  ShieldCheck,
  TriangleAlert,
  WifiOff,
} from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge, type BadgeTone } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/Table'
import { getSecurityPosture } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import type {
  PostureEntry,
  PostureSignals,
  PostureStatus,
  SecurityPostureResponse,
} from '@/lib/api/platform'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'

/**
 * Status → an honest tone + label + icon. `partial` is amber and NEVER dressed
 * as green — a control that only half-holds a threat down reads as partial.
 */
const STATUS_META: Record<
  PostureStatus,
  { tone: BadgeTone; label: string; icon: typeof CircleCheck }
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
      <Icon className="size-3" />
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
          <span className="font-mono text-[0.7rem] font-semibold uppercase tracking-wide text-muted-foreground">
            {entry.threat_id}
          </span>
          <span className="text-sm font-medium text-foreground">{entry.name}</span>
        </div>
      </TD>
      <TD>
        <div className="flex max-w-xl flex-col gap-1">
          <span className="text-sm font-medium text-foreground">{entry.control}</span>
          <span className="font-mono text-[0.7rem] text-muted-foreground">
            {entry.module} · {entry.mechanism}
          </span>
          {entry.detail ? (
            <span className="text-[0.72rem] leading-snug text-muted-foreground">{entry.detail}</span>
          ) : null}
        </div>
      </TD>
      <TD className="whitespace-nowrap text-right">
        <StatusPill status={entry.status} />
      </TD>
    </TR>
  )
}

/** Tally the three status bands across every entry. */
function tally(entries: PostureEntry[]): Record<PostureStatus, number> {
  const counts: Record<PostureStatus, number> = { enforced: 0, partial: 0, not_covered: 0 }
  for (const e of entries) counts[bandOf(e.status)] += 1
  return counts
}

/**
 * Security posture — the `aegis.security` read-surface. Every OWASP-Agentic
 * threat is mapped to the concrete Aegis control holding it down, with an honest
 * status derived from real wiring signals: enforced (green), partial (amber —
 * never dressed as green) or not covered (red). Summary tiles count the bands
 * and surface a couple of key introspected signals (NeMo availability, the
 * budget chokepoint), so a viewer sees exactly where the defense is complete and
 * where it is only half-wired.
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
      .catch(() => {
        if (alive) setError('Could not load the security posture. Is the backend running?')
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const counts = useMemo(() => (data ? tally(data.entries) : null), [data])

  return (
    <div className="space-y-6">
      {/* Section header */}
      <div>
        <p className="eyebrow mb-1">OWASP-Agentic · posture</p>
        <h1 className="t-hero text-foreground">Security</h1>
      </div>

      {error ? (
        <Card>
          <CardBody>
            <p className="py-8 text-center text-sm text-danger">{error}</p>
          </CardBody>
        </Card>
      ) : data == null || counts == null ? (
        <Card>
          <CardBody>
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="size-4 animate-spin" />
              Loading security posture…
            </div>
          </CardBody>
        </Card>
      ) : (
        <>
          {/* ── Summary tiles ─────────────────────────────────────────────────── */}
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
            <StatCard label="Enforced" value={String(counts.enforced)} icon={ShieldCheck} tone="ok" />
            <StatCard label="Partial" value={String(counts.partial)} icon={TriangleAlert} tone="risk" />
            <StatCard
              label="Not covered"
              value={String(counts.not_covered)}
              icon={ShieldAlert}
              tone="block"
            />
            <StatCard
              label="NeMo guardrails"
              value={data.signals.nemo_available ? 'available' : 'off'}
              icon={ShieldCheck}
              tone={data.signals.nemo_available ? 'ok' : 'neutral'}
            />
            <StatCard
              label="Budget enforcement"
              value={
                data.signals.budget_hook_wired && !data.signals.budget_fail_open ? 'fail-closed' : 'open'
              }
              icon={Coins}
              tone={
                data.signals.budget_hook_wired && !data.signals.budget_fail_open ? 'ok' : 'risk'
              }
            />
          </div>

          {/* ── Posture table ─────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.security · /security/posture"
              title="Threat → control posture"
              description="One row per OWASP-Agentic threat, its Aegis control (module · mechanism), and a status derived from real wiring signals."
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Lock className="size-3" />
                  {data.entries.length} threats · mode {data.signals.mode}
                </Badge>
              }
            />
            <CardBody className="pt-0">
              <div className="overflow-hidden rounded-xl border border-border">
                <Table>
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
              </div>
            </CardBody>
          </Card>

          {/* ── Wiring signals ────────────────────────────────────────────────── */}
          <Card>
            <CardHeader
              eyebrow="aegis.security · signals"
              title="Wiring signals"
              description="The introspected facts the statuses above derive from — the honest provenance of each verdict."
            />
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

/** The introspected posture signals, rendered as a compact fact grid. */
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
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
      {facts.map((f) => (
        <div
          key={f.label}
          className="flex flex-col gap-1.5 rounded-xl border border-border bg-surface-2/40 p-3.5"
        >
          <span className="eyebrow">{f.label}</span>
          <Badge tone={f.tone} className="w-fit font-mono">
            {f.value}
          </Badge>
        </div>
      ))}
    </div>
  )
}

/**
 * Client entry for the Security section. Runs the boot probe once (live-first,
 * mock fallback) before mounting the view, so the posture fetch reads the
 * resolved mode — the offline demo seeds from the mock fixture and is labelled
 * with the honest banner.
 */
export function SecurityMount(): ReactElement {
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

  if (mode === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <div>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className="mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white"
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <SecurityView />
    </div>
  )
}
