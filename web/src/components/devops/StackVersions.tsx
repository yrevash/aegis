'use client'

import { Boxes, Layers } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { getStack } from '@/lib/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { RankedBars } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { SceneState } from '@/components/illustration/Scene'
import { PipelineHealthPanel } from '@/components/health/PipelineHealthView'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { StackComponent, StackResponse } from '@/lib/api/types'

import { StackLayers } from './StackLayers'
import { groupByCategory, moduleCounts, summarizeStack, versionLabel } from './stackDisplay'

/**
 * DevOps — Tech Stack & Versions (Aegis SBOM).
 *
 * The ground-truth software bill of materials: every runtime, library and
 * service the agent runs on, grouped by layer, with the Aegis module each
 * component powers. Versions are the real resolved pins — DevOps needs what is
 * actually installed, not a hand-maintained list — and a missing version is
 * shown honestly as "not installed / n-a" rather than papered over.
 *
 * **What the redesign changed.** The four per-layer tables were four hand-rolled
 * `<table>` elements inside a `overflow-x-auto` `div`, each with its own header
 * row and its own idea of column widths; at 390px the page itself scrolled
 * sideways. They are one {@link DataPanel} now — one header row, one scroll box
 * that cannot widen the page, and a layer band that separates the sections. The
 * two explanatory paragraphs became an {@link InfoTip} and a {@link Receipt}, and
 * the layer split, which was previously only inferable by counting rows, is the
 * {@link StackLayers} bar.
 *
 * **The visual pass.** `aegis_module` was read once, reduced to `new Set(...).size`
 * and discarded, so the screen could say "9 modules powered" and not which — it is
 * a {@link RankedBars} beside the layer split now, with the components that declare
 * no module counted in the receipt rather than dropped. The bespoke "No components
 * reported" box is a scene over a stated {@link Absence}, and the second of two
 * tooltips that said the same thing is gone.
 *
 * **No category donut.** `StackLayers` already draws the `category` composition as
 * an ordered bar with counts and shares in its legend, and layers are ordered —
 * runtime under backend under frontend under infra — which a donut discards. Two
 * marks of one field is the duplication this pass removes elsewhere.
 *
 * All grouping / counting lives in the recharts-free `stackDisplay` module so it
 * can be unit-tested; this file only fetches and renders.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StackResponse }

/**
 * Module-level formatters (DESIGN.md §3 / §4) — one shape for every count and
 * every timestamp on the screen, built once rather than per render.
 */
const COUNT = new Intl.NumberFormat('en-US')
const STAMP = new Intl.DateTimeFormat('en-US', {
  dateStyle: 'medium',
  timeStyle: 'short',
})

/** An ISO scalar in the one timestamp shape, or stated as unparseable. */
function stamp(iso: string): string {
  const at = new Date(iso)
  return Number.isNaN(at.getTime()) ? 'unparseable timestamp' : STAMP.format(at)
}

