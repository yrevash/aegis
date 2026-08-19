'use client'

import { CircleSlash, FileText, Loader2, RefreshCw } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Badge } from '@/components/primitives/badge'
import { Card } from '@/components/primitives/card'
import { getDocuments, type DocumentRow } from '@/lib/api/jobs'
import { cn } from '@/lib/utils'

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
function statusVariant(status: string): 'ok' | 'block' | 'agent' | 'outline' {
  if (status === 'succeeded') return 'ok'
  if (status === 'failed') return 'block'
  if (status === 'running' || status === 'pending') return 'agent'
  return 'outline'
}

/** Human-readable size, so a 4 MB PDF does not read as 4194304. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** Local date from an ISO 8601 timestamp, or an em dash. */
function formatDate(iso: string | null): string {
  if (!iso) return '—'
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
    <Card className="gap-0 overflow-hidden p-0">
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
          className="inline-flex items-center gap-2 rounded-md border border-border bg-card px-3 py-1.5 text-xs font-medium text-foreground transition-colors hover:bg-surface-2"
        >
          <RefreshCw className="size-3.5" />
          Refresh
        </button>
      </div>

      {load.status === 'loading' ? (
        <div className="flex min-h-[160px] items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading
        </div>
      ) : load.status === 'error' ? (
        <div className="flex min-h-[160px] flex-col items-center justify-center gap-2 px-6 text-center">
          <CircleSlash className="size-7 text-muted-foreground/50" />
          <p className="text-sm font-medium text-foreground">The corpus could not be read</p>
          <p className="max-w-md text-sm text-muted-foreground">{load.message}</p>
        </div>
      ) : rows.length === 0 ? (
        <div className="flex min-h-[160px] flex-col items-center justify-center gap-2 px-6 text-center">
          <CircleSlash className="size-7 text-muted-foreground/50" />
          <p className="text-sm font-medium text-foreground">No documents yet</p>
          <p className="max-w-md text-sm text-muted-foreground">
            Upload a PDF above and it appears here the moment its row is written — before
            a single page has been parsed.
          </p>
        </div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface-2/50">
              <tr className="text-[0.7rem] uppercase tracking-wide text-muted-foreground">
                <th className="px-4 py-2 font-medium">Document</th>
                <th className="px-4 py-2 font-medium">Status</th>
                <th className="px-4 py-2 font-medium">Stage</th>
                <th className="px-4 py-2 font-medium">Pages</th>
                <th className="px-4 py-2 font-medium">Chunks</th>
                <th className="px-4 py-2 font-medium">Size</th>
                <th className="px-4 py-2 font-medium">Uploaded</th>
                <th className="px-4 py-2 font-medium">Detail</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-border/70">
              {rows.map((row) => (
                <tr key={row.document_id} className="align-middle">
                  <td className="px-4 py-2.5">
                    <button
                      type="button"
                      onClick={() => onOpen?.(row.document_id)}
                      className="text-left font-medium text-foreground underline-offset-2 hover:underline"
                    >
                      {row.title ?? row.filename}
                    </button>
                    <p className="font-mono text-[0.68rem] text-muted-foreground">
                      #{row.document_id} · {row.doc_type ?? 'untyped'} ·{' '}
                      {row.doc_date ?? 'undated'}
                    </p>
                  </td>
                  <td className="px-4 py-2.5">
                    <Badge variant={statusVariant(row.status)}>{row.status}</Badge>
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {row.completed_stage ?? '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {row.page_count ?? '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {row.chunk_count ?? '—'}
                  </td>
                  <td className="px-4 py-2.5 font-mono text-xs text-muted-foreground">
                    {formatBytes(row.size_bytes)}
                  </td>
                  <td className="px-4 py-2.5 text-xs text-muted-foreground">
                    {formatDate(row.created_at)}
                  </td>
                  <td
                    className={cn(
                      'max-w-[22rem] truncate px-4 py-2.5 text-xs',
                      row.error ? 'text-block-ink' : 'text-muted-foreground',
                    )}
                  >
                    {row.error ?? row.workflow_id ?? '—'}
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
