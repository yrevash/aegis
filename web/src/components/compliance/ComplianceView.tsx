'use client'

import { ChevronRight, FileText, Route, ScrollText, ShieldAlert, TestTube } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { errorSentence } from '@/lib/api/apiError'
import { getCompliance } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import { CoverageLegend, CoverageStrip, STATE_META, stateOf } from './CoverageStrip'
import { ResidencyPanel } from './ResidencyPanel'
import type {
  ComplianceControl,
  ComplianceEvidence,
  ComplianceFramework,
  ComplianceResponse,
} from '@/lib/api/platform'

/** One shape for every count on the screen, built once (DESIGN.md §3). */
const COUNT = new Intl.NumberFormat('en-US')

/** Evidence kind → the glyph that says what sort of artefact the reference names. */
const EVIDENCE_ICON = {
  file: FileText,
  doc: ScrollText,
  route: Route,
  test: TestTube,
} as const

/** A state pill — icon, word and hue together, never hue alone (DESIGN.md §2). */
function StatePill({ state }: { state: string }): ReactElement {
  const meta = STATE_META[stateOf(state)]
  const Icon = meta.icon
  return (
    <span
      className={cn(
        'inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[0.75rem] font-medium',
        meta.cell,
      )}
    >
      <Icon className="size-3 shrink-0" aria-hidden />
      {meta.label}
    </span>
  )
}

/**
 * One evidence reference, rendered as what it is: a path, a route or a test node id.
 *
 * Set as a figure because every one of them is an identifier a reviewer will copy
 * into a terminal, and identifiers that reflow between proportional glyphs are
 * harder to read back than the sentence beside them.
 */
function EvidenceRow({ evidence }: { evidence: ComplianceEvidence }): ReactElement {
  const Icon = EVIDENCE_ICON[evidence.kind as keyof typeof EVIDENCE_ICON] ?? FileText
  return (
    <li className="flex min-w-0 items-start gap-2 py-1">
      <Icon className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <div className="flex min-w-0 flex-col gap-0.5">
        <Figure className="min-w-0 break-all text-foreground">
          <span translate="no">{evidence.ref}</span>
        </Figure>
        <span className="text-xs text-muted-foreground">{evidence.label}</span>
      </div>
    </li>
  )
}

/**
 * One control row: the mark first, the evidence one layer down.
 *
 * Ninety-one controls each carrying a summary sentence and a gap sentence would be
 * the text bomb DESIGN.md §4 exists to delete — roughly six hundred lines of prose
 * stacked into a table whose header already knows the answer. So the closed row is
 * an id, a title and a state; opening it reveals what Aegis actually does, what is
 * missing, and every checkable reference. Nothing is dropped, and the disclosure is
 * reachable by keyboard as well as by pointer.
 */
function ControlRow({ control }: { control: ComplianceControl }): ReactElement {
  const [open, setOpen] = useState(false)
  const panelId = `control-${control.id.replace(/[^A-Za-z0-9]+/g, '-')}`
  const meta = STATE_META[stateOf(control.state)]

  return (
    <li className="border-b border-border last:border-b-0">
      <button
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
        className="flex w-full min-w-0 items-center gap-3 px-1 py-2.5 text-left transition-colors duration-[--dur-fast] hover:bg-surface-2/60 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <ChevronRight
          aria-hidden
          className={cn(
            'size-4 shrink-0 text-muted-foreground transition-transform duration-[--dur-fast]',
            open && 'rotate-90',
          )}
        />
        {/*
          Below `sm` the title stacks under the id rather than sharing the line.
          Inline at 390px, a fixed id column left about ninety pixels for the
          title and every row read "Prom…", "Sensi…", "Supply …" — a table of
          ellipses, which is the one place a truncated label is a lie about the
          control it names. Two lines per row is the cheaper cost.
        */}
        <span className="flex min-w-0 flex-1 flex-col gap-0.5 sm:flex-row sm:items-center sm:gap-3">
          <Figure className="font-semibold text-foreground sm:w-32 sm:shrink-0">
            <span translate="no">{control.id}</span>
          </Figure>
          <span className="min-w-0 text-sm text-foreground sm:flex-1 sm:truncate">
            {control.title}
          </span>
        </span>
        {control.evidence.length > 0 ? (
          <span className="hidden shrink-0 text-xs text-muted-foreground sm:inline">
            <Figure>{control.evidence.length}</Figure>{' '}
            {control.evidence.length === 1 ? 'ref' : 'refs'}
          </span>
        ) : null}
        <StatePill state={control.state} />
      </button>

      {open ? (
        <div id={panelId} className="min-w-0 space-y-3 px-1 pb-4 pl-8">
          <p className="max-w-prose text-pretty text-sm leading-relaxed text-foreground">
            {control.summary}
          </p>
          {control.gap ? (
            /* The gap was a coloured left border — the accent-stripe pattern, and a hue
               carrying the state on its own. DESIGN.md §4 requires a glyph, a word and a
               hue, so the state moves into a `Badge` and the border goes. */
            <p className={cn('max-w-prose text-pretty text-sm leading-relaxed', meta.ink)}>
              <Badge
                tone={stateOf(control.state) === 'partial' ? 'risk' : 'neutral'}
                className="mr-2 align-[0.05em]"
              >
                Missing
              </Badge>
              {control.gap}
            </p>
          ) : null}
          {control.evidence.length > 0 ? (
            <div className="min-w-0 rounded-lg border border-border bg-surface-2/40 p-3">
              <p className="eyebrow mb-1">evidence</p>
              <ul className="min-w-0 divide-y divide-border">
                {control.evidence.map((evidence) => (
                  <EvidenceRow key={`${evidence.kind}-${evidence.ref}`} evidence={evidence} />
                ))}
              </ul>
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              No reference — this cell states an absence, and there is nothing to open.
            </p>
          )}
        </div>
      ) : null}
    </li>
  )
}

