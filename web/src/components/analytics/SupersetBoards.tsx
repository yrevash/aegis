'use client'

import {
  BarChart3,
  LayoutDashboard,
  PlugZap,
  Table2,
} from 'lucide-react'
import { useCallback, useEffect, useMemo, useState, type ReactElement } from 'react'

import { BarChart } from '@/components/charts/BarChart'
import { RankedBars } from '@/components/charts/RankedBars'
import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
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
  groupByDimension,
  seriesColor,
  type AnalyticsState,
  type ChartRow,
} from './analyticsBoard'
import { SupersetEmbed } from './SupersetEmbed'

/** What one board is doing right now. Exhaustive — there is no fourth state. */
type BoardResult =
  | { state: 'loading' }
  | { state: 'ready'; data: AnalyticsBoardData }
  | { state: 'failed'; detail: string }

/**
 * The Superset half of the Analytics screen — every board this role may see, at once.
 *
 * **Why it stopped being a tab strip.** The section used to render *one* board at a
 * time behind a row of buttons, which meant a page called "analytics" showed a single
 * chart and hid the rest behind a click nobody makes during a demo. The catalogue is
 * the deployment's real BI surface — spend, runs, latency, human gates, job throughput,
 * the governance trail and the red-team record, each narrowed to the caller's tenant by
 * a `WHERE` clause the browser cannot remove — so all of it is drawn, as a gallery of
 * small multiples on one window.
 *
 * **Every card is one server-compiled query.** Nothing is derived from another card and
 * nothing is a placeholder: a board that fails renders the backend's own sentence in
 * the space its chart would occupy, and a board that returns no rows says exactly that.
 * There is no skeleton chart anywhere in this file, because a grey chart on an
 * analytics page is indistinguishable from a real one at a glance.
 *
 * **The embed is a second view of the same catalogue.** A board that declares the
 * `dashboard` kind can be opened as the Superset dashboard itself, under a short-lived
 * guest token carrying the same tenant clause; the gallery is what the section opens
 * on, because it keeps working when the iframe does not.
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
  const chartBoards = useMemo(() => boards.filter(chartAvailable), [boards])
  const dashboards = useMemo(
    () => boards.filter((board) => embedAvailable(board, status)),
    [boards, status],
  )

  const [window_, setWindow] = useState<string>(
    () => boards[0]?.window ?? Object.keys(windows)[0] ?? '',
  )
  const [embedded, setEmbedded] = useState<string | null>(null)
  const [results, setResults] = useState<Record<string, BoardResult>>({})

  const loadAll = useCallback(
    (chosen: string, alive: () => boolean) => {
      setResults(
        Object.fromEntries(
          chartBoards.map((board) => [board.id, { state: 'loading' } as BoardResult]),
        ),
      )
      for (const board of chartBoards) {
        void getAnalyticsBoardData(board.id, chosen || null)
          .then((data) => {
            if (!alive()) return
            setResults((current) => ({ ...current, [board.id]: { state: 'ready', data } }))
          })
          .catch((error: unknown) => {
            if (!alive()) return
            setResults((current) => ({
              ...current,
              [board.id]: { state: 'failed', detail: analyticsMessage(error) },
            }))
          })
      }
    },
    [chartBoards],
  )

  useEffect(() => {
    let live = true
    loadAll(window_, () => live)
    return () => {
      live = false
    }
  }, [loadAll, window_])

  const drawn = chartBoards.filter((board) => results[board.id]?.state === 'ready').length
  const embeddedBoard = boards.find((board) => board.id === embedded) ?? null
  const groups = useMemo(() => groupByDimension(chartBoards), [chartBoards])

  return (
    <Card>
      <CardHeader
        eyebrow="Apache Superset · one guest token per board, carrying this tenant’s WHERE clause"
        title={`Insight boards — ${boards.length} for your role`}
        actions={
          <span className="flex flex-wrap items-center gap-2">
            <Badge tone="ok" className="gap-1.5">
              <PlugZap className="size-3" aria-hidden />
              Superset answering
            </Badge>
            <InfoTip label="Which boards you can see">
              Each board declares the roles it is for, and the server refuses one you are not
              an audience for with the same 404 it gives for a board that does not exist — so
              the list cannot be used to enumerate what you are not allowed to open.
            </InfoTip>
          </span>
        }
      />
      <CardBody className="space-y-6 pt-0">
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-sm text-muted-foreground" htmlFor="analytics-window">
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
          <span className="text-xs text-muted-foreground">
            <Figure className="text-foreground">{drawn}</Figure> of {chartBoards.length} drawn
          </span>

          {dashboards.length > 0 ? (
            <div className="ml-auto flex flex-wrap items-center gap-1 rounded-lg border border-border p-1">
              <ModeButton
                active={embedded === null}
                onClick={() => setEmbedded(null)}
                icon={<BarChart3 className="size-4" aria-hidden />}
                label={`Aegis charts · ${chartBoards.length}`}
              />
              {dashboards.map((board) => (
                <ModeButton
                  key={board.id}
                  active={embedded === board.id}
                  onClick={() => setEmbedded(board.id)}
                  icon={<LayoutDashboard className="size-4" aria-hidden />}
                  label={board.title}
                />
              ))}
            </div>
          ) : null}
        </div>

        {embeddedBoard !== null ? (
          <div className="space-y-3">
            <SupersetEmbed boardId={embeddedBoard.id} title={embeddedBoard.title} />
            <Receipt
              origin={`embedded Superset dashboard · ${status?.baseUrl || 'not reported'}`}
              detail="drawn by Superset under a short-lived guest token whose signed RLS clause the browser cannot edit"
            />
          </div>
        ) : (
          <>
            {groups.map((group) => (
              <section key={group.dimension || 'tail'} className="space-y-3">
                <h3 className="flex flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-border pb-1.5">
                  <span className="text-sm font-semibold text-foreground">
                    {group.dimension === '' ? (
                      'One board each'
                    ) : (
                      <>
                        Broken down by <Figure className="text-blue-700">{group.dimension}</Figure>
                      </>
                    )}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {group.boards.length} {group.boards.length === 1 ? 'board' : 'boards'}, one
                    query each
                  </span>
                </h3>
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-3">
                  {group.boards.map((board) => (
                    <BoardCard
                      key={board.id}
                      board={board}
                      result={results[board.id] ?? { state: 'loading' }}
                      windowLabel={windows[window_] ?? window_}
                    />
                  ))}
                </div>
              </section>
            ))}
            <Receipt
              origin={`Apache Superset · ${status?.baseUrl || 'not reported'}`}
              detail={`${chartBoards.length} boards, each a query this server compiled — scoped by a WHERE clause no request body can move`}
            />
          </>
        )}
      </CardBody>
    </Card>
  )
}

/**
 * One board, drawn small.
 *
 * The card plots the board's **first** measure, named in its own caption, because a
 * gallery cell is not big enough to tell two series apart honestly and a second axis is
 * forbidden outright (DESIGN.md §2). Every other measure the board returned is in the
 * disclosure underneath, with the rows, so nothing is dropped — it is deferred.
 */
