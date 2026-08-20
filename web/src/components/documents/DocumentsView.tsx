'use client'

import { BookOpen, FileText, Layers, ScrollText } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Figure } from '@/components/primitives/Figure'
import { InfoTip } from '@/components/primitives/InfoTip'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Absence, Receipt } from '@/components/primitives/Receipt'
import { ErrorState, LoadingState } from '@/components/primitives/States'
import { BackendGate } from '@/components/shared/BackendGate'
import { Badge } from '@/components/ui/Badge'
import { Card, CardBody } from '@/components/ui/Card'
import { DataPanel } from '@/components/ui/DataPanel'
import { StatCard } from '@/components/ui/StatCard'
import { Scene, SceneState } from '@/components/illustration/Scene'
import { CorpusPanel } from '@/components/jobs/CorpusPanel'
import { IngestLog } from '@/components/jobs/IngestLog'
import { UploadPanel } from '@/components/jobs/UploadPanel'
import { getDocuments, type DocumentRow } from '@/lib/api/jobs'
import { useAuth } from '@/lib/auth/AuthContext'

/**
 * Documents — what this agent knows, and how a tenant gives it more.
 *
 * **Why this section exists.** The upload control was real and it was mounted inside
 * `Jobs`, which is a pipeline-*operations* screen on two admin portals. So the one act
 * a tenant performs more than any other — hand the agent a document — lived behind a
 * nav entry named after the queue that processes it, and the `client` portal, which is
 * the role the product exists for, had no route to it at all. A tenant that cannot
 * contribute knowledge has a read-only product.
 *
 * Everything here is composed from panels that already existed and are imported, not
 * copied: {@link UploadPanel} is the front door, {@link CorpusPanel} is the shelf, and
 * {@link IngestLog} is the per-document stage log. What this file adds is the thing
 * none of them could add on their own — the *shape of the corpus*, read from the same
 * `GET /documents` projection the shelf reads, so "what does it know?" is answered
 * before "what did I upload?".
 *
 * **The counts are honest about the placeholders.** Six of this tenant's rows are
 * 0-chunk seed placeholders sitting at `pending`; they are counted as pending and
 * never as knowledge, and the searchable figure is the sum of real chunk counts —
 * which is `null` on every row that has not parsed, and is therefore stated as an
 * {@link Absence} when nothing has.
 */

/** The one focus treatment on this screen. */
const FOCUS =
  'outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background'

/** What the corpus adds up to. Derived, never asserted — every field comes off a row. */
interface CorpusShape {
  total: number
  ingested: number
  pending: number
  failed: number
  /** Summed `chunk_count`, or null when not one document has parsed. */
  chunks: number | null
  /** Distinct `doc_type` values the tenant supplied. */
  types: string[]
  /** How many rows carry no `doc_type` at all. */
  untyped: number
}

/** Fold the document rows into the shape above. */
export function summarizeCorpus(rows: readonly DocumentRow[]): CorpusShape {
  let chunks: number | null = null
  const types = new Set<string>()
  let untyped = 0
  for (const row of rows) {
    if (row.chunk_count != null) chunks = (chunks ?? 0) + row.chunk_count
    if (row.doc_type) types.add(row.doc_type)
    else untyped += 1
  }
  return {
    total: rows.length,
    ingested: rows.filter((r) => r.status === 'succeeded').length,
    pending: rows.filter((r) => r.status === 'pending' || r.status === 'running').length,
    failed: rows.filter((r) => r.status === 'failed').length,
    chunks,
    types: [...types].sort(),
    untyped,
  }
}

type LoadState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'ready'; rows: DocumentRow[] }

/**
 * The corpus at a glance, above the shelf.
 *
 * No `trend` on any tile: `GET /documents` is a snapshot of the table and carries no
 * history at all, so a sparkline here would be a series this screen invented
 * (DESIGN.md §3 — `trend` only where a real one exists).
 */
function CorpusShapeRow({ shape }: { shape: CorpusShape }): ReactElement {
  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <StatCard
        label="Documents"
        value={String(shape.total)}
        icon={FileText}
        tone="neutral"
        source="GET /documents · this tenant's own rows"
      />
      <StatCard
        label="Ingested and searchable"
        value={`${shape.ingested} of ${shape.total}`}
        icon={BookOpen}
        tone={shape.ingested > 0 ? 'ok' : 'neutral'}
        source="documents.status = succeeded"
      />
      <StatCard
        label="Waiting to be ingested"
        value={String(shape.pending)}
        icon={Layers}
        tone={shape.pending > 0 ? 'risk' : 'ok'}
        source="documents.status = pending or running"
      />
      {shape.chunks == null ? (
        <Card>
          <CardBody>
            <Absence
              figure="Searchable passages"
              why="Not one document has finished parsing, so no row carries a chunk count yet."
              needed="an ingest that reaches the chunk stage — the count is written with it"
            />
          </CardBody>
        </Card>
      ) : (
        <StatCard
          label="Searchable passages"
          value={String(shape.chunks)}
          icon={ScrollText}
          tone="agent"
          source="sum of documents.chunk_count · null on anything unparsed"
        />
      )}
    </div>
  )
}

