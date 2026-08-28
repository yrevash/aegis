'use client'

import { ChevronDown, FileText, RefreshCw } from 'lucide-react'
import { Fragment, useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/ui/Badge'
import { Card } from '@/components/ui/Card'
import { Figure } from '@/components/primitives/Figure'
import { EmptyState, ErrorState, LoadingState } from '@/components/primitives/States'
import { NotRecorded } from '@/components/jobs/JobsView'
import { getDocuments, type DocumentRow } from '@/lib/api/jobs'
import { cn } from '@/lib/utils'

/** The one focus treatment on this panel: the ring token, at 2px, always visible. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

interface CorpusPanelProps {
  token: string | null
  /** Bumped by the parent after an upload, so the list reloads without a page refresh. */
  reloadKey?: number
  /** Open one document's ingest log, by id. */
  onOpen?: (documentId: number) => void
}

/** Load state for the corpus fetch. */
type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: DocumentRow[] }

/** The signal colour each document status carries. */
function statusVariant(status: string): 'ok' | 'block' | 'agent' | 'neutral' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'block'
  if (status === 'running' || status === 'pending') return 'agent'
  return 'neutral'
}

/** Human-readable size, so a 4 MB PDF does not read as 4194304. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Local date from an ISO 8601 timestamp, or `null` when none was written. */
function formatDate(iso: string | null): string | null {
  if (!iso) return null
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString([], { year: 'numeric', month: 'short', day: '2-digit' })
}

/**
 * This tenant's corpus — every document it has put into the platform, newest first.
 *
 * Read from `GET /documents`, which projects the `documents` table itself rather than the
 * job queue. That distinction is the reason this panel exists beside the jobs table: a
 * document whose ingest never started owns no job row, so it appeared nowhere at all
 * until this listing did. "Show me what you have ingested for this tenant" is now a
 * question with a screen behind it rather than one that needed a document id nobody had.
 *
 * Empty is an honest answer and is rendered as one. Nothing here is simulated.
 */
/**
 * The six facts a row carries beyond its name and its status.
 *
 * They are not columns any more. Eight columns of small grey type is the density this
 * screen was criticised for: a reader scanning for *which document is stuck* has to
 * read past stage, pages, chunks, size, date and workflow id on every row to find the
 * one word that answers it. The facts are still every bit as true and none is dropped
 * — they are revealed for the row being looked at instead of for all of them at once.
 */
function rowFacts(row: DocumentRow): { label: string; value: ReactElement }[] {
  return [
    {
      label: 'stage',
      value: row.completed_stage ? (
        <Figure>{row.completed_stage}</Figure>
      ) : (
        <NotRecorded what="not started" />
      ),
    },
    {
      label: 'pages',
      value:
        row.page_count === null ? <NotRecorded what="not parsed" /> : <Figure>{row.page_count}</Figure>,
    },
    {
      label: 'chunks',
      value:
        row.chunk_count === null ? (
          <NotRecorded what="not chunked" />
        ) : (
          <Figure>{row.chunk_count}</Figure>
        ),
    },
    { label: 'size', value: <Figure>{formatBytes(row.size_bytes)}</Figure> },
    { label: 'uploaded', value: <>{formatDate(row.created_at) ?? <NotRecorded />}</> },
    {
      label: row.error ? 'error' : 'workflow',
      value: (
        <span className={cn('min-w-0 break-all', row.error && 'text-block-ink')}>
          {row.error ?? row.workflow_id ?? <NotRecorded what="no workflow" />}
        </span>
      ),
    },
  ]
}

