'use client'

import { BarChart3, LayoutDashboard, PlugZap, Table2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { RankedBars } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { TBody, TD, TH, THead, TR, Table } from '@/components/ui/Table'
import {
  analyticsMessage,
  getAnalyticsBoardData,
  type AnalyticsBoard,
  type AnalyticsBoardData,
  type AnalyticsStatus,
} from '@/lib/api/analytics'

import {
  chartAvailable,
  chartRows,
  countedRows,
  embedAvailable,
  formatValue,
  seriesColor,
  type AnalyticsState,
} from './analyticsBoard'
import { SupersetEmbed } from './SupersetEmbed'

/**
 * The Superset half of the Analytics screen — the boards, when there are boards.
 *
 * It used to *be* the screen, which is why the screen died when Superset was off.
 * It is now one section of a page whose charts come from the usage ledger, so its
 * absence costs a section rather than the surface. Nothing about how a board is
 * drawn has changed: the same server-compiled query, the same tenant `WHERE`
 * clause, the same choice between Aegis-drawn charts and the embedded dashboard.
 */
export function SupersetBoards({
  status,
  boards,
  windows,
}: {
  status: AnalyticsStatus | null
  boards: AnalyticsBoard[]
  windows: Record<string, string>
}): ReactElement {
  const [selected, setSelected] = useState<string>(boards[0]?.id ?? '')
  const [window_, setWindow] = useState<string>(boards[0]?.window ?? '')
  const [data, setData] = useState<AnalyticsBoardData | null>(null)
  const [dataError, setDataError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [mode, setMode] = useState<'charts' | 'dashboard'>('charts')

  const board = boards.find((entry) => entry.id === selected) ?? boards[0] ?? null

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

  if (board === null) return <></>

  const rows = data === null ? [] : chartRows(data)
  const counted = data === null ? { drawn: 0, dropped: 0 } : countedRows(data)
  const canEmbed = embedAvailable(board, status)
  /*
    Which mark a board's x column deserves.

    Every board this deployment ships is *categorical* — `model`, `status` — and a
    vertical bar chart of fourteen model deployment ids draws fourteen 50-character
    labels along one axis, which overlap into an unreadable smear at any width. The
    fix is not a rotation or a truncated tick; it is the right mark. Aligned lengths
    with the label beside each bar is what DESIGN.md §2 asks for when identity comes
    from a name, and it is what `RankedBars` already draws everywhere else.

    A board whose x really is time keeps the bar chart, because the order of the
    categories is then the meaning and sorting them by magnitude would destroy it.
    Time is detected from the values rather than from the column name, so a board
    called `bucket` and a board called `day` behave the same.
  */
  const temporal =
    rows.length > 1 && rows.every((row) => !Number.isNaN(Date.parse(row.label)))
  const showing = canEmbed && mode === 'dashboard'

  return (
    <Card>
      <CardHeader
        title={board.title}
        eyebrow="Apache Superset"
        actions={<Badge tone="ok">Superset answering</Badge>}
      />
      <CardBody className="space-y-5 pt-0">
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
                  ? 'rounded-md border border-blue-600 bg-blue-50 px-3 py-1.5 text-sm font-medium text-blue-700 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
                  : 'rounded-md border border-border bg-card px-3 py-1.5 text-sm text-muted-foreground transition-colors duration-[var(--dur-fast)] hover:bg-surface-2 hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
              }
            >
              {entry.title}
            </button>
          ))}

          <label className="ml-auto text-sm text-muted-foreground" htmlFor="analytics-window">
            Window
          </label>
          <select
            id="analytics-window"
            value={window_}
            onChange={(event) => setWindow(event.target.value)}
            className="rounded-md border border-border bg-card px-3 py-1.5 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
          >
            {Object.entries(windows).map(([key, label]) => (
              <option key={key} value={key}>
                {label}
              </option>
            ))}
          </select>

          {canEmbed ? (
            <div className="flex items-center gap-1 rounded-lg border border-border p-1">
              <ModeButton
                active={mode === 'charts'}
                onClick={() => setMode('charts')}
                icon={<BarChart3 className="size-4" aria-hidden />}
                label="Aegis charts"
              />
              <ModeButton
                active={mode === 'dashboard'}
                onClick={() => setMode('dashboard')}
                icon={<LayoutDashboard className="size-4" aria-hidden />}
                label="Superset dashboard"
              />
            </div>
          ) : null}
        </div>

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
            Superset ran the query and returned no rows for this window. Widen the window,
            or check the dataset behind this board has data for your tenant.
          </p>
        ) : (
          <div className="space-y-6">
            {board.series.map((series) => (
              <figure key={series} className="space-y-2">
                <figcaption className="text-sm font-medium text-foreground">{series}</figcaption>
                {temporal ? (
                  <BarChart
                    data={rows}
                    index="label"
                    category={series}
                    color={seriesColor(board, series)}
                    valueFormatter={formatValue}
                    height={220}
                  />
                ) : (
                  <RankedBars
                    label={`${series} by ${data?.x ?? 'category'}, highest first`}
                    data={rows.map((row) => ({ name: row.label, value: Number(row[series]) }))}
                    valueFormatter={formatValue}
                    color={seriesColor(board, series)}
                    maxRows={8}
                  />
                )}
              </figure>
            ))}

            <details className="group rounded-lg border border-border">
              <summary className="flex cursor-pointer list-none items-center gap-2 px-4 py-2.5 text-sm font-medium text-foreground select-none">
                <Table2 className="size-4 text-muted-foreground" aria-hidden />
                The rows behind the charts
                <span className="tabular ml-auto font-mono text-[0.72rem] text-muted-foreground">
                  {counted.drawn} plotted
                  {counted.dropped > 0 ? ` · ${counted.dropped} without an x value` : ''}
                </span>
              </summary>
              <div className="overflow-x-auto border-t border-border">
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
                          <TD key={series} className="text-right">
                            <Figure>{formatValue(Number(row[series]))}</Figure>
                          </TD>
                        ))}
                      </TR>
                    ))}
                  </TBody>
                </Table>
              </div>
            </details>
          </div>
        )}

        <Receipt
          origin={`Apache Superset · ${status?.baseUrl || 'not reported'}`}
          detail={
            data?.tenantScoped === false
              ? 'read across every tenant'
              : 'scoped by a server-compiled WHERE clause'
          }
        />
      </CardBody>
    </Card>
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
          ? 'flex items-center gap-2 rounded-md bg-surface-2 px-3 py-1.5 text-sm font-medium text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
          : 'flex items-center gap-2 rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors duration-[var(--dur-fast)] hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none'
      }
    >
      {icon}
      {label}
    </button>
  )
}

