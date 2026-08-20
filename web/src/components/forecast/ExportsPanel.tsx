'use client'

import { Download, Loader2 } from 'lucide-react'
import { useCallback, useState, type ReactElement } from 'react'

import { Card, CardBody, CardHeader } from '@/components/ui/Card'
import { ApiError } from '@/lib/api/apiError'
import {
  reportDownloadPath,
  startReportDownload,
  type ReportFilters,
  type ReportId,
} from '@/lib/api/reports'
import { useAuth } from '@/lib/auth/AuthContext'

/** One offered export: what it holds, and why an operator would want the file. */
interface ExportRow {
  id: ReportId
  title: string
  contents: string
}

const ROWS: ExportRow[] = [
  {
    id: 'forecast',
    title: 'Spend forecast',
    contents:
      'Every projected step with its band, and the caveats as columns on each row: ' +
      'the coverage requested, the coverage achieved on held-out windows, and whether ' +
      'the cumulative envelope is calibrated (it is not).',
  },
  {
    id: 'budget',
    title: 'Budget caps and consumption',
    contents:
      'Every governing cap beside the spend the gateway enforcer measures against it — ' +
      'the same accessor, so the file and the cap that blocks a call cannot disagree.',
  },
  {
    id: 'audit',
    title: 'Audit trail',
    contents:
      'The trail in scope, streamed in full rather than clamped to a page. The download ' +
      'is itself an audited action, so this export appears at the top of the next one.',
  },
  {
    id: 'tenant',
    title: 'Tenant roster',
    contents:
      'Users, roles and the most recent sign-in the audit trail can evidence. There is ' +
      'no last-login column on the users table, so an empty cell means "not observed".',
  },
]

/**
 * Take the record away — four CSVs, each scoped by the server, each audited.
 *
 * **The mechanism, and why it is not a blob.** Each button mints a 60-second download
 * ticket and then lets the browser fetch the file itself, saving it as it streams from
 * `Content-Disposition: attachment`. Building the CSV in the page instead — a `Blob`
 * and a synthetic `<a download>` — would buffer a whole export in the tab, defeat the
 * server's streaming, and do nothing at all inside a sandboxed frame.
 *
 * **The scope is not on the button.** Every route re-resolves the tenant filter from
 * the caller's own token, so nothing rendered here can widen it; the file then states
 * the scope it was actually generated under, in its own first rows.
 */
export function ExportsPanel({
  forecastFilters,
}: {
  /** The forecast panel's current parameters, so the file matches the screen. */
  forecastFilters: ReportFilters
}): ReactElement {
  const { session } = useAuth()
  const token = session?.token ?? null
  const [busy, setBusy] = useState<ReportId | null>(null)
  const [error, setError] = useState<string | null>(null)

  const download = useCallback(
    (report: ReportId) => {
      setBusy(report)
      setError(null)
      startReportDownload(token, report, report === 'forecast' ? forecastFilters : {})
        .catch((err: unknown) => {
          setError(
            err instanceof ApiError
              ? err.message
              : 'Could not reach the export service. Is the backend running?',
          )
        })
        .finally(() => setBusy(null))
    },
    [token, forecastFilters],
  )

  return (
    <Card>
      <CardHeader eyebrow="aegis.reports · CSV" title="Take the record away" />
      <CardBody className="space-y-3">
        {error ? <p className="text-sm text-danger">{error}</p> : null}
        {ROWS.map((row) => (
          <div
            key={row.id}
            className="flex flex-col gap-3 rounded-lg border border-border bg-surface-2/40 p-4 sm:flex-row sm:items-start sm:justify-between"
          >
            <div className="min-w-0 space-y-1.5">
              <p className="text-sm font-semibold text-foreground">{row.title}</p>
              <p className="text-[0.78rem] leading-relaxed text-muted-foreground">
                {row.contents}
              </p>
              <p className="tabular font-mono text-[0.68rem] text-muted-foreground">
                GET {reportDownloadPath(row.id, row.id === 'forecast' ? forecastFilters : {})}
              </p>
            </div>
            <button
              type="button"
              onClick={() => download(row.id)}
              disabled={busy !== null}
              className="inline-flex shrink-0 items-center gap-1.5 rounded-lg border border-border px-3 py-1.5 text-[0.78rem] font-medium text-foreground transition-colors hover:bg-surface-2 focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none disabled:opacity-50"
            >
              {busy === row.id ? (
                <Loader2 className="size-3.5 motion-safe:animate-spin" />
              ) : (
                <Download className="size-3.5" />
              )}
              Download CSV
            </button>
          </div>
        ))}
        <p className="border-t border-border pt-3 font-mono text-[0.7rem] leading-relaxed text-muted-foreground">
          Every export states its scope, its window and the account that took it in the file
          itself, ends with the row count it wrote, and leaves a report.export row in the audit
          trail.
        </p>
      </CardBody>
    </Card>
  )
}
