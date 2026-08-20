'use client'

import { FileText, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

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
export function CorpusPanel({ token, reloadKey, onOpen }: CorpusPanelProps): ReactElement {
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

  return (
    <Card className="overflow-hidden">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-border p-5">
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
        <button
          type="button"
          onClick={() => void refresh()}
          className={`inline-flex h-11 touch-manipulation items-center gap-2 rounded-lg border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
        >
          <RefreshCw className="size-3.5" aria-hidden />
          Refresh
        </button>
      </div>

      {load.status === 'loading' ? (
        <div className="p-4">
          <LoadingState rows={3} label="Reading the corpus…" />
        </div>
      ) : load.status === 'error' ? (
        <div className="p-4">
          <ErrorState
            error={load.message}
            fallback="The corpus could not be read."
            retry={() => void refresh()}
          />
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
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-2/50">
              <tr>
                {['Document', 'Status', 'Stage', 'Pages', 'Chunks', 'Size', 'Uploaded', 'Detail'].map(
                  (h) => (
                    <th key={h} scope="col" className="eyebrow px-4 py-2.5 font-medium">
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody className="divide-y divide-border">
              {rows.map((row) => (
                <tr
                  key={row.document_id}
                  className="align-middle transition-colors duration-[--dur-fast] hover:bg-surface-2/60"
                >
                  <td className="px-4 py-2.5">
                    <button
                      type="button"
                      onClick={() => onOpen?.(row.document_id)}
                      className={`rounded-sm text-left font-medium text-foreground underline-offset-2 hover:underline ${FOCUS}`}
                    >
                      {row.title ?? row.filename}
                      <span className="sr-only">, open the ingest log</span>
                    </button>
                    <p className="text-[0.68rem] text-muted-foreground">
                      <Figure>#{row.document_id}</Figure> · {row.doc_type ?? 'untyped'} ·{' '}
                      {row.doc_date ?? 'undated'}
                    </p>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge tone={statusVariant(row.status)}>{row.status}</Badge>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {row.completed_stage ? (
                      <Figure>{row.completed_stage}</Figure>
                    ) : (
                      <NotRecorded what="not started" />
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {row.page_count === null ? <NotRecorded what="not parsed" /> : <Figure>{row.page_count}</Figure>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {row.chunk_count === null ? <NotRecorded what="not chunked" /> : <Figure>{row.chunk_count}</Figure>}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    <Figure>{formatBytes(row.size_bytes)}</Figure>
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {formatDate(row.created_at) ?? <NotRecorded />}
                  </td>
                  <td
                    className={cn(
                      'max-w-[22rem] truncate px-4 py-2.5 text-xs',
                      row.error ? 'text-block-ink' : 'text-muted-foreground',
                    )}
                  >
                    {row.error ?? row.workflow_id ?? <NotRecorded what="no workflow" />}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  )
}