function BoardCard({
  board,
  result,
  windowLabel,
}: {
  board: AnalyticsBoard
  result: BoardResult
  windowLabel: string
}): ReactElement {
  const data = result.state === 'ready' ? result.data : null
  const rows: ChartRow[] = data ? chartRows(data) : []
  const counted = data ? countedRows(data) : { drawn: 0, dropped: 0 }
  const primary = board.series[0] ?? ''
  /*
    Which mark this board's x column deserves.

    Every categorical board — `model`, `status`, `action` — draws long identifiers, and
    a vertical bar chart of fourteen 50-character model ids overlaps into a smear at any
    width. Aligned lengths with the label beside each bar is the right mark when identity
    comes from a name. A board whose x really is time keeps the bar chart, because the
    order of the categories is then the meaning. Time is detected from the values rather
    than the column name, so `bucket` and `day` behave the same.
  */
  const temporal = rows.length > 1 && rows.every((row) => !Number.isNaN(Date.parse(row.label)))

  return (
    <article className="flex min-h-[19rem] min-w-0 flex-col gap-3 rounded-lg border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-2">
        <h3 className="flex min-w-0 items-center gap-1 text-sm font-semibold text-foreground">
          <span className="min-w-0 text-pretty">{board.title}</span>
          <InfoTip label={`About ${board.title}`}>{board.summary}</InfoTip>
        </h3>
        {result.state === 'ready' ? (
          <Badge tone="neutral" className="shrink-0 whitespace-nowrap">
            {counted.drawn} {counted.drawn === 1 ? 'row' : 'rows'}
          </Badge>
        ) : null}
      </header>

      {result.state === 'failed' ? (
        <p role="status" className="text-xs leading-5 text-muted-foreground">
          {result.detail}
        </p>
      ) : result.state === 'loading' ? (
        <p role="status" className="py-6 text-center text-xs text-muted-foreground">
          Asking Superset…
        </p>
      ) : rows.length === 0 ? (
        <p role="status" className="text-xs leading-5 text-muted-foreground">
          Superset ran the query and returned no rows in this window for your tenant.
        </p>
      ) : (
        <>
          <figure className="space-y-1.5">
            <figcaption className="text-[0.68rem] text-muted-foreground">
              {primary} by {data?.x ?? 'category'}
            </figcaption>
            {temporal ? (
              <BarChart
                data={rows}
                index="label"
                category={primary}
                color={seriesColor(board, primary)}
                valueFormatter={formatValue}
                height={180}
              />
            ) : (
              <RankedBars
                label={`${primary} by ${data?.x ?? 'category'}, highest first`}
                data={rows.map((row) => ({ name: row.label, value: Number(row[primary]) }))}
                valueFormatter={formatValue}
                color={seriesColor(board, primary)}
                maxRows={6}
              />
            )}
          </figure>

          <details className="group mt-auto rounded-md border border-border">
            <summary className="flex cursor-pointer list-none items-center gap-2 px-3 py-2 text-xs font-medium text-foreground select-none">
              <Table2 className="size-3.5 text-muted-foreground" aria-hidden />
              {board.series.length > 1
                ? `${board.series.length - 1} more measure${board.series.length > 2 ? 's' : ''}, and the rows`
                : 'The rows behind the chart'}
              <span className="tabular ml-auto font-mono text-[0.68rem] text-muted-foreground">
                {counted.dropped > 0 ? `${counted.dropped} without an x value` : windowLabel}
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
        </>
      )}
    </article>
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
