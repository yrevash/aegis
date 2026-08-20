'use client'

import { BarChart3, LayoutDashboard, PlugZap, Table2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import {
  analyticsMessage,
  getAnalyticsBoardData,
  getAnalyticsBoards,
  getAnalyticsStatus,
  type AnalyticsBoard,
  type AnalyticsBoardData,
  type AnalyticsStatus,
} from '@/lib/api/analytics'

import {
  analyticsState,
  chartAvailable,
  chartRows,
  countedRows,
  embedAvailable,
  formatValue,
  seriesColor,
} from './analyticsBoard'
import { SupersetEmbed } from './SupersetEmbed'

/**
 * Aegis Analytics — Apache Superset, rendered inside Aegis.
 *
 * The operator never leaves this page to see a chart. There is no link to
 * `localhost:8088` anywhere in this product, and two paths put Superset's answers on
 * this screen:
 *
 * - **Aegis charts** (the default). The backend builds the Superset query, reads the
 *   rows, and these are drawn by the same components every other section uses — light
 *   theme, Aegis chrome, no iframe. This is the path that keeps working if
 *   `EMBEDDED_SUPERSET` turns out to be one of Superset 6.1.0's broken paths.
 * - **The Superset dashboard** (opt-in, per board). The embedded dashboard itself, for
 *   the boards that have one, when the deployment says the embed works.
 *
 * Every row on this page is already narrowed to the session's tenant by a `WHERE`
 * clause the backend compiled into the query from the sealed session scope. The
 * browser has no field that could move it.
 *
 * When Superset is off, unconfigured or not answering, the page says which — and names
 * the command or the variable that fixes it. It never renders an empty chart, because
 * an empty chart reads as "you have no data".
 */
function AnalyticsView(): ReactElement {
  const [status, setStatus] = useState<AnalyticsStatus | null>(null)
  const [statusLoaded, setStatusLoaded] = useState(false)
  const [boards, setBoards] = useState<AnalyticsBoard[]>([])
  const [windows, setWindows] = useState<Record<string, string>>({})
  const [selected, setSelected] = useState<string>('')
  const [window_, setWindow] = useState<string>('')
  const [data, setData] = useState<AnalyticsBoardData | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<'charts' | 'dashboard'>('charts')

  useEffect(() => {
    let alive = true
    void (async () => {
      try {
        const resolved = await getAnalyticsStatus()
        if (!alive) return
        setStatus(resolved)
        if (resolved.boards > 0) {
          const catalogue = await getAnalyticsBoards()
          if (!alive) return
          setBoards(catalogue.boards)
          setWindows(catalogue.windows)
          const first = catalogue.boards[0]
          if (first !== undefined) {
            setSelected(first.id)
            setWindow(first.window)
          }
        }
      } catch {
        if (alive) setStatus(null)
      } finally {
        if (alive) setStatusLoaded(true)
      }
    })()
    return () => {
      alive = false
    }
  }, [])

  const board = boards.find((entry) => entry.id === selected) ?? null

  const load = useCallback(async (boardId: string, chosen: string) => {
    setLoading(true)
    setDataError(null)
    try {
      setData(await getAnalyticsBoardData(boardId, chosen || null))
    } catch (err) {
      setData(null)
      setDataError(analyticsMessage(err))
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (board === null || !chartAvailable(board)) {
      setData(null)
      return
    }
    void load(board.id, window_)
  }, [board, window_, load])

  const state = analyticsState(status)

  if (!statusLoaded) {
    return (
      <p role="status" className="text-sm text-muted-foreground">
        Checking whether Superset is answering…
      </p>
    )
  }

  if (state !== 'ready' || board === null) {
    return <NotReady status={status} />
  }

  const rows = data === null ? [] : chartRows(data)
  const counted = data === null ? { drawn: 0, dropped: 0 } : countedRows(data)
  const canEmbed = embedAvailable(board, status)
  const showing = canEmbed && mode === 'dashboard'

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader
          title="Analytics"
          eyebrow="Aegis Analytics · Apache Superset"
          actions={
            <Badge tone="ok">
              Superset answering
            </Badge>
          }
        />
        <CardBody className="space-y-4 pt-0">
          <p className="text-sm text-muted-foreground">
            Read from Superset at{' '}
            <span className="font-mono text-[0.72rem]">{status?.baseUrl}</span>, and drawn
            here.{' '}
            {data?.tenantScoped === false
              ? 'This sign-in reads across every tenant.'
              : 'Every row is filtered to your tenant by a WHERE clause the browser cannot remove.'}
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {boards.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => {
                  setSelected(entry.id)
                  setWindow(entry.window)
                  setMode('charts')
                }}
                aria-pressed={entry.id === board.id}
                className={
                  entry.id === board.id
                    ? 'rounded-full border border-blue-400 bg-blue-400/12 px-3 py-1.5 text-sm text-blue-600 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400'
                    : 'rounded-full border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400'
                }
              >
                {entry.title}
              </button>
            ))}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <label className="text-sm text-muted-foreground" htmlFor="analytics-window">
              Window
            </label>
            <select
              id="analytics-window"
              value={window_}
              onChange={(event) => setWindow(event.target.value)}
              className="rounded-lg border border-border bg-card px-3 py-1.5 text-sm text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400"
            >
              {Object.entries(windows).map(([key, label]) => (
                <option key={key} value={key}>
                  {label}
                </option>
              ))}
            </select>

            {canEmbed ? (
              <div className="ml-auto flex items-center gap-1 rounded-lg border border-border p-1">
                <ModeButton
                  active={mode === 'charts'}
                  onClick={() => setMode('charts')}
                  icon={<BarChart3 className="size-4" />}
                  label="Aegis charts"
                />
                <ModeButton
                  active={mode === 'dashboard'}
                  onClick={() => setMode('dashboard')}
                  icon={<LayoutDashboard className="size-4" />}
                  label="Superset dashboard"
                />
              </div>
            ) : null}
          </div>
        </CardBody>
      </Card>

      <Card>
        <CardHeader title={board.title} eyebrow={board.summary} />
        <CardBody className="space-y-6 pt-0">
          {showing ? (
            <SupersetEmbed boardId={board.id} title={board.title} />
          ) : dataError !== null ? (
            <p role="status" className="text-sm text-muted-foreground">
              {dataError}
            </p>
          ) : loading ? (
            <p role="status" className="text-sm text-muted-foreground">
              Asking Superset for {board.title.toLowerCase()}…
            </p>
          ) : rows.length === 0 ? (
            <p role="status" className="text-sm text-muted-foreground">
              Superset ran the query and returned no rows for this window. Widen the
              window, or check the dataset behind this board has data for your tenant.
            </p>
          ) : (
            <>
              {board.series.map((series) => (
                <figure key={series} className="space-y-2">
                  <figcaption className="text-sm font-medium text-foreground">
                    {series}
                  </figcaption>
                  <BarChart
                    data={rows}
                    index="label"
                    category={series}
                    color={seriesColor(board, series)}
                    valueFormatter={formatValue}
                    height={220}
                  />
                </figure>
              ))}

              <div className="space-y-2">
                <p className="flex items-center gap-2 text-sm font-medium text-foreground">
                  <Table2 className="size-4 text-muted-foreground" />
                  The rows behind the charts
                </p>
                <Table>
                  <THead>
                    <TH>{data?.x}</TH>
                    {board.series.map((series) => (
                      <TH key={series} className="text-right">
                        {series}
                      </TH>
                    ))}
                  </THead>
                  <TBody>
                    {rows.map((row) => (
                      <TR key={row.label}>
                        <TD>{row.label}</TD>
                        {board.series.map((series) => (
                          <TD key={series} className="tabular text-right">
                            {formatValue(Number(row[series]))}
                          </TD>
                        ))}
                      </TR>
                    ))}
                  </TBody>
                </Table>
                {counted.dropped > 0 ? (
                  <p className="text-[0.72rem] text-muted-foreground">
                    {counted.dropped} row{counted.dropped === 1 ? '' : 's'} carried no{' '}
                    {data?.x} value and are not plotted.
                  </p>
                ) : null}
              </div>
            </>
          )}
        </CardBody>
      </Card>
    </div>
  )
}