/** One framework in the picker: name, edition, its own strip, its control count. */
function FrameworkButton({
  framework,
  selected,
  onSelect,
}: {
  framework: ComplianceFramework
  selected: boolean
  onSelect: () => void
}): ReactElement {
  return (
    <li>
      <button
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          'flex w-full min-w-0 flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors duration-[--dur-fast] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          selected
            ? 'border-blue-400 bg-blue-400/12'
            : 'border-border bg-surface hover:bg-surface-2/60',
        )}
      >
        <span className="flex min-w-0 items-baseline justify-between gap-2">
          <span className="min-w-0 truncate text-sm font-medium text-foreground">
            {framework.name}
          </span>
          <Figure className="shrink-0 text-muted-foreground">
            {COUNT.format(framework.coverage.total)}
          </Figure>
        </span>
        <span className="eyebrow truncate" translate="no">
          {framework.version}
        </span>
        <CoverageStrip coverage={framework.coverage} size="sm" />
      </button>
    </li>
  )
}

/**
 * Compliance readiness — nine published frameworks, four honest states, and a
 * checkable reference under every claim.
 *
 * The screen is a projection of `docs/compliance/README.md` by way of
 * `GET /compliance`; the disclaimer travels with the payload rather than being typed
 * here, so the page can never quietly become the authority for what Aegis says it
 * complies with.
 */
