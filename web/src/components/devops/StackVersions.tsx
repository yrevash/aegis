'use client'

import { Boxes, Layers } from 'lucide-react'
import { useEffect, useMemo, useState, type ReactElement } from 'react'

import { getStack } from '@/lib/api/client'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { PipelineHealthPanel } from '@/components/health/PipelineHealthView'
import { BackendGate } from '@/components/shared/BackendGate'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'
import type { StackComponent, StackResponse } from '@/lib/api/types'

import { StackLayers } from './StackLayers'
import { groupByCategory, summarizeStack, versionLabel } from './stackDisplay'

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
 * All grouping / counting lives in the recharts-free `stackDisplay` module so it
 * can be unit-tested; this file only fetches and renders.
 */

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; data: StackResponse }

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
          <EmptyState
            icon={Boxes}
            title="No components reported"
            body="This is a live inventory of what the running process resolved. An empty one means the backend answered, and had nothing to declare."
          />
        </CardBody>
      </Card>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* The shape of the stack: three counts and the layer split, in one card. */}
      <Card>
        <CardBody className="flex flex-col gap-5">
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Stat label="Components" value={summary.total} />
            <Stat
              label="Versions resolved"
              value={`${summary.withVersion}/${summary.total}`}
              hint={summary.unknownVersion > 0 ? `${summary.unknownVersion} unresolved` : 'all resolved'}
              tone={summary.unknownVersion > 0 ? 'warn' : 'ok'}
            />
            <Stat label="Layers" value={summary.categories} />
            <Stat
              label="Modules powered"
              value={new Set(
                load.data.components.map((c) => c.aegis_module).filter(Boolean),
              ).size}
            />
          </div>
          <StackLayers groups={groups} total={summary.total} />
        </CardBody>
      </Card>

      <DataPanel
        eyebrow="SBOM · resolved pins"
        title="The resolved inventory"
        maxHeight={620}
        actions={
          <div className="flex items-center gap-2">
            <Badge tone="neutral" className="gap-1.5">
              <Layers className="size-3 shrink-0" aria-hidden />
              <Figure>{summary.total}</Figure>
            </Badge>
            <InfoTip label="What this inventory is">
              DevOps needs the real installed versions, not a hand-maintained list. Every row is
              what the running process resolved, so this answers “what exactly is in production?”
              and shows unpinned or aged components rather than hiding them.
            </InfoTip>
          </div>
        }
        footer={
          <Receipt
            label="Inventoried"
            origin={new Date(load.data.generated_at).toLocaleString()}
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
            'mt-0.5 font-mono text-[0.62rem]',
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
            <InfoTip label="How this list is built">
              Every runtime, library and service the agent runs on, at the version this process
              actually resolved. A component whose version cannot be determined is shown as
              unresolved rather than papered over.
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