/** One segment of the charts/dashboard switch. */
function ModeButton({
  active,
  onClick,
  icon,
  label,
}: {
  active: boolean
  onClick: () => void
  icon: ReactElement
  label: string
}): ReactElement {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={
        active
          ? 'flex items-center gap-2 rounded-md bg-surface-2 px-3 py-1.5 text-sm text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400'
          : 'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-400'
      }
    >
      {icon}
      {label}
    </button>
  )
}

/**
 * The honest unavailable state — an instruction, not a shrug.
 *
 * Each branch names the exact thing to change: the environment variable that turns the
 * feature on, the ones that point it at a server, the command that starts that server,
 * or the catalogue that gives it something to draw.
 */
function NotReady({ status }: { status: AnalyticsStatus | null }): ReactElement {
  const detail =
    status?.detail ??
    'Aegis could not ask its own backend whether Superset is running.'
  const action =
    status?.action ?? 'Check the Aegis backend is running, then reload this page.'

  return (
    <Card>
      <CardHeader title="Analytics" eyebrow="Aegis Analytics · Apache Superset" />
      <CardBody>
        <div
          role="status"
          className="flex min-h-[320px] flex-col items-center justify-center gap-3 rounded-2xl border border-dashed border-border bg-surface-2/40 px-6 py-10 text-center"
        >
          <PlugZap className="size-8 text-muted-foreground/50" />
          <div className="max-w-xl">
            <p className="text-sm font-medium text-foreground">{detail}</p>
            <p className="mt-2 text-sm text-muted-foreground">{action}</p>
            {status?.baseUrl ? (
              <p className="mt-3 font-mono text-[0.7rem] text-muted-foreground/80">
                {status.baseUrl}
              </p>
            ) : null}
          </div>
          <p className="max-w-xl text-sm text-muted-foreground">
            The rest of Aegis is unaffected: analytics is an optional add-on and nothing
            else on this deployment depends on it.
          </p>
        </div>
      </CardBody>
    </Card>
  )
}

/** Client entry for the Analytics section — gated on a reachable backend. */
export function AnalyticsMount(): ReactElement {
  return (
    <BackendGate>
      <AnalyticsView />
    </BackendGate>
  )
}
