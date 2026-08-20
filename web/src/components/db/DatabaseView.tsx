'use client'

import {
  Database,
  KeyRound,
  Link2,
  Loader2,
  Lock,
  Play,
  PowerOff,
  Search,
  ShieldCheck,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useId, useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { Input } from '@/components/primitives/input'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'
import { errorSentence } from '@/lib/api/apiError'
import {
  browseTable,
  getDatabaseOverview,
  runInspection,
  type DbInspection,
  type DbOverview,
  type DbResult,
  type DbTable,
} from '@/lib/api/database'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'

import {
  cell,
  coverage,
  defaultOrder,
  emptyMessage,
  estimate,
  grouped,
  isAbsent,
  nextCursor,
} from './dbView'

/**
 * Chrome adds a scroll container's overflowing content to the **document's** own
 * scroll extent unless that container is positioned. `DataPanel`'s scroll box is
 * `position: static`, so a 200-row table inside a 30rem panel left the page
 * 10,948px tall — nine thousand of them empty — while the panel itself correctly
 * scrolled at 480px. Measured in Chrome 1440x1000: `box.style.position =
 * 'relative'` takes the document from 10,948px back to 2,232px.
 *
 * The real fix is one word in `components/ui/DataPanel.tsx`, which this lane does
 * not own; this is the same fix applied through the `className` the component
 * already exposes, targeting the scroll box by the `role="group"` it is given
 * whenever `maxHeight` is set. Remove it once the primitive carries it.
 */
const SCROLL_BOX = '[&>[data-slot=card-body]>[role=group]]:relative'

/** What the operator has selected on the left: a table to browse, or an inspection to run. */
type Selection =
  | { kind: 'table'; name: string }
  | { kind: 'inspection'; id: string }
  | { kind: 'none' }

/** The one focus treatment on this screen: the ring token, at 2px, always visible. */
const FOCUS = 'outline-none focus-visible:ring-2 focus-visible:ring-ring'

/**
 * The console is off, said as a designed state rather than a red box.
 *
 * A screen that renders an error banner and nothing else is indistinguishable from a
 * screen that is broken, and this one is neither broken nor an accident: it is a
 * deliberately dark capability that a deployment has not lit. So the off state carries
 * what the console *would* do, the two environment variables that turn it on, and the
 * reason there is a second role at all — which is the same content the live screen
 * carries, minus the data.
 */
function ConsoleOff({ detail }: { detail: string | null }): ReactElement {
  return (
    <Card>
      <CardHeader
        eyebrow="AEGIS_DB_CONSOLE_ENABLED · not set"
        title="The database console is switched off"
        actions={
          <Badge tone="neutral" className="gap-1">
            <PowerOff className="size-3" aria-hidden />
            off
          </Badge>
        }
      />
      <CardBody className="flex flex-col gap-4">
        <div className="grid gap-3 md:grid-cols-2">
          <EnvVar
            name="AEGIS_DB_CONSOLE_ENABLED"
            value="1"
            what="Lights the console. Without it the routes answer, and answer that they are off."
          />
          <EnvVar
            name="AEGIS_DB_CONSOLE_DSN"
            value="postgresql://aegis_readonly:…@host/db"
            what="A second connection, to a role holding SELECT and nothing else. The application's own DSN is deliberately not reused — a console that shared it would be one bug away from a write."
          />
        </div>

        <div className="rounded-lg border border-border bg-surface-2/60 p-4">
          <p className="eyebrow mb-2">what it turns on</p>
          <ul className="grid gap-1.5 text-sm text-muted-foreground sm:grid-cols-2">
            {[
              'Every table in the schema, with its columns, keys and row estimate.',
              'A closed set of parameterised reads — no typed SQL, ever.',
              'A tenant selector that narrows every read and can never widen one.',
              'The statement the server built, printed beside its own result.',
            ].map((line) => (
              <li key={line} className="flex items-start gap-2">
                <ShieldCheck className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" aria-hidden />
                {line}
              </li>
            ))}
          </ul>
        </div>

        {detail ? (
          <p className="rounded-lg border border-border bg-surface-2 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground">The server said: </span>
            {detail}
          </p>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** One environment variable, named exactly, with what setting it does. */
function EnvVar({
  name,
  value,
  what,
}: {
  name: string
  value: string
  what: string
}): ReactElement {
  return (
    <div className="rounded-lg border border-border p-3">
      <p className="flex items-center gap-1.5">
        <Figure className="text-foreground">{name}</Figure>
        <InfoTip label={`What ${name} does`}>{what}</InfoTip>
      </p>
      <p className="mt-1 truncate font-mono text-xs text-muted-foreground">= {value}</p>
    </div>
  )
}

/**
 * The console's own connection, stated rather than assumed.
 *
 * The page's central claim is "this cannot write", and the server verifies it over the
 * very connection the queries run on before every request. Showing the measurement is
 * what makes the claim checkable by the person relying on it — and when it fails, the
 * server's sentence is the whole of the error state.
 */
function Posture({ overview }: { overview: DbOverview }): ReactElement {
  const posture = overview.posture
  if (!posture) {
    return <ConsoleOff detail="The console has no connection of its own, so nothing can be read." />
  }
  if (!posture.readOnly) {
    return <ErrorState error={posture.refusal ?? 'This connection is not read-only.'} />
  }
  return (
    <div className="grid grid-cols-2 gap-px overflow-hidden rounded-lg border border-border bg-border lg:grid-cols-4">
      <Fact
        label="Role"
        value={posture.role}
        tip="Re-verified over this very connection before every request, not trusted from configuration."
        badge={
          <Badge tone="ok" className="gap-1">
            <ShieldCheck className="size-3" aria-hidden />
            read-only
          </Badge>
        }
      />
      <Fact
        label="Tables it can write"
        value={posture.writableTables.length === 0 ? 'none' : posture.writableTables.join(', ')}
        tip="Measured, not declared. A non-empty list here is a refusal, and the console stops."
      />
      <Fact
        label="Statement timeout"
        value={posture.statementTimeout || `${overview.statementTimeoutMs} ms`}
        tip="A read that outruns this is cancelled by the database, not by the browser."
      />
      <Fact
        label="Result ceiling"
        value={`${overview.rowLimitMax.toLocaleString()} rows · ${overview.maxResultMb} MB`}
        tip={overview.freeFormReason}
      />
    </div>
  )
}

/** One measured fact about the connection. Label above, figure below, prose in the tip. */
function Fact({
  label,
  value,
  tip,
  badge,
}: {
  label: string
  value: string
  tip: string
  badge?: ReactElement
}): ReactElement {
  return (
    <div className="bg-card p-3.5">
      <p className="flex items-center gap-1 text-xs text-muted-foreground">
        {label}
        <InfoTip label={`About ${label}`}>{tip}</InfoTip>
      </p>
      <p className="mt-1 flex flex-wrap items-center gap-2">
        <Figure className="text-foreground">{value}</Figure>
        {badge}
      </p>
    </div>
  )
}

/**
 * The schema browser — forty-three relations, filterable, with their size legible.
 *
 * The split between tenant-scoped and platform tables is the point of the rail. A
 * tenant-scoped table is one the scope selector changes the contents of; a platform table
 * is one it does not, and reading them as the same kind of thing is how somebody concludes
 * the filter is not working.
 *
 * Each row carries a bar for its row estimate, scaled against the largest table in the
 * schema. Forty-three names in a column are forty-three equal-weight things; the bar is
 * what makes `usage_ledger` at sixteen thousand rows look different from a table with
 * none, which is the first question anybody actually asks of a schema.
 */
function Catalog({
  overview,
  selection,
  onSelect,
}: {
  overview: DbOverview
  selection: Selection
  onSelect: (next: Selection) => void
}): ReactElement {
  const [filter, setFilter] = useState('')
  const id = useId()
  const needle = filter.trim().toLowerCase()

  const matching = useMemo(
    () =>
      needle === ''
        ? overview.tables
        : overview.tables.filter((table) => table.name.toLowerCase().includes(needle)),
    [overview.tables, needle],
  )
  const { scoped, platform } = useMemo(() => grouped(matching), [matching])
  const peak = useMemo(
    () => Math.max(1, ...overview.tables.map((table) => table.rowEstimate)),
    [overview.tables],
  )

  const tableButton = (table: DbTable): ReactElement => {
    const active = selection.kind === 'table' && selection.name === table.name
    const share = table.rowEstimate <= 0 ? 0 : Math.max(2, (table.rowEstimate / peak) * 100)
    return (
      <li key={table.name}>
        <button
          type="button"
          onClick={() => onSelect({ kind: 'table', name: table.name })}
          aria-current={active ? 'true' : undefined}
          title={estimate(table.rowEstimate)}
          className={cn(
            'flex w-full touch-manipulation flex-col gap-1 rounded-md px-2 py-1.5 text-left transition-colors duration-[--dur-fast]',
            FOCUS,
            active ? 'bg-blue-50' : 'hover:bg-surface-2/70',
          )}
        >
          <span className="flex w-full items-baseline justify-between gap-2">
            <Figure
              className={cn(
                'min-w-0 truncate text-xs',
                active ? 'font-medium text-foreground' : 'text-muted-foreground',
              )}
            >
              {table.name}
            </Figure>
            <Figure className="shrink-0 text-[0.65rem] text-muted-foreground/80">
              {table.rowEstimate > 0 ? table.rowEstimate.toLocaleString() : '0'}
            </Figure>
          </span>
          <span aria-hidden className="h-1 w-full overflow-hidden rounded-full bg-surface-2">
            <span
              className={cn('block h-full', active ? 'bg-blue-600' : 'bg-blue-400')}
              style={{ width: `${share}%` }}
            />
          </span>
        </button>
      </li>
    )
  }

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader
        eyebrow="information_schema"
        title="Schema"
        actions={
          <span className="text-xs text-muted-foreground">
            <Figure className="text-foreground">{overview.tables.length}</Figure> relations
          </span>
        }
      />
      <CardBody className="flex min-h-0 flex-col gap-3 pt-0">
        <div className="relative">
          <label htmlFor={`${id}-filter`} className="sr-only">
            Filter tables
          </label>
          <Search
            aria-hidden
            className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-muted-foreground/60"
          />
          <Input
            id={`${id}-filter`}
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            placeholder="Filter relations…"
            autoComplete="off"
            spellCheck={false}
            className="h-8 pl-8 text-xs"
          />
        </div>

        <div className="-mx-2 max-h-[32rem] min-h-0 overflow-y-auto px-2">
          <Group
            title="Tenant tables"
            count={scoped.length}
            tip="Every row is filtered to the scope above by a clause the server writes into the query."
          >
            {scoped.map(tableButton)}
          </Group>
          <Group
            title="Platform tables"
            count={platform.length}
            tip="No tenant column, so the scope selector does not change what these show."
            className="mt-3"
          >
            {platform.map(tableButton)}
          </Group>
          {matching.length === 0 ? (
            <p className="px-2 py-4 text-xs text-muted-foreground italic">
              No relation matches “{filter.trim()}”.
            </p>
          ) : null}
        </div>
      </CardBody>
    </Card>
  )
}

/** One half of the schema rail, with the sentence that distinguishes it in a tip. */
function Group({
  title,
  count,
  tip,
  className,
  children,
}: {
  title: string
  count: number
  tip: string
  className?: string
  children: ReactElement[]
}): ReactElement {
  return (
    <div className={className}>
      <p className="eyebrow mb-1 flex items-center gap-1 px-2">
        {title}
        <Figure className="text-[0.65rem]">{count}</Figure>
        <InfoTip label={`About ${title}`}>{tip}</InfoTip>
      </p>
      <ul className="space-y-0.5">{children}</ul>
    </div>
  )
}

/**
 * The saved questions, each one a parameterised read the server assembles.
 *
 * Eight of them, each previously a card with a title, a summary paragraph, its parameter
 * fields and its own button — most of the right-hand column, permanently, for a control
 * used once. They are chips now: the summary is the tip, and only the selected one opens
 * its parameters.
 */
function Inspections({
  overview,
  selection,
  parameters,
  onParameter,
  onRun,
  onSelect,
  running,
}: {
  overview: DbOverview
  selection: Selection
  parameters: Record<string, string>
  onParameter: (key: string, value: string) => void
  onRun: (inspection: DbInspection) => void
  onSelect: (id: string) => void
  running: boolean
}): ReactElement {
  const id = useId()
  const active =
    selection.kind === 'inspection'
      ? (overview.inspections.find((entry) => entry.id === selection.id) ?? null)
      : null
  const keys = active ? Object.keys(active.parameters) : []

  return (
    <Card>
      <CardHeader
        eyebrow="parameterised · assembled by the server"
        title="Saved questions"
        actions={
          <InfoTip label="Why there is no SQL box">{overview.freeFormReason}</InfoTip>
        }
      />
      <CardBody className="flex flex-col gap-3 pt-0">
        <div className="flex flex-wrap gap-1.5">
          {overview.inspections.map((inspection) => {
            const on = active?.id === inspection.id
            return (
              <button
                key={inspection.id}
                type="button"
                aria-pressed={on}
                onClick={() => onSelect(inspection.id)}
                className={cn(
                  'inline-flex h-8 touch-manipulation items-center gap-1.5 rounded-full border px-3 text-xs font-medium transition-colors duration-[--dur-fast]',
                  FOCUS,
                  on
                    ? 'border-blue-600 bg-blue-50 text-blue-700'
                    : 'border-border bg-card text-muted-foreground hover:bg-surface-2',
                )}
              >
                {inspection.title}
              </button>
            )
          })}
        </div>

        {active ? (
          <div className="flex flex-wrap items-end gap-2 border-t border-border pt-3">
            <p className="mr-auto max-w-md text-xs leading-snug text-muted-foreground">
              {active.summary}
            </p>
            {keys.map((key) => (
              <label
                key={key}
                htmlFor={`${id}-${active.id}-${key}`}
                className="flex flex-col gap-1"
              >
                <span className="eyebrow mb-0">{key}</span>
                <Input
                  id={`${id}-${active.id}-${key}`}
                  className="tabular h-8 w-28 rounded-lg font-mono text-xs"
                  value={
                    parameters[`${active.id}.${key}`] ?? String(active.parameters[key] ?? '')
                  }
                  onChange={(event) => onParameter(`${active.id}.${key}`, event.target.value)}
                />
              </label>
            ))}
            <Button type="button" size="sm" disabled={running} onClick={() => onRun(active)}>
              {running ? (
                <Loader2
                  aria-hidden
                  className="mr-1 size-3.5 animate-spin motion-reduce:animate-none"
                />
              ) : (
                <Play aria-hidden className="mr-1 size-3.5" />
              )}
              Run
            </Button>
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** The columns of the selected table, including the ones this connection may not read. */
function Structure({ table }: { table: DbTable }): ReactElement {
  return (
    <Card>
      <CardHeader
        as="h3"
        eyebrow={`structure · ${table.columns.length} columns · ${estimate(table.rowEstimate)}`}
        title={table.name}
        actions={
          <span className="flex flex-wrap items-center gap-1.5">
            <Badge tone={table.tenantScoped ? 'graph' : 'neutral'}>
              {table.tenantScoped ? 'tenant-scoped' : 'platform'}
            </Badge>
            {table.withheldColumns.length > 0 ? (
              <Badge tone="risk" className="gap-1">
                <Lock className="size-3" aria-hidden />
                {table.withheldColumns.length} withheld
                <InfoTip label="What withheld means">
                  {table.withheldColumns.join(', ')}{' '}
                  {table.withheldColumns.length === 1 ? 'is' : 'are'} withheld from this
                  connection by a column grant, so{' '}
                  {table.withheldColumns.length === 1 ? 'it is' : 'they are'} not in the
                  catalogue and cannot be read, ordered by or filtered on.
                </InfoTip>
              </Badge>
            ) : null}
          </span>
        }
      />
      <CardBody className="flex flex-col gap-2 pt-0">
        <ul className="flex flex-wrap gap-1.5">
          {table.columns.map((column) => (
            <li
              key={column.name}
              className="flex items-center gap-1.5 rounded-md border border-border bg-surface-2/60 px-2 py-0.5"
            >
              {column.isPrimaryKey ? (
                <KeyRound className="size-3 text-blue-700" aria-label="primary key" />
              ) : null}
              <Figure className="text-xs text-foreground">{column.name}</Figure>
              <span className="text-[0.65rem] text-muted-foreground">{column.dataType}</span>
            </li>
          ))}
        </ul>
        {table.foreignKeys.length > 0 ? (
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.7rem] text-muted-foreground">
            <Link2 className="size-3" aria-hidden />
            {table.foreignKeys.map((key) => (
              <Figure key={`${key.column}-${key.referencesTable}`} className="text-[0.7rem]">
                {`${key.column} → ${key.referencesTable}.${key.referencesColumn}`}
              </Figure>
            ))}
          </div>
        ) : null}
      </CardBody>
    </Card>
  )
}

/** One executed read, with the statement that produced it and the bounds that fired. */
function Result({
  result,
  onNextPage,
  onCount,
  busy,
  canPage,
}: {
  result: DbResult
  onNextPage: () => void
  onCount: () => void
  busy: boolean
  canPage: boolean
}): ReactElement {
  return (
    <DataPanel
      as="h3"
      eyebrow={`${result.durationMs} ms · ${result.planSummary}`}
      title={result.label}
      maxHeight={result.rowCount === 0 ? undefined : '28rem'}
      className={SCROLL_BOX}
      actions={
        <div className="flex items-center gap-2">
          {canPage ? (
            <Button type="button" size="sm" variant="outline" disabled={busy} onClick={onNextPage}>
              Next page
            </Button>
          ) : null}
          {result.exactCount === null ? (
            <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onCount}>
              Count exactly
            </Button>
          ) : null}
        </div>
      }
      toolbar={
        <>
          <Badge tone={result.truncated ? 'risk' : 'ok'}>
            {result.truncated ? 'truncated' : `${result.rowCount.toLocaleString()} rows, complete`}
          </Badge>
          <Badge tone={result.tenantFiltered ? 'graph' : 'neutral'}>
            {result.tenantFiltered ? `scoped to ${result.scope}` : `read as ${result.scope}`}
          </Badge>
          {result.exactCount !== null ? (
            <Badge tone="neutral">{result.exactCount.toLocaleString()} match in total</Badge>
          ) : null}
          {/* What this read did not show, and which bound decided that. Never silent —
              compressed into the chips above with the full sentence one hover away. */}
          <InfoTip label="What this read did not show">{coverage(result)}</InfoTip>
        </>
      }
      footer={
        <>
          <details className="min-w-0 flex-1">
            <summary
              className={cn('cursor-pointer rounded-md text-xs text-muted-foreground', FOCUS)}
            >
              The statement the server built
            </summary>
            <pre className="tabular mt-2 overflow-x-auto rounded-lg bg-surface-2 px-3 py-2 font-mono text-[0.7rem] leading-relaxed text-foreground">
              {result.sql}
            </pre>
          </details>
          <Receipt
            origin={`${result.queryId} · ${result.planSummary}`}
            detail={`${result.durationMs} ms · ${result.scope}`}
            variant="inline"
          />
        </>
      }
    >
      {result.rowCount === 0 ? (
        <EmptyState icon={Table2} title="No rows came back" body={emptyMessage(result)} />
      ) : (
        <Table>
          <THead>
            {result.columns.map((column) => (
              <TH key={column} className="whitespace-nowrap">
                {column}
              </TH>
            ))}
          </THead>
          <TBody>
            {result.rows.map((row, index) => (
              <TR key={`${result.queryId}-${index}`}>
                {row.map((value, column) => (
                  <TD
                    key={result.columns[column] ?? column}
                    className={cn(
                      'max-w-[24rem] truncate whitespace-nowrap font-mono text-[0.72rem]',
                      isAbsent(value) && 'text-muted-foreground/60 italic',
                    )}
                  >
                    {cell(value)}
                  </TD>
                ))}
              </TR>
            ))}
          </TBody>
        </Table>
      )}
    </DataPanel>
  )
}

/**
 * Aegis DB console (§7.9) — the data layer, inside the product.
 *
 * The requirement behind this page is *"view full db, not go into code or db checking"*,
 * and the naive way to satisfy it — a text box wired to the application's connection — is
 * unsafe in five measured ways. So the page is a projection of a hardened path rather than
 * a query tool: the server owns the connection (a role that holds `SELECT` and nothing
 * else, re-verified before every request), owns the statement (assembled from identifiers
 * matched against the catalog, with a tenant filter welded into the `WHERE`), and owns the
 * bounds (row cap, byte cap, statement timeout, plan-cost ceiling), and every read is
 * audited on both sides of its execution.
 *
 * Three things on this screen are deliberate and worth not undoing:
 *
 * - **The scope selector, not a SQL box.** Binding a tenant and re-running the same read
 *   is the isolation story in thirty seconds. A box would be the one place in the product
 *   where that story depended on the operator's SQL being right.
 * - **The statement is shown.** An operator who cannot see the query cannot check the
 *   answer, and this page's whole claim is about what is in the `WHERE`.
 * - **Nothing is truncated silently.** Every result states what it did not show and which
 *   bound decided that — as chips now rather than a paragraph, with the full sentence a
 *   hover away.
 */
function DatabaseConsole({ token }: { token: string | null }): ReactElement {
  const [overview, setOverview] = useState<DbOverview | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [tenantId, setTenantId] = useState<number | null>(null)
  const [selection, setSelection] = useState<Selection>({ kind: 'none' })
  const [result, setResult] = useState<DbResult | null>(null)
  const [refusal, setRefusal] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [parameters, setParameters] = useState<Record<string, string>>({})

  useEffect(() => {
    let alive = true
    getDatabaseOverview(token)
      .then((data) => {
        if (alive) {
          setOverview(data)
          setLoadError(null)
        }
      })
      .catch((err: unknown) => {
        if (alive) {
          setLoadError(
            errorSentence(err, 'The database console did not load. Check the backend is up.'),
          )
        }
      })
    return () => {
      alive = false
    }
  }, [token])

  const table = useMemo(
    () =>
      selection.kind === 'table'
        ? (overview?.tables.find((entry) => entry.name === selection.name) ?? null)
        : null,
    [overview, selection],
  )

  const browse = useCallback(
    async (name: string, options: { after?: string; exactCount?: boolean } = {}) => {
      const target = overview?.tables.find((entry) => entry.name === name)
      setBusy(true)
      setRefusal(null)
      try {
        const next = await browseTable(
          {
            table: name,
            tenantId,
            after: options.after,
            exactCount: options.exactCount ?? false,
            orderBy: target ? defaultOrder(target) : undefined,
          },
          token,
        )
        setResult(next)
      } catch (err: unknown) {
        setResult(null)
        setRefusal(errorSentence(err, 'That read did not go through. Try it again.'))
      } finally {
        setBusy(false)
      }
    },
    [overview, tenantId, token],
  )

  const inspect = useCallback(
    async (inspection: DbInspection) => {
      setSelection({ kind: 'inspection', id: inspection.id })
      setBusy(true)
      setRefusal(null)
      const values: Record<string, unknown> = {}
      for (const key of Object.keys(inspection.parameters)) {
        const raw = parameters[`${inspection.id}.${key}`]
        const fallback = inspection.parameters[key]
        if (raw === undefined || raw === '') {
          values[key] = fallback
        } else {
          values[key] = typeof fallback === 'number' ? Number(raw) : raw
        }
      }
      try {
        setResult(await runInspection(inspection.id, { parameters: values, tenantId }, token))
      } catch (err: unknown) {
        setResult(null)
        setRefusal(errorSentence(err, 'That read did not go through. Try it again.'))
      } finally {
        setBusy(false)
      }
    },
    [parameters, tenantId, token],
  )

  // A scope change re-runs whatever is on screen. That is the demonstration: same read,
  // different authority, and the row count is what moves.
  useEffect(() => {
    if (selection.kind === 'table') void browse(selection.name)
    // Re-running an inspection needs its parameters, which the run handler already holds;
    // a scope change therefore clears the result rather than replaying it with stale input.
    if (selection.kind === 'inspection') setResult(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tenantId])

  if (loadError !== null) {
    return (
      <div className="space-y-4">
        <PageHeader eyebrow="read-only · parameterised" title="Database" />
        <ConsoleOff detail={loadError} />
      </div>
    )
  }

  if (overview === null) {
    return <LoadingState rows={6} label="Reading the schema…" />
  }

  if (!overview.enabled) {
    return (
      <div className="space-y-4">
        <PageHeader eyebrow="read-only · parameterised" title="Database" />
        <ConsoleOff detail={null} />
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <PageHeader
        eyebrow="read-only · parameterised"
        title="Database"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <InfoTip label="What this console is and is not">
              A closed set of parameterised reads over a connection that holds SELECT and
              nothing else. Every read is capped at{' '}
              {overview.rowLimitMax.toLocaleString()} rows and {overview.maxResultMb} MB,
              cancelled after {overview.statementTimeoutMs / 1000} s, and recorded in the
              audit trail with who ran it, what it read and how many rows came back.
            </InfoTip>
            <label htmlFor="db-scope" className="eyebrow mb-0">
              Read as
            </label>
            <select
              id="db-scope"
              value={tenantId === null ? '' : String(tenantId)}
              onChange={(event) =>
                setTenantId(event.target.value === '' ? null : Number(event.target.value))
              }
              disabled={busy}
              className={cn(
                'h-9 rounded-lg border border-input bg-surface px-3 text-sm text-foreground transition-colors disabled:cursor-not-allowed disabled:opacity-50',
                FOCUS,
              )}
            >
              <option value="">Every tenant</option>
              {overview.tenants.map((tenant) => (
                <option key={tenant.id} value={tenant.id}>
                  {tenant.name} (#{tenant.id})
                </option>
              ))}
            </select>
            <InfoTip label="What the scope selector does">
              Narrows every read on this page, by a clause the server welds into the WHERE.
              It cannot widen one: the server refuses any selection that would exceed the
              caller&rsquo;s own authority.
            </InfoTip>
          </div>
        }
      />

      <Posture overview={overview} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,17rem)_minmax(0,1fr)]">
        <Catalog
          overview={overview}
          selection={selection}
          onSelect={(next) => {
            setSelection(next)
            if (next.kind === 'table') void browse(next.name)
          }}
        />
        <div className="flex min-w-0 flex-col gap-4">
          <Inspections
            overview={overview}
            selection={selection}
            parameters={parameters}
            running={busy}
            onParameter={(key, value) => setParameters((prev) => ({ ...prev, [key]: value }))}
            onSelect={(id) => setSelection({ kind: 'inspection', id })}
            onRun={(inspection) => void inspect(inspection)}
          />
          {table ? <Structure table={table} /> : null}
          {refusal !== null ? <ErrorState error={refusal} /> : null}
          {result !== null ? (
            <Result
              result={result}
              busy={busy}
              canPage={
                selection.kind === 'table' &&
                table !== null &&
                nextCursor(result, defaultOrder(table)) !== null
              }
              onNextPage={() => {
                if (selection.kind !== 'table' || table === null) return
                const cursor = nextCursor(result, defaultOrder(table))
                if (cursor !== null) void browse(selection.name, { after: cursor })
              }}
              onCount={() => {
                if (selection.kind === 'table') void browse(selection.name, { exactCount: true })
              }}
            />
          ) : null}
          {result === null && refusal === null ? (
            <EmptyState
              icon={Database}
              title="Nothing has been read yet"
              body="Every read on this page is bounded, tenant-filtered and written to the audit trail before it runs. Pick a relation on the left, or run one of the saved questions above."
            />
          ) : null}
        </div>
      </div>
    </div>
  )
}

/** Client entry for the Database section — gated on a reachable backend. */
export function DatabaseMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <DatabaseConsole token={session?.token ?? null} />
      </TooltipProvider>
    </BackendGate>
  )
}