/** What each unavailable state is called on the section's badge. */
const STATE_LABEL: Record<Exclude<AnalyticsState, 'ready'>, string> = {
  off: 'not enabled',
  unconfigured: 'not configured',
  down: 'not answering',
  empty: 'no boards yet',
}

/**
 * The Superset section when there is no Superset — a designed absence.
 *
 * DESIGN.md §5 is the rule being followed: a thing that cannot be shown is stated
 * in the slot it would occupy, with what is missing and what would fix it, and it
 * is never dressed up as either an error or a preview. So there is no greyed-out
 * chart skeleton here and no sample dashboard: a placeholder chart on an analytics
 * page is indistinguishable from a real one at a glance, and this product's whole
 * argument is that you can trust what it draws.
 *
 * What the panel does carry is the three capabilities the add-on brings that the
 * ledger charts above genuinely cannot — named so a reader knows what they are
 * choosing when they turn it on — and the backend's own sentence and command,
 * verbatim from `GET /analytics/status`, so the fix is not paraphrased.
 */
export function SupersetOffPanel({
  status,
  state,
}: {
  status: AnalyticsStatus | null
  state: Exclude<AnalyticsState, 'ready'>
}): ReactElement {
  const detail =
    status?.detail ?? 'Aegis could not ask its own backend whether Superset is running.'
  const action = status?.action ?? 'Check the Aegis backend is running, then reload this page.'

  const capabilities = [
    {
      icon: LayoutDashboard,
      title: 'Governed dashboards',
      body: 'Boards an analyst builds in Superset, embedded here under the tenant’s row-level filter.',
    },
    {
      icon: Table2,
      title: 'Datasets beyond the ledger',
      body: 'Joins across warehouse tables the Aegis API does not expose as an endpoint.',
    },
    {
      icon: BarChart3,
      title: 'Ad-hoc exploration',
      body: 'SQL Lab and saved charts, for questions nobody has built a screen for yet.',
    },
  ]

  return (
    <Card>
      <CardHeader
        title="Superset boards"
        eyebrow="optional add-on"
        actions={
          <Badge tone="neutral" className="gap-1.5">
            <PlugZap className="size-3" aria-hidden />
            {STATE_LABEL[state]}
          </Badge>
        }
      />
      <CardBody className="space-y-4 pt-0">
        <div className="rounded-lg border border-border bg-surface-2/50 px-4 py-3.5">
          <p className="text-sm font-medium text-foreground">{detail}</p>
          <p className="mt-1.5 text-sm text-muted-foreground">{action}</p>
          {status?.baseUrl ? (
            <p className="mt-2">
              <Figure className="text-[0.72rem] text-muted-foreground">{status.baseUrl}</Figure>
            </p>
          ) : null}
        </div>

        <ul className="grid gap-3 md:grid-cols-3">
          {capabilities.map(({ icon: Icon, title, body }) => (
            <li key={title} className="rounded-lg border border-border px-4 py-3.5">
              <span className="grid size-7 place-items-center rounded-md bg-blue-100/60">
                <Icon className="size-4 text-blue-800" aria-hidden />
              </span>
              <p className="mt-2.5 text-sm font-medium text-foreground">{title}</p>
              <p className="mt-1 text-[0.8rem] leading-5 text-muted-foreground">{body}</p>
            </li>
          ))}
        </ul>

        <Receipt
          origin="GET /analytics/status"
          detail="The charts above come from the usage ledger and do not depend on this."
        />
      </CardBody>
    </Card>
  )
}
