'use client'

import {
  AlertTriangle,
  Database,
  KeyRound,
  Link2,
  ListFilter,
  Loader2,
  Lock,
  ShieldCheck,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import { Button } from '@/components/primitives/button'
import { Input } from '@/components/primitives/input'
import { BackendGate } from '@/components/shared/BackendGate'
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

/** What the operator has selected on the left: a table to browse, or an inspection to run. */
type Selection =
  | { kind: 'table'; name: string }
  | { kind: 'inspection'; id: string }
  | { kind: 'none' }

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
    return (
      <Card>
        <CardBody className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-risk-ink" />
          <p className="text-sm text-foreground">
            The console has no connection of its own, so nothing can be read.
          </p>
        </CardBody>
      </Card>
    )
  }
  if (!posture.readOnly) {
    return (
      <Card>
        <CardBody className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" />
          <p className="text-sm text-foreground">{posture.refusal}</p>
        </CardBody>
      </Card>
    )
  }
  return (
    <Card>
      <CardHeader
        title="Read-only by privilege, not by promise"
        eyebrow="Connection"
        actions={
          <Badge tone="ok" className="gap-1">
            <ShieldCheck className="size-3" />
            verified
          </Badge>
        }
      />
      <CardBody className="pt-3">
        <dl className="grid gap-x-6 gap-y-3 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">Role</dt>
            <dd className="mt-0.5 font-mono text-[0.8rem] text-foreground">{posture.role}</dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Tables it can write
            </dt>
            <dd className="mt-0.5 text-foreground">
              {posture.writableTables.length === 0 ? 'none' : posture.writableTables.join(', ')}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Statement timeout
            </dt>
            <dd className="mt-0.5 font-mono text-[0.8rem] text-foreground">
              {posture.statementTimeout || `${overview.statementTimeoutMs} ms`}
            </dd>
          </div>
          <div>
            <dt className="text-xs uppercase tracking-wide text-muted-foreground">
              Result ceiling
            </dt>
            <dd className="mt-0.5 text-foreground">
              {overview.rowLimitMax.toLocaleString()} rows · {overview.maxResultMb} MB
            </dd>
          </div>
        </dl>
        <p className="mt-4 flex items-start gap-2 rounded-xl bg-surface-2 px-3 py-2 text-[0.8rem] leading-relaxed text-muted-foreground">
          <Lock className="mt-0.5 size-3.5 shrink-0" />
          <span>{overview.freeFormReason}</span>
        </p>
      </CardBody>
    </Card>
  )
}

/**
 * The tenant selector — the control worth more than a SQL box.
 *
 * Binding a tenant and re-running the same read is the most convincing thirty seconds of
 * the isolation story there is: the query text does not change, the row count does. The
 * server refuses any selection that would widen a caller's authority, so this is a
 * narrowing control and it says so.
 */
function ScopePicker({
  overview,
  tenantId,
  onChange,
  disabled,
}: {
  overview: DbOverview
  tenantId: number | null
  onChange: (id: number | null) => void
  disabled: boolean
}): ReactElement {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className="text-xs uppercase tracking-wide text-muted-foreground">Read as</span>
      <select
        value={tenantId === null ? '' : String(tenantId)}
        onChange={(event) => onChange(event.target.value === '' ? null : Number(event.target.value))}
        disabled={disabled}
        aria-label="Tenant scope for every read on this page"
        className="h-9 rounded-lg border border-input bg-surface px-3 text-sm text-foreground outline-none transition-colors focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50"
      >
        <option value="">Every tenant</option>
        {overview.tenants.map((tenant) => (
          <option key={tenant.id} value={tenant.id}>
            {tenant.name} (#{tenant.id})
          </option>
        ))}
      </select>
      <span className="text-[0.72rem] text-muted-foreground">
        Narrows every read on this page. It cannot widen one.
      </span>
    </div>
  )
}

