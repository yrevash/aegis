'use client'

import { FileUp, Loader2, Upload } from 'lucide-react'
import { useRef, useState, type FormEvent, type ReactElement } from 'react'

import { Card } from '@/components/primitives/card'
import { JobsApiError, uploadDocument, type DocumentUpload } from '@/lib/api/jobs'
import { cn } from '@/lib/utils'

interface UploadPanelProps {
  token: string | null
  /** Called after a successful upload, so the job table beside this reloads. */
  onUploaded?: () => void
}

/** What the panel is currently saying about the last attempt. */
type Notice =
  | { kind: 'ok'; text: string }
  | { kind: 'duplicate'; text: string }
  | { kind: 'refused'; text: string }
  | { kind: 'error'; text: string }

/** Human-readable size, so a 4 MB PDF does not read as 4194304. */
function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/**
 * Upload a document and start its ingest — the front door to the whole pipeline.
 *
 * Three things this surface is deliberately explicit about, because each one is a
 * decision the backend makes and a user cannot otherwise see:
 *
 * - **Type and date are the tenant's to give.** They are two of the four fields every
 *   chunk's prefix carries, and nothing in a PDF's bytes states either — so they are
 *   offered here, and left blank they are recorded as *unknown* rather than guessed.
 *   The date is the date the document is *from*, never the date it was uploaded.
 * - **Re-uploading the same file starts nothing.** The response says so, and the panel
 *   repeats it, because "nothing happened" and "it worked" look identical otherwise.
 * - **A refusal is shown with its reason.** Admission control refuses an ingest the
 *   tenant cannot afford or has no free slot for; swallowing that into "upload failed"
 *   would recreate in the browser exactly the invisible backpressure the gate exists to
 *   prevent.
 */
export function UploadPanel({ token, onUploaded }: UploadPanelProps): ReactElement {
  const fileRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [docType, setDocType] = useState('')
  const [docDate, setDocDate] = useState('')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<Notice | null>(null)

  const submit = async (event: FormEvent): Promise<void> => {
    event.preventDefault()
    if (!file || busy) return
    setBusy(true)
    setNotice(null)
    try {
      const res: DocumentUpload = await uploadDocument(file, { docType, docDate }, token)
      setNotice(
        res.created
          ? { kind: 'ok', text: res.detail }
          : { kind: 'duplicate', text: res.detail },
      )
      setFile(null)
      if (fileRef.current) fileRef.current.value = ''
      onUploaded?.()
    } catch (error: unknown) {
      if (error instanceof JobsApiError && error.refusedByAdmission) {
        setNotice({
          kind: 'refused',
          text: `Refused by the ${error.gate ?? 'admission'} gate — ${error.message}`,
        })
      } else {
        setNotice({
          kind: 'error',
          text: error instanceof Error ? error.message : 'The upload failed',
        })
      }
    } finally {
      setBusy(false)
    }
  }

  return (
    <Card className="gap-0 p-0">
      <form onSubmit={(event) => void submit(event)} className="flex flex-col gap-4 p-5">
        <div className="flex items-center gap-3">
          <FileUp className="size-5 text-muted-foreground" />
          <div>
            <p className="eyebrow mb-0.5">documents · ingest</p>
            <p className="text-sm text-muted-foreground">
              Upload a PDF. It is stored, queued and parsed by the durable substrate — the
              stages appear in the table below as they commit.
            </p>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-3">
          <label className="flex flex-col gap-1.5 text-sm md:col-span-3">
            <span className="font-medium text-foreground">Document</span>
            <input
              ref={fileRef}
              type="file"
              accept="application/pdf,.pdf"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
              className="h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background file:mr-3 file:rounded-md file:border-0 file:bg-surface-2 file:px-3 file:py-1 file:text-sm file:text-foreground"
            />
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-foreground">Type (optional)</span>
            <input
              type="text"
              value={docType}
              placeholder="policy · 10-K · lab report"
              onChange={(event) => setDocType(event.target.value)}
              className="h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            />
            <span className="text-xs text-muted-foreground">
              Recorded as <code>untyped</code> if left blank — never inferred.
            </span>
          </label>

          <label className="flex flex-col gap-1.5 text-sm">
            <span className="font-medium text-foreground">Document date (optional)</span>
            <input
              type="date"
              value={docDate}
              onChange={(event) => setDocDate(event.target.value)}
              className="h-11 rounded-lg border border-border bg-card px-3 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            />
            <span className="text-xs text-muted-foreground">
              The date the document is <em>from</em>, not today.
            </span>
          </label>

          <div className="flex flex-col justify-end gap-1.5">
            <button
              type="submit"
              disabled={!file || busy}
              className="inline-flex h-11 touch-manipulation items-center justify-center gap-2 rounded-lg border border-border bg-card px-4 text-sm font-medium text-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 disabled:opacity-50 outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background"
            >
              {busy ? (
                <Loader2 className="size-4 animate-spin motion-reduce:animate-none" aria-hidden />
              ) : (
                <Upload className="size-4" aria-hidden />
              )}
              {busy ? 'Uploading…' : 'Upload and ingest'}
            </button>
            <span className="text-xs text-muted-foreground">
              {file ? `${file.name} · ${formatBytes(file.size)}` : 'No file chosen'}
            </span>
          </div>
        </div>

        {notice ? (
          <div
            role="status"
            className={cn(
              'rounded-lg border px-4 py-3 text-sm',
              notice.kind === 'ok' && 'border-ok/60 bg-ok/10 text-ok-ink',
              notice.kind === 'duplicate' && 'border-border bg-surface-2 text-foreground',
              notice.kind === 'refused' && 'border-risk/60 bg-risk/10 text-risk-ink',
              notice.kind === 'error' && 'border-block/60 bg-block/10 text-block-ink',
            )}
          >
            {notice.text}
          </div>
        ) : null}
      </form>
    </Card>
  )
}