export function StackVersions({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })

  useEffect(() => {
    let alive = true
    setLoad({ status: 'loading' })
    getStack(token)
      .then((data) => alive && setLoad({ status: 'ready', data }))
      .catch((e: unknown) =>
        alive &&
        setLoad({ status: 'error', message: e instanceof Error ? e.message : 'Failed to load stack' }),
      )
    return () => {
      alive = false
    }
  }, [token])

  const summary = load.status === 'ready' ? summarizeStack(load.data.components) : null
  const groups = useMemo(
    () => (load.status === 'ready' ? groupByCategory(load.data.components) : []),
    [load],
  )
  const modules = useMemo(
    () =>
      load.status === 'ready'
        ? moduleCounts(load.data.components)
        : { modules: [], sharedInfra: 0 },
    [load],
  )

  if (load.status === 'loading') {
    return (
      <Card>
        <CardBody>
          <LoadingState rows={6} label="Reading the stack inventory…" />
        </CardBody>
      </Card>
    )
  }

  if (load.status === 'error') {
    return (
      <Card>
        <CardBody>
          <ErrorState error={load.message} fallback="The stack inventory could not be read." />
        </CardBody>
      </Card>
    )
  }

  if (load.data.components.length === 0 || summary == null) {
    return (
      <Card>
        <CardBody>
          {/* "A prompt's version history" is the nearest true picture of an SBOM
              with nothing in it — the shape of the thing, before the pins. */}
          <SceneState name="versions" size="md">
            <Absence
              className="text-left"
              figure="The resolved inventory"
              why="The backend answered and declared no components."
              needed="A running process that can resolve at least one distribution pin."
            />
          </SceneState>
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/*
        One panel, two panes, rather than two cards in a row.

        The counts and the layer split are a short band — a 215px card — and the
        module ranking is a 470px one. Side by side as separate cards that left a
        260px hole of canvas under the shorter one, and stretching the shorter one
        to match just moves the hole inside a card, which is the worse of the two.
        A single card with a rule between the panes has neither: the left pane's
        slack reads as the margin of a sidebar, and the ranking sets the height of
        a panel rather than of a card standing next to a gap.
      */}
      <Card className="min-w-0">
        <CardBody className="grid min-w-0 gap-6 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)] lg:gap-10">
          {/* Top-aligned, deliberately. Distributing this pane's two blocks to the
              top and bottom of the panel puts the slack *between* them, and a
              share bar floating in the middle of a card reads as a layout bug
              rather than as a pane with room left in it. */}
          <div className="flex min-w-0 flex-col gap-5">
            <div className="grid grid-cols-3 gap-4">
              <Stat label="Components" value={COUNT.format(summary.total)} />
              <Stat
                label="Versions resolved"
                value={`${COUNT.format(summary.withVersion)}/${COUNT.format(summary.total)}`}
                hint={
                  summary.unknownVersion > 0
                    ? `${COUNT.format(summary.unknownVersion)} unresolved`
                    : 'all resolved'
                }
                tone={summary.unknownVersion > 0 ? 'warn' : 'ok'}
              />
              <Stat label="Layers" value={COUNT.format(summary.categories)} />
            </div>
            <StackLayers groups={groups} total={summary.total} />
          </div>

          <section className="flex min-w-0 flex-col gap-4 border-t border-border pt-6 lg:border-t-0 lg:border-l lg:pt-0 lg:pl-10">
            <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
              <div className="min-w-0">
                <p className="eyebrow mb-1">components[].aegis_module</p>
                {/* `h2`, matching every other panel title on the page — this pane
                    is a sibling of the DataPanel below, not a child of it. */}
                <h2 className="text-base leading-6 font-semibold text-pretty text-foreground">
                  What each module stands on
                </h2>
              </div>
              <Badge tone="neutral" className="shrink-0 gap-1.5">
                <Boxes className="size-3 shrink-0" aria-hidden />
                <Figure>{COUNT.format(modules.modules.length)}</Figure>
              </Badge>
            </div>

            {modules.modules.length === 0 ? (
              <SceneState name="noChart" size="sm">
                <Absence
                  className="text-left"
                  figure="Components per module"
                  why="No inventoried component declares an Aegis module."
                  needed="An aegis_module on at least one component."
                />
              </SceneState>
            ) : (
              <RankedBars
                label="Inventoried components per Aegis module"
                data={modules.modules}
                valueFormatter={(v) => COUNT.format(v)}
                color="graph"
                maxRows={6}
              />
            )}
            <Receipt
              className="mt-auto"
              origin="components[].aegis_module"
              detail={`${COUNT.format(modules.sharedInfra)} of ${COUNT.format(summary.total)} declare none — shared infra`}
            />
          </section>
        </CardBody>
      </Card>

      <DataPanel
        eyebrow="SBOM · resolved pins"
        title="The resolved inventory"
        collapsible
        maxHeight={620}
        actions={
          <Badge tone="neutral" className="gap-1.5">
            <Layers className="size-3 shrink-0" aria-hidden />
            <Figure>{COUNT.format(summary.total)}</Figure>
          </Badge>
        }
        footer={
          <Receipt
            label="Inventoried"
            origin={stamp(load.data.generated_at)}
            detail="resolved pins from the running process, not a maintained list"
            className="w-full border-t-0 pt-0"
          />
        }
      >
        <Table className="min-w-[640px]">
          <THead>
            <TH className="text-left">Component</TH>
            <TH className="text-left">Package</TH>
            <TH className="text-left">Version</TH>
            <TH className="text-left">Powers</TH>
          </THead>
          <TBody>
            {groups.map((group) => (
              <Fragmented key={group.category} label={group.label} count={group.rows.length}>
                {group.rows.map((component) => (
                  <StackRow key={component.package} component={component} />
                ))}
              </Fragmented>
            ))}
          </TBody>
        </Table>
      </DataPanel>
    </div>
  )
}

