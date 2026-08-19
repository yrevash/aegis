/**
 * The database console (§7.9) — the schema, the closed set of reads, and one result.
 *
 * A faithful TypeScript mirror of `OverviewOut` / `ResultOut` in
 * `backend/src/app/api/routes_db.py`.
 *
 * **Why its own fetch rather than `client.ts`'s `request`.** That helper discards the
 * response body, and on these routes the body *is* the point: a 503 says the console is
 * switched off and names the variable that turns it on, a 400 names the column that is not
 * in the catalog, and a 429 says how long to wait. Throwing those away would turn four
 * precise refusals into "request failed", which is the defect this whole surface exists to
 * remove.
 *
 * **There is no `runSql` function here, and that is deliberate.** The console runs a closed
 * set of reads assembled by the server with the tenant filter welded in; the argument is at
 * the top of `aegis/src/aegis/dbadmin/catalogue.py`, and the server sends the one-sentence
 * version of it as `freeFormReason` so the screen never has to restate it.
 *
 * @see backend/src/app/api/routes_db.py
 * @see aegis/src/aegis/dbadmin/catalogue.py
 */

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { API_BASE } from './config'

/** One column of one table, as the console's grants report it. */
export interface DbColumn {
  name: string
  dataType: string
  nullable: boolean
  isPrimaryKey: boolean
}

/** One outgoing reference, for navigating from a row to what it points at. */
export interface DbForeignKey {
  column: string
  referencesTable: string
  referencesColumn: string
}

/** One browsable relation. */
export interface DbTable {
  name: string
  columns: DbColumn[]
  primaryKey: string[]
  foreignKeys: DbForeignKey[]
  /** `pg_class.reltuples` — the planner's estimate, never presented as a count. */
  rowEstimate: number
  tenantScoped: boolean
  /** Columns the catalog has but this connection may not read. */
  withheldColumns: string[]
}

/** One curated read the operator may run. */
export interface DbInspection {
  id: string
  title: string
  summary: string
  source: string
  tenantScoped: boolean
  /** Declared parameters, mapped to their defaults. A name not here is refused. */
  parameters: Record<string, unknown>
}

/** What the console's connection can actually do, measured server-side. */
export interface DbPosture {
  role: string
  readOnly: boolean
  isSuperuser: boolean
  bypassesRls: boolean
  writableTables: string[]
  defaultReadOnly: boolean
  statementTimeout: string
  /** The server's own sentence when the connection is not fit to serve. */
  refusal: string | null
}

/** One tenant, for the scope selector. */
export interface DbTenant {
  id: number
  name: string
}

/** Body of `GET /database/overview`. */
export interface DbOverview {
  enabled: boolean
  posture: DbPosture | null
  tables: DbTable[]
  inspections: DbInspection[]
  tenants: DbTenant[]
  scope: string
  rowLimitDefault: number
  rowLimitMax: number
  maxResultMb: number
  statementTimeoutMs: number
  /** Always `false`. Present so the screen states the product decision, not a guess. */
  freeFormSql: boolean
  /** The server's sentence explaining why there is no SQL box. */
  freeFormReason: string
}

/** One executed read: the rows, the bounds that fired, and what it ran as. */
export interface DbResult {
  label: string
  columns: string[]
  rows: unknown[][]
  rowCount: number
  truncated: boolean
  /** Empty exactly when `truncated` is false. */
  truncationReason: string
  durationMs: number
  approxBytes: number
  planCost: number
  planSummary: string
  /** The authority this read ran under, in words. */
  scope: string
  tenantFiltered: boolean
  /** The statement the server built. Shown so the tenant filter can be read, not trusted. */
  sql: string
  exactCount: number | null
  queryId: string
}

/** What a browse asks for. Every identifier is matched against the catalog server-side. */
export interface DbBrowseRequest {
  table: string
  limit?: number
  orderBy?: string
  /** Keyset cursor: the ordering column's value on the last row of the previous page. */
  after?: string
  filterColumn?: string
  filterValue?: string
  /** The tenant selector. Can only narrow; the server refuses anything that would widen. */
  tenantId?: number | null
  exactCount?: boolean
}

/** What an inspection run asks for. */
export interface DbInspectionRequest {
  limit?: number
  parameters?: Record<string, unknown>
  tenantId?: number | null
}

async function call<T>(path: string, init: RequestInit, token: string | null): Promise<T> {
  const method = init.method ?? 'GET'
  const headers = new Headers(init.headers)
  headers.set('Content-Type', 'application/json')
  const bearer = token ?? getAuthToken()
  if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
  const res = await fetch(`${API_BASE}${path}`, { ...init, headers })
  if (!res.ok) {
    const detail = await res
      .json()
      .then((body: { detail?: string }) => body.detail)
      .catch(() => undefined)
    if (res.status === 401) reportSessionExpired()
    throw new ApiError(res.status, method, path, detail)
  }
  return (await res.json()) as T
}

/** The schema, the console's own privileges, and the reads it offers — one round trip. */
export async function getDatabaseOverview(token: string | null): Promise<DbOverview> {
  return call<DbOverview>('/database/overview', { method: 'GET' }, token)
}

/** Read one table, keyset-paginated and tenant-filtered. */
export async function browseTable(
  body: DbBrowseRequest,
  token: string | null,
): Promise<DbResult> {
  return call<DbResult>('/database/browse', { method: 'POST', body: JSON.stringify(body) }, token)
}

/** Run one entry from the closed set of inspections. */
export async function runInspection(
  inspectionId: string,
  body: DbInspectionRequest,
  token: string | null,
): Promise<DbResult> {
  return call<DbResult>(
    `/database/inspections/${encodeURIComponent(inspectionId)}`,
    { method: 'POST', body: JSON.stringify(body) },
    token,
  )
}