function ComplianceView(): ReactElement {
  // Read the live session token. `AuthProvider` restores the persisted session in an
  // effect that runs *after* this component's own on a reload, so a constant `null`
  // here would fetch with no bearer, 401 — and never retry once the token arrived.
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null

  const [data, setData] = useState<ComplianceResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selectedId, setSelectedId] = useState<string | null>(null)

  useEffect(() => {
    if (!hydrated) return
    let alive = true
    getCompliance(token)
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
              'The compliance map did not load. Check the backend is reachable, then retry.',
            ),
          )
        }
      })
    return () => {
      alive = false
    }
  }, [token, hydrated])

  const selected = useMemo(() => {
    const frameworks = data?.frameworks ?? []
    return frameworks.find((f) => f.id === selectedId) ?? frameworks[0] ?? null
  }, [data, selectedId])

  /*
    Grouped by jurisdiction, in the order the server sent — which is India first, and
    deliberately so: for a deployment in India the DPDP Act and the CERT-In Directions
    are law, and ISO 42001 and the OWASP lists are practice. A flat list of twelve names
    puts the binding obligations somewhere in the middle of the alphabet. The grouping is
    read off the payload rather than re-derived here, so the screen cannot disagree with
    the written position about which framework belongs where.
  */
  const groups = useMemo(() => {
    const order: string[] = []
    const byJurisdiction = new Map<string, ComplianceFramework[]>()
    for (const framework of data?.frameworks ?? []) {
      const key = framework.jurisdiction || 'International'
      if (!byJurisdiction.has(key)) {
        byJurisdiction.set(key, [])
        order.push(key)
      }
      byJurisdiction.get(key)?.push(framework)
    }
    return order.map((jurisdiction) => ({
      jurisdiction,
      frameworks: byJurisdiction.get(jurisdiction) ?? [],
    }))
  }, [data])

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="readiness · not certification"
        title="Compliance"
        actions={
          <InfoTip label="What this page is and is not">
            A control-by-control map from published frameworks to files, routes and tests in
            this repository. Nothing here is certified, audited or attested by anyone; every
            reference is resolved against the real artefact by a test on every run.
          </InfoTip>
        }
      />

      {/*
        The one sentence that must never be a hover. DESIGN.md §4 sends explanatory
        prose to a tooltip, and names the exception: the moment a caveat is needed is
        the wrong moment to make somebody find it. A jury reading "ISO 27001" on a
        screen and inferring a certificate is exactly that moment.
      */}
      <div className="flex min-w-0 items-start gap-2.5 rounded-lg border border-risk bg-risk/15 px-3 py-2.5">
        <ShieldAlert className="mt-0.5 size-4 shrink-0 text-risk-ink" aria-hidden />
        <p className="min-w-0 text-pretty text-sm leading-relaxed text-risk-ink">
          {data?.disclaimer ??
            'Compliance-readiness evidence, not certification. Nothing on this page has been audited by an independent party.'}
          {data ? (
            <>
              {' '}
              <Figure className="break-all">
                <span translate="no">{data.doc_ref}</span>
              </Figure>{' '}
              is the written position this page projects.
            </>
          ) : null}
        </p>
      </div>

      {error ? (
        <ErrorState error={error} />
      ) : data == null ? (
        <LoadingState rows={6} label="Reading the compliance map…" />
      ) : (
        <>
          <Card className="min-w-0">
            <CardHeader
              eyebrow="aegis.compliance · /compliance"
              title="Coverage across every mapped framework"
              actions={
                <Badge tone="neutral" className="gap-1.5">
                  <Figure>{COUNT.format(data.frameworks.length)}</Figure> frameworks ·{' '}
                  <Figure>{COUNT.format(data.coverage.total)}</Figure> controls
                </Badge>
              }
            />
            <CardBody className="flex min-w-0 flex-col gap-3 pt-0">
              <CoverageStrip coverage={data.coverage} />
              <CoverageLegend coverage={data.coverage} className="border-t border-border pt-3" />
              <Receipt
                origin="GET /compliance"
                detail="counts are derived from the control states, never authored beside them"
                className="border-t-0 pt-0"
              />
            </CardBody>
          </Card>

          {/*
            Residency sits directly under the coverage board because two India rows —
            DPDP s.16 and CERT-In Direction (iv) — are questions about where data goes,
            and neither is answerable by a sentence. It is the one claim on this page
            that re-derives itself from the deployment's own wiring on every read.
          */}
          <ResidencyPanel residency={data.residency} />

          {/*
            `grid-cols-1` is load-bearing, not decoration. Without an explicit
            template the single implicit column is an `auto` track, whose growth
            limit is max-content — and at 390px the framework names resolved that
            to 524px inside a 350px container, so the page itself scrolled
            sideways. `grid-cols-1` is `repeat(1, minmax(0, 1fr))`, which caps the
            track at the container and lets the `truncate` inside actually bite.
          */}
          <div className="grid min-w-0 grid-cols-1 items-start gap-4 xl:grid-cols-[minmax(0,20rem)_minmax(0,1fr)]">
            <Card className="min-w-0">
              <CardHeader eyebrow="frameworks · by jurisdiction" title="Pick one" />
              <CardBody className="flex min-w-0 flex-col gap-4 pt-0">
                {groups.map((group) => (
                  <div key={group.jurisdiction} className="min-w-0">
                    <p className="eyebrow mb-2">{group.jurisdiction}</p>
                    <ul className="flex min-w-0 flex-col gap-2">
                      {group.frameworks.map((framework) => (
                        <FrameworkButton
                          key={framework.id}
                          framework={framework}
                          selected={selected?.id === framework.id}
                          onSelect={() => setSelectedId(framework.id)}
                        />
                      ))}
                    </ul>
                  </div>
                ))}
              </CardBody>
            </Card>

            {selected == null ? null : (
              <DataPanel
                eyebrow={selected.version}
                title={selected.name}
                maxHeight={720}
                actions={
                  <InfoTip label={`What ${selected.name} governs here`}>{selected.scope}</InfoTip>
                }
                toolbar={
                  <div className="flex min-w-0 flex-col gap-2">
                    <CoverageStrip coverage={selected.coverage} />
                    <CoverageLegend coverage={selected.coverage} />
                  </div>
                }
                footer={
                  <Receipt
                    origin={`${selected.name} · ${selected.version}`}
                    detail="open a row for what Aegis does, what is missing, and the file, route or test behind it"
                    className="w-full border-t-0 pt-0"
                  />
                }
              >
                <ul className="min-w-0">
                  {selected.controls.map((control) => (
                    <ControlRow key={control.id} control={control} />
                  ))}
                </ul>
              </DataPanel>
            )}
          </div>
        </>
      )}
    </div>
  )
}

/** Client entry for the Compliance section — gated on a reachable backend. */
export function ComplianceMount(): ReactElement {
  return (
    <BackendGate>
      <ComplianceView />
    </BackendGate>
  )
}