/** The documents surface for one tenant-scoped principal. */
export function DocumentsView({ token }: { token: string | null }): ReactElement {
  const [load, setLoad] = useState<LoadState>({ status: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)
  const [openDocument, setOpenDocument] = useState<number | null>(null)

  const refresh = useCallback(() => setReloadKey((n) => n + 1), [])

  useEffect(() => {
    let alive = true
    getDocuments(token)
      .then((data) => {
        if (alive) setLoad({ status: 'ready', rows: data.rows })
      })
      .catch((error: unknown) => {
        if (alive) {
          setLoad({
            status: 'error',
            message: error instanceof Error ? error.message : 'The corpus could not be read.',
          })
        }
      })
    return () => {
      alive = false
    }
  }, [token, reloadKey])

  return (
    <div className="space-y-4">
      {load.status === 'loading' ? (
        <Card>
          <CardBody>
            <LoadingState rows={4} label="Reading the corpus…" />
          </CardBody>
        </Card>
      ) : load.status === 'error' ? (
        <Card>
          <CardBody>
            <ErrorState error={load.message} retry={refresh} />
          </CardBody>
        </Card>
      ) : (
        <>
          <CorpusShapeRow shape={summarizeCorpus(load.rows)} />

          {/*
            The upload, given the room the act deserves — it is the reason this
            section exists. The scene beside it is Storyset's `Upload`, and it is
            here because it depicts what this panel does rather than decorating it.
          */}
          <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_18rem]">
            <UploadPanel token={token} onUploaded={refresh} />
            <Card className="hidden overflow-hidden lg:block">
              <CardBody className="flex h-full flex-col items-center justify-center gap-3">
                <Scene name="upload" size="md" />
                <p className="text-center text-xs text-muted-foreground">
                  A document becomes searchable only after every ingest stage commits.
                  <InfoTip label="What ingest does to a document">
                    Six stages, each writing its own row: parse, chunk, enrich, embed,
                    index and graph. The document is stored the moment it is uploaded;
                    it becomes answerable when the index stage commits, and the stage
                    log on each row shows exactly how far it got.
                  </InfoTip>
                </p>
              </CardBody>
            </Card>
          </div>

          {load.rows.length === 0 ? (
            <Card>
              <CardBody>
                <SceneState name="empty" size="md">
                  <p className="t-title text-foreground">Nothing in the corpus yet</p>
                  <p className="mt-1 text-sm text-muted-foreground">
                    The agent can only ground an answer in what this tenant has given it.
                    Upload a document above and its six ingest stages appear here as they
                    commit.
                  </p>
                </SceneState>
              </CardBody>
            </Card>
          ) : (
            <CorpusPanel token={token} reloadKey={reloadKey} onOpen={setOpenDocument} />
          )}

          {openDocument != null ? (
            <DataPanel
              eyebrow="ingest · stage by stage"
              title={
                <span className="flex flex-wrap items-center gap-2">
                  Document <Figure>{openDocument}</Figure>
                  <Badge tone="neutral">live log</Badge>
                </span>
              }
              actions={
                <button
                  type="button"
                  onClick={() => setOpenDocument(null)}
                  className={`rounded-md border border-border px-2.5 py-1 text-xs text-muted-foreground transition-colors duration-[--dur-fast] hover:bg-surface-2 ${FOCUS}`}
                >
                  Close
                </button>
              }
            >
              <IngestLog documentId={openDocument} token={token} />
            </DataPanel>
          ) : null}

          <Receipt
            label="Source"
            origin="GET /documents · the documents table, not the job queue"
            detail="a document whose ingest never started owns no job row, so the queue alone could not list it"
          />
        </>
      )}
    </div>
  )
}

/** Client entry for the Documents section — gated on a reachable backend. */
export function DocumentsMount(): ReactElement {
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="rounded-lg border border-dashed border-border bg-surface-2/40 p-4">
        <LoadingState rows={4} label="Restoring the session…" />
      </div>
    )
  }

  return (
    <BackendGate>
      <div className="space-y-4">
        <PageHeader
          eyebrow="corpus · ingestion"
          title="Documents"
          actions={
            <InfoTip label="What this section is for">
              Everything the agent can ground an answer in, and the one place to add
              more. A document is stored under your tenant the moment it uploads and
              becomes answerable when its ingest reaches the index stage; nothing here
              is visible to another tenant, and a re-upload of a file already held
              starts nothing rather than duplicating it.
            </InfoTip>
          }
        />
        <DocumentsView token={session?.token ?? null} />
      </div>
    </BackendGate>
  )
}
