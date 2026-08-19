/**
 * The database console's pure helpers — everything the screen decides without React.
 *
 * Kept out of the component for the reason `redteamReport.ts` is: a cell renderer that
 * has to guess how to print a JSON column, and a "what am I not seeing" line that has to
 * combine three independent bounds, are both easy to get subtly wrong and impossible to
 * test through a rendered tree. `web/tests/db/dbView.test.mjs` exercises them directly.
 */

import type { DbResult, DbTable } from '@/lib/api/database'

/** How a value from a database row is rendered in a cell. */
export function cell(value: unknown): string {
  if (value === null || value === undefined) return '—'
  if (typeof value === 'string') return value
  if (typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value)
}

/** Whether a cell should render as muted — a NULL is absence, not a value. */
export function isAbsent(value: unknown): boolean {
  return value === null || value === undefined
}

/**
 * The sentence under a result naming what the reader is *not* seeing.
 *
 * Three bounds can each hide rows — the row cap, the byte cap and the tenant filter — and
 * a reader who does not know which one fired will read a partial answer as a complete one.
 * The server already writes the truncation sentence; this adds the scope, which is the
 * bound that is invisible precisely when it matters most.
 */
export function coverage(result: DbResult): string {
  const parts: string[] = []
  if (result.truncated) parts.push(result.truncationReason)
  else parts.push(`${result.rowCount.toLocaleString()} rows, complete.`)
  parts.push(
    result.tenantFiltered
      ? `Scoped to ${result.scope} by a filter the server wrote into the query.`
      : `Read as ${result.scope}. This table carries no tenant column.`,
  )
  if (result.exactCount !== null) {
    parts.push(`${result.exactCount.toLocaleString()} rows match in total.`)
  }
  return parts.join(' ')
}

/**
 * What an empty result means, as an instruction rather than a shrug.
 *
 * "No rows" on this page has three quite different causes and only one of them is boring,
 * so the empty state says which one it is looking at. A reader who sees "No rows" after
 * scoping to a tenant needs to know the scope is the reason.
 */
export function emptyMessage(result: DbResult): string {
  if (result.tenantFiltered) {
    return `No rows in ${result.label.toLowerCase()} for ${result.scope}. Widen the scope to every tenant, or pick another table.`
  }
  return `No rows in ${result.label.toLowerCase()} yet. This is the table's real state, not a failed read.`
}

/** The keyset cursor for the next page: the ordering column's value on the last row. */
export function nextCursor(result: DbResult, orderBy: string): string | null {
  if (!result.truncated || result.rows.length === 0) return null
  const index = result.columns.indexOf(orderBy)
  if (index < 0) return null
  const value = result.rows[result.rows.length - 1][index]
  return value === null || value === undefined ? null : String(value)
}

/** The column a table is paged on by default — its primary key's first column. */
export function defaultOrder(table: DbTable): string {
  return table.primaryKey[0] ?? table.columns[0]?.name ?? ''
}

/** A row-count estimate, labelled as an estimate because that is what it is. */
export function estimate(rows: number): string {
  if (rows <= 0) return 'no rows estimated'
  return `~${rows.toLocaleString()} rows`
}

/**
 * Tables grouped for the browser sidebar: tenant-scoped first, then platform tables.
 *
 * The split is the point of the sidebar. A tenant-scoped table is one the tenant selector
 * changes the contents of; a platform table is one it does not, and reading them as the
 * same kind of thing is how somebody concludes the filter is not working.
 */
export function grouped(tables: DbTable[]): { scoped: DbTable[]; platform: DbTable[] } {
  return {
    scoped: tables.filter((table) => table.tenantScoped),
    platform: tables.filter((table) => !table.tenantScoped),
  }
}