/** The left rail: what there is to look at, split by whether the scope selector moves it. */
function Catalog({
  overview,
  selection,
  onSelect,
}: {
  overview: DbOverview
  selection: Selection
  onSelect: (next: Selection) => void
}): ReactElement {
  const { scoped, platform } = useMemo(() => grouped(overview.tables), [overview.tables])

  const tableButton = (table: DbTable): ReactElement => {
    const active = selection.kind === 'table' && selection.name === table.name
    return (
      <li key={table.name}>
        <button
          type="button"
          onClick={() => onSelect({ kind: 'table', name: table.name })}
          aria-current={active ? 'true' : undefined}
          className={`flex w-full items-center justify-between gap-2 rounded-lg px-2.5 py-1.5 text-left text-sm transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/40 ${
            active ? 'bg-surface-2 text-foreground' : 'text-muted-foreground hover:bg-surface-2/60'
          }`}
        >
          <span className="truncate font-mono text-[0.78rem]">{table.name}</span>
          <span className="shrink-0 text-[0.68rem] text-muted-foreground/80">
            {estimate(table.rowEstimate)}
          </span>
        </button>
      </li>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader title="Tenant tables" eyebrow={`${scoped.length} relations`} />
        <CardBody className="pt-2">
          <p className="mb-2 text-[0.72rem] leading-snug text-muted-foreground">
            Every row here is filtered to the scope above by a clause the server writes
            into the query.
          </p>
          <ul className="space-y-0.5">{scoped.map(tableButton)}</ul>
        </CardBody>
      </Card>
      <Card>
        <CardHeader title="Platform tables" eyebrow={`${platform.length} relations`} />
        <CardBody className="pt-2">
          <p className="mb-2 text-[0.72rem] leading-snug text-muted-foreground">
            No tenant column, so the scope selector does not change what these show.
          </p>
          <ul className="space-y-0.5">{platform.map(tableButton)}</ul>
        </CardBody>
      </Card>
    </div>
  )
}

/** The saved questions, each one a parameterised read the server assembles. */
function Inspections({
  overview,
  selection,
  parameters,
  onParameter,
  onRun,
  running,
}: {
  overview: DbOverview
  selection: Selection
  parameters: Record<string, string>
  onParameter: (key: string, value: string) => void
  onRun: (inspection: DbInspection) => void
  running: boolean
}): ReactElement {
  return (
    <Card>
      <CardHeader title="Saved questions" eyebrow={`${overview.inspections.length} reads`} />
      <CardBody className="space-y-3 pt-3">
        {overview.inspections.map((inspection) => {
          const active = selection.kind === 'inspection' && selection.id === inspection.id
          const keys = Object.keys(inspection.parameters)
          return (
            <div
              key={inspection.id}
              className={`rounded-xl border px-3 py-3 transition-colors ${
                active ? 'border-border bg-surface-2' : 'border-border/60'
              }`}
            >
              <p className="text-sm font-medium text-foreground">{inspection.title}</p>
              <p className="mt-1 text-[0.75rem] leading-snug text-muted-foreground">
                {inspection.summary}
              </p>
              <div className="mt-2 flex flex-wrap items-end gap-2">
                {keys.map((key) => (
                  <label key={key} className="flex flex-col gap-1">
                    <span className="text-[0.68rem] uppercase tracking-wide text-muted-foreground">
                      {key}
                    </span>
                    <Input
                      className="h-8 w-32 text-[0.78rem]"
                      value={parameters[`${inspection.id}.${key}`] ?? String(inspection.parameters[key] ?? '')}
                      onChange={(event) => onParameter(`${inspection.id}.${key}`, event.target.value)}
                    />
                  </label>
                ))}
                <Button
                  type="button"
                  size="sm"
                  variant={active ? 'default' : 'outline'}
                  disabled={running}
                  onClick={() => onRun(inspection)}
                >
                  {running && active ? <Loader2 className="size-3.5 animate-spin" /> : null}
                  Run
                </Button>
              </div>
            </div>
          )
        })}
      </CardBody>
    </Card>
  )
}

/** The columns of the selected table, including the ones this connection may not read. */
function Structure({ table }: { table: DbTable }): ReactElement {
  return (
    <Card>
      <CardHeader title={table.name} eyebrow="Structure" />
      <CardBody className="pt-3">
        <div className="w-full overflow-x-auto">
          <ul className="flex min-w-max flex-wrap gap-1.5">
            {table.columns.map((column) => (
              <li
                key={column.name}
                className="flex items-center gap-1.5 rounded-lg border border-border/60 bg-surface-2/50 px-2 py-1"
              >
                {column.isPrimaryKey ? (
                  <KeyRound className="size-3 text-muted-foreground" aria-label="primary key" />
                ) : null}
                <span className="font-mono text-[0.74rem] text-foreground">{column.name}</span>
                <span className="text-[0.68rem] text-muted-foreground">{column.dataType}</span>
              </li>
            ))}
          </ul>
        </div>
        {table.foreignKeys.length > 0 ? (
          <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem] text-muted-foreground">
            <Link2 className="size-3" />
            {table.foreignKeys.map((key) => (
              <span key={`${key.column}-${key.referencesTable}`} className="font-mono">
                {key.column} → {key.referencesTable}.{key.referencesColumn}
              </span>
            ))}
          </div>
        ) : null}
        {table.withheldColumns.length > 0 ? (
          <p className="mt-3 flex items-start gap-2 text-[0.72rem] leading-snug text-muted-foreground">
            <Lock className="mt-0.5 size-3 shrink-0" />
            <span>
              {table.withheldColumns.join(', ')} {table.withheldColumns.length === 1 ? 'is' : 'are'}{' '}
              withheld from this connection by a column grant, so {table.withheldColumns.length === 1 ? 'it is' : 'they are'} not
              in the catalogue and cannot be read, ordered by or filtered on.
            </span>
          </p>
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
    <Card>
      <CardHeader
        title={result.label}
        eyebrow={`${result.durationMs} ms · ${result.planSummary}`}
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
      />
      <CardBody className="pt-3">
        <p className="text-[0.78rem] leading-snug text-muted-foreground">{coverage(result)}</p>
        {result.rowCount === 0 ? (
          <p className="mt-4 rounded-xl border border-dashed border-border bg-surface-2/40 px-4 py-6 text-center text-sm text-muted-foreground">
            {emptyMessage(result)}
          </p>
        ) : (
          <div className="mt-3 max-h-[28rem] overflow-auto rounded-xl border border-border">
            <Table>
              <THead>
                {result.columns.map((column) => (
                  <TH key={column}>{column}</TH>
                ))}
              </THead>
              <TBody>
                {result.rows.map((row, index) => (
                  <TR key={`${result.queryId}-${index}`}>
                    {row.map((value, column) => (
                      <TD
                        key={result.columns[column] ?? column}
                        className={`whitespace-nowrap font-mono text-[0.75rem] ${
                          isAbsent(value) ? 'text-muted-foreground/60' : ''
                        }`}
                      >
                        {cell(value)}
                      </TD>
                    ))}
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
        <details className="mt-3">
          <summary className="cursor-pointer text-[0.72rem] text-muted-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring/40">
            The statement the server built
          </summary>
          <pre className="mt-2 overflow-x-auto rounded-xl bg-surface-2 px-3 py-2 font-mono text-[0.7rem] leading-relaxed text-foreground">
            {result.sql}
          </pre>
        </details>
      </CardBody>
    </Card>
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
 *   bound decided that.
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
        if (alive) setLoadError(err instanceof Error ? err.message : 'The console did not load.')
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
        setRefusal(err instanceof Error ? err.message : 'That read did not go through.')
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
        setRefusal(err instanceof Error ? err.message : 'That read did not go through.')
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
      <Card>
        <CardBody className="flex items-start gap-3">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-risk-ink" />
          <div>
            <p className="text-sm text-foreground">{loadError}</p>
            <p className="mt-1 text-[0.78rem] text-muted-foreground">
              The database console is off unless a deployment sets{' '}
              <span className="font-mono">AEGIS_DB_CONSOLE_ENABLED</span> and points{' '}
              <span className="font-mono">AEGIS_DB_CONSOLE_DSN</span> at the read-only role.
            </p>
          </div>
        </CardBody>
      </Card>
    )
  }

  if (overview === null) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Reading the schema…
      </div>
    )
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <Database className="size-4 text-muted-foreground" />
          <h2 className="t-title text-foreground">Database</h2>
        </div>
        <ScopePicker
          overview={overview}
          tenantId={tenantId}
          onChange={setTenantId}
          disabled={busy}
        />
      </div>

      <Posture overview={overview} />

      <div className="grid gap-4 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)]">
        <div className="space-y-4">
          <Catalog overview={overview} selection={selection} onSelect={(next) => {
            setSelection(next)
            if (next.kind === 'table') void browse(next.name)
          }} />
        </div>
        <div className="min-w-0 space-y-4">
          {table ? <Structure table={table} /> : null}
          {refusal !== null ? (
            <Card>
              <CardBody className="flex items-start gap-3">
                <AlertTriangle className="mt-0.5 size-4 shrink-0 text-block-ink" />
                <p className="text-sm text-foreground">{refusal}</p>
              </CardBody>
            </Card>
          ) : null}
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
            <Card>
              <CardBody className="flex flex-col items-center gap-2 py-10 text-center">
                <Table2 className="size-6 text-muted-foreground/50" />
                <p className="text-sm text-foreground">Pick a table, or run a saved question.</p>
                <p className="max-w-md text-[0.78rem] text-muted-foreground">
                  Nothing has been read yet. Every read on this page is bounded, tenant-filtered
                  and written to the audit trail before it runs.
                </p>
              </CardBody>
            </Card>
          ) : null}
          <Inspections
            overview={overview}
            selection={selection}
            parameters={parameters}
            running={busy}
            onParameter={(key, value) => setParameters((prev) => ({ ...prev, [key]: value }))}
            onRun={(inspection) => void inspect(inspection)}
          />
        </div>
      </div>

      <p className="flex items-center gap-2 text-[0.72rem] text-muted-foreground">
        <ListFilter className="size-3" />
        Every read is capped at {overview.rowLimitMax.toLocaleString()} rows and{' '}
        {overview.maxResultMb} MB, cancelled after {overview.statementTimeoutMs / 1000}s, and
        recorded in the audit trail with who ran it, what it read and how many rows came back.
      </p>
    </div>
  )
}

/** Client entry for the Database section — gated on a reachable backend. */
export function DatabaseMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <DatabaseConsole token={session?.token ?? null} />
    </BackendGate>
  )
}