/**
 * A layer band and the rows under it, inside one table body.
 *
 * A `<tbody>` per group would be the tidier markup, but `ui/Table` owns the
 * single `TBody`, and four separate tables is exactly what this screen was
 * before — four header rows and four column-width negotiations. A full-width
 * band row keeps one grid and one scroll box.
 */
function Fragmented({
  label,
  count,
  children,
}: {
  label: string
  count: number
  children: ReactElement[]
}): ReactElement {
  return (
    <>
      <tr className="bg-surface-2/60">
        <td colSpan={4} className="px-4 py-2">
          <span className="flex items-baseline gap-2">
            <span className="eyebrow">{label}</span>
            <Figure className="text-muted-foreground">{count}</Figure>
          </span>
        </td>
      </tr>
      {children}
    </>
  )
}

/** One component row: name · package · version badge · Aegis module. */
function StackRow({ component }: { component: StackComponent }): ReactElement {
  const version = versionLabel(component.version)
  return (
    <TR>
      <TD className="font-medium">{component.name}</TD>
      <TD>
        <Figure className="text-muted-foreground">{component.package}</Figure>
      </TD>
      <TD className="whitespace-nowrap">
        <span
          className={cn(
            'inline-block rounded border px-1.5 py-0.5 font-mono text-[0.7rem]',
            // `--blue-600` is a fill/border/ring step and measures 4.57:1 on white
            // — DESIGN.md §2 puts small blue text on `--blue-700` instead.
            version.known
              ? 'border-blue-400/40 bg-blue-400/10 text-blue-700'
              : 'border-border/70 bg-surface-2/50 text-muted-foreground italic',
          )}
        >
          {version.text}
        </span>
      </TD>
      <TD className="text-[0.8125rem]">
        {component.aegis_module ? (
          <span className="text-foreground/80">{component.aegis_module}</span>
        ) : (
          <span className="text-muted-foreground/60">shared infra</span>
        )}
      </TD>
    </TR>
  )
}

/** One compact figure in the header band. */
function Stat({
  label,
  value,
  hint,
  tone = 'neutral',
}: {
  label: string
  value: string | number
  hint?: string
  tone?: 'neutral' | 'ok' | 'warn'
}): ReactElement {
  return (
    <div className="min-w-0">
      <p className="eyebrow mb-1">{label}</p>
      <Figure size="stat" className="text-foreground">
        {value}
      </Figure>
      {hint && (
        <p
          className={cn(
            'mt-0.5 font-mono text-[0.6875rem]',
            tone === 'warn' ? 'text-risk-ink' : tone === 'ok' ? 'text-ok-ink' : 'text-muted-foreground',
          )}
        >
          {hint}
        </p>
      )}
    </div>
  )
}

/** Client entry for the Tech Stack & Versions section — gated on a reachable backend. */
export function StackMount(): ReactElement {
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
        <PageHeader
          eyebrow="SBOM"
          title="Tech stack and versions"
          actions={
            /*
              There were two tooltips on this screen — "What this inventory is" in
              the table header and this one — and both amounted to "these are the
              real installed versions, not a hand-maintained list". The other is
              deleted; this is the one sentence that survives.
            */
            <InfoTip label="How this list is built">
              Every runtime, library and service the agent runs on at the version this process
              actually resolved, with anything it could not determine shown as unresolved rather
              than papered over.
            </InfoTip>
          }
        />
        <StackVersions token={session?.token ?? null} />
        {/*
          Pipeline health lives here as well as under `jobs`, because `GET /jobs` is
          `require_admin_or_ai_team` and devops is neither — so the section that
          carries the panel is one they get a 403 on. `GET /platform/pipeline` admits
          any authenticated principal and narrows every figure to their own scope, so
          the people who actually operate the pipeline can read it from a section that
          is already theirs, rather than from a nav entry that would promise a control
          and deliver a refusal.
        */}
        <PipelineHealthPanel />
      </div>
    </BackendGate>
  )
}