export function CorpusPanel({ token, reloadKey, onOpen }: CorpusPanelProps): ReactElement {
  /*
   * Two ways a row's facts come on screen, and they compose.
   *
   * `hovered` is the row under the pointer — one at a time, and it follows the pointer
   * off the row again. `pinned` is the set a reader has clicked open, and those stay
   * open while they compare two documents, which is the whole reason a pin exists: a
   * hover reveal cannot be compared against anything, because moving to the second row
   * closes the first.
   *
   * Keyboard reaches the same thing without a pointer: the disclosure control is a real
   * button, `onFocus` reveals on tab, and Enter or Space pins exactly as a click does.
   */
  /*
   * The list itself is closed until it is asked for.
   *
   * Nine rows of document is the bulk of this page, and almost nobody arriving here is
   * reading it: the figures above already answer how much is in the corpus and how much
   * of it is searchable. So the card keeps its heading and its counts, and the rows come
   * on the same terms the rows' own figures do — a hover shows them, a click keeps them.
   */
  const [listHovered, setListHovered] = useState(false)
  const [listPinned, setListPinned] = useState(false)

  const [hovered, setHovered] = useState<number | null>(null)
  const [pinned, setPinned] = useState<ReadonlySet<number>>(() => new Set())
  const togglePin = useCallback((id: number): void => {
    setPinned((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }, [])
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })

  const refresh = useCallback(async (): Promise<void> => {
    try {
      const res = await getDocuments(token)
      setLoad({ status: 'ready', rows: res.rows })
    } catch (error: unknown) {
      setLoad({
        status: 'error',
        message: error instanceof Error ? error.message : 'Failed to load documents',
      })
    }
  }, [token])

  useEffect(() => {
    void refresh()
  }, [refresh, reloadKey])

  const rows = load.status === 'ready' ? load.rows : []
  const listShown = listPinned || listHovered

  return (
    <Card
      className="overflow-hidden"
      onMouseEnter={() => setListHovered(true)}
      onMouseLeave={() => setListHovered(false)}
    >
      <div
        className={cn(
          'flex flex-wrap items-center justify-between gap-3 p-5',
          listShown && 'border-b border-border',
        )}
      >
        <div className="flex items-center gap-3">
          <FileText className="size-5 text-muted-foreground" />
          <div>
            <p className="eyebrow mb-0.5">documents · this tenant&rsquo;s corpus</p>
            <p className="text-sm text-muted-foreground">
              {load.status === 'loading'
                ? 'Reading the corpus…'
                : `${rows.length} document${rows.length === 1 ? '' : 's'}`}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => void refresh()}
            className={`inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
          >
            <RefreshCw className="size-3.5" aria-hidden />
            Refresh
          </button>
          <button
            type="button"
            aria-expanded={listShown}
            aria-controls="corpus-list"
            onFocus={() => setListHovered(true)}
            onClick={() => setListPinned((open) => !open)}
            className={`inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
          >
            <ChevronDown
              aria-hidden
              className={cn(
                'size-3.5 transition-transform duration-[--dur-fast] motion-reduce:transition-none',
                listShown && 'rotate-180',
              )}
            />
            {listPinned ? 'Hide the list' : 'Show the list'}
          </button>
        </div>
      </div>

      {/* A failed read is shown whether or not the list is open. Everything else is
          behind the disclosure; an error a reader has to hover to discover is an error
          the screen is hiding. */}
      {load.status === 'error' ? (
        <div className="border-t border-border p-4">
          <ErrorState
            error={load.message}
            fallback="The corpus could not be read."
            retry={() => void refresh()}
          />
        </div>
      ) : !listShown ? null : load.status === 'loading' ? (
        <div className="p-4">
          <LoadingState rows={3} label="Reading the corpus…" />
        </div>
      ) : rows.length === 0 ? (
        <div className="p-4">
          <EmptyState
            icon={FileText}
            title="No documents yet"
            body="Every document this tenant has put into the platform is listed here, newest first. Upload a PDF above and it appears the moment its row is written, before a single page has been parsed."
          />
        </div>
      ) : (
        <div id="corpus-list" className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-2/50">
              <tr>
                {['Document', 'Status'].map((h) => (
                  <th key={h} scope="col" className="eyebrow px-4 py-2.5 font-medium">
                    {h}
                  </th>
                ))}
                {/* The disclosure column has no heading text worth reading aloud on
                    every row, but a header cell must still name the column. */}
                <th scope="col" className="w-10 px-4 py-2.5">
                  <span className="sr-only">Show this document&rsquo;s ingest figures</span>
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => {
                const isPinned = pinned.has(row.document_id)
                const shown = isPinned || hovered === row.document_id
                return (
                  <Fragment key={row.document_id}>
                    <tr
                      onMouseEnter={() => setHovered(row.document_id)}
                      onMouseLeave={() =>
                        setHovered((current) => (current === row.document_id ? null : current))
                      }
                      className={cn(
                        'align-middle transition-colors duration-[--dur-fast]',
                        shown ? 'bg-surface-2/60' : 'hover:bg-surface-2/60',
                      )}
                    >
                      <td className="px-4 py-2.5">
                        <button
                          type="button"
                          onClick={() => onOpen?.(row.document_id)}
                          /* A document titled `dl` is a 15px-wide target. The row's own
                             label decides the width, so the floor has to be a minimum
                             rather than an overhang: min-h/min-w 1.5rem = 24px. */
                          className={`inline-block min-h-6 min-w-6 rounded-sm text-left font-medium text-foreground underline-offset-2 hover:underline ${FOCUS}`}
                        >
                          {row.title ?? row.filename}
                          <span className="sr-only">, open the ingest log</span>
                        </button>
                        <p className="text-[0.6875rem] text-muted-foreground">
                          <Figure>#{row.document_id}</Figure> · {row.doc_type ?? 'untyped'} ·{' '}
                          {row.doc_date ?? 'undated'}
                        </p>
                      </td>
                      <td className="px-4 py-2.5">
                        <Badge tone={statusVariant(row.status)}>{row.status}</Badge>
                      </td>
                      <td className="px-4 py-2.5">
                        <button
                          type="button"
                          aria-expanded={shown}
                          onFocus={() => setHovered(row.document_id)}
                          onClick={() => togglePin(row.document_id)}
                          className={cn(
                            'inline-flex size-6 items-center justify-center rounded-md text-muted-foreground',
                            'transition-colors duration-[--dur-fast] hover:text-foreground',
                            isPinned && 'text-foreground',
                            FOCUS,
                          )}
                        >
                          <ChevronDown
                            aria-hidden
                            className={cn(
                              'size-4 transition-transform duration-[--dur-fast] motion-reduce:transition-none',
                              shown && 'rotate-180',
                            )}
                          />
                          <span className="sr-only">
                            {isPinned
                              ? `Stop showing the ingest figures for ${row.title ?? row.filename}`
                              : `Keep the ingest figures for ${row.title ?? row.filename} on screen`}
                          </span>
                        </button>
                      </td>
                    </tr>
                    {shown && (
                      <tr className="bg-surface-2/40">
                        <td colSpan={3} className="px-4 pb-3 pt-0">
                          <dl className="flex flex-wrap items-baseline gap-x-5 gap-y-1.5 text-xs text-muted-foreground">
                            {rowFacts(row).map((fact) => (
                              <div key={fact.label} className="flex min-w-0 items-baseline gap-1.5">
                                <dt className="eyebrow">{fact.label}</dt>
                                <dd className="min-w-0">{fact.value}</dd>
                              </div>
                            ))}
                          </dl>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
