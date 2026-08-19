'use client'

import { Brain, MessagesSquare } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import {
  getMemoryFacts,
  getMemoryProfile,
  getMemorySessions,
  getMemoryWrites,
} from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { Button } from '@/components/primitives/button'
import { Card, CardBody } from '@/components/ui/Card'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { BackendGate } from '@/components/shared/BackendGate'

import { ChatThreadsPanel } from './ChatThreadsPanel'
import { EpisodicSessionsPanel } from './EpisodicSessionsPanel'
import { LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { RecallDebugPanel } from './RecallDebugPanel'
import { SemanticFactsPanel } from './SemanticFactsPanel'
import { StructuredProfilePanel } from './StructuredProfilePanel'
import { SubjectSummary } from './SubjectSummary'
import { WriteLogPanel } from './WriteLogPanel'
import { formatAgo } from './datetime'
import { useAsync } from './useAsync'
import {
  factGrowthSeries,
  summaryHighlights,
  totalTurns,
  validFactCount,
} from './memoryText'

/**
 * Everything the platform holds on ONE subject: the summary hero, the semantic
 * facts / structured profile / episodic sessions / write-log panels, and the
 * recall trace. Split from {@link MemoryView} so the four `/memory/*` reads are
 * only ever issued for a subject the operator actually asked for.
 */
function SubjectPanels({ token, subject }: { token: string | null; subject: string }): ReactElement {
  const facts = useAsync(() => getMemoryFacts(token, subject, true), [token, subject])
  const profile = useAsync(() => getMemoryProfile(token, subject), [token, subject])
  const sessions = useAsync(() => getMemorySessions(token, subject), [token, subject])
  const writes = useAsync(() => getMemoryWrites(token, subject), [token, subject])

  const factRows = facts.state.status === 'ready' ? facts.state.data.rows : []
  const sessionRows = sessions.state.status === 'ready' ? sessions.state.data.rows : []
  const writeRows = writes.state.status === 'ready' ? writes.state.data.rows : []

  const factCount = facts.state.status === 'ready' ? validFactCount(factRows) : null
  const sessionCount = sessions.state.status === 'ready' ? sessionRows.length : null
  const turnCount = sessions.state.status === 'ready' ? totalTurns(sessionRows) : null

  const profileData = profile.state.status === 'ready' ? profile.state.data.data : {}
  const highlights = summaryHighlights(profileData)
  const lastActive = sessionRows
    .map((s) => s.last_active_at)
    .filter((t): t is string => t != null)
    .sort()
    .at(-1)
  const lastSeen = lastActive ? formatAgo(lastActive) : null
  const growth = factGrowthSeries(writeRows)

  return (
    <div className="space-y-6">
        {/* Row 1 — subject-summary hero. */}
      <Card>
        <CardBody>
          <SubjectSummary
            subjectLabel={subject}
            highlights={highlights}
            lastSeen={lastSeen}
            stats={[
              { label: 'Facts', value: factCount },
              { label: 'Sessions', value: sessionCount },
              { label: 'Turns', value: turnCount },
            ]}
            trend={growth}
            loading={profile.state.status === 'loading'}
          />
        </CardBody>
      </Card>

      {/* Rows 2 and 3 share one 3 + 2 column rhythm so the vertical seam lines
          up, and `items-start` keeps every card at its own content height
          instead of padding the shorter one out with dead space. */}
      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardBody>
            <SemanticFactsPanel state={facts.state} />
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardBody>
            <StructuredProfilePanel state={profile.state} />
          </CardBody>
        </Card>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-3">
          <CardBody>
            <div className="flex h-full flex-col gap-3">
              <PanelHeader
                icon={MessagesSquare}
                title="Sessions"
                tint="bg-agent/12"
                ink="text-agent-ink"
                info="Past conversations with this subject, each with a running summary. Expand a row for its transcript."
              />
              {sessions.state.status === 'loading' && <LoadingRow label="Loading sessions…" />}
              {sessions.state.status === 'error' && (
                <div className="py-8 text-sm text-destructive">
                  Could not load sessions. {sessions.state.message}
                </div>
              )}
              {sessions.state.status === 'ready' && (
                <EpisodicSessionsPanel token={token} sessions={sessionRows} />
              )}
            </div>
          </CardBody>
        </Card>
        <Card className="lg:col-span-2">
          <CardBody>
            <WriteLogPanel state={writes.state} />
          </CardBody>
        </Card>
      </div>

      {/* Flagship recall trace — the "Why did it recall this?" expander. */}
      <RecallDebugPanel token={token} subject={subject} />
    </div>
  )
}

/**
 * Memory (§4.3) — "what the agent knows about this subject". The one control on
 * the surface names the subject; everything below is that subject's record. The
 * honest tier machinery (semantic / episodic / structured, bitemporal,
 * relevance × recency × importance) lives one layer down in tooltips and detail
 * popovers.
 *
 * A faithful Next.js port of `frontend/src/components/memory/MemoryView.tsx`
 * onto the web app's Card/Badge components and shared library.
 */
function MemoryView({ token }: { token: string | null }): ReactElement {
  // The backend exposes no "list known subjects" route — memory is keyed by an
  // arbitrary subject string the caller supplies — so the picker is a free-text
  // field rather than a dropdown of ids the console would have to invent.
  const [draft, setDraft] = useState('')
  const [subject, setSubject] = useState('')

  return (
    <div className="space-y-6">
      {/* Section header + the one control on the surface: the subject picker. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow mb-1">long-term memory · Postgres + pgvector</p>
          <h1 className="t-hero text-foreground">Memory</h1>
        </div>
        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault()
            setSubject(draft.trim())
          }}
        >
          <label className="eyebrow" htmlFor="memory-subject">
            subject
          </label>
          <input
            id="memory-subject"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder="subject id"
            className="h-9 rounded-md border border-input bg-surface px-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          />
          <Button type="submit" size="sm" variant="outline" disabled={draft.trim() === ''}>
            Load
          </Button>
        </form>
      </div>


      {/* The caller's own chat threads. Not gated on the subject picker: a chat is
          keyed by the person who had it, not by the subject the store is keyed on,
          and it is how an operator finds a session id worth inspecting. */}
      <Card>
        <CardBody>
          <ChatThreadsPanel token={token} />
        </CardBody>
      </Card>

      {subject === '' ? (
        <Card>
          <CardBody>
            <div className="flex min-h-56 flex-col items-center justify-center gap-3 text-center">
              <Brain className="size-8 text-muted-foreground/50" />
              <div>
                <p className="text-sm font-medium text-foreground">No subject selected</p>
                <p className="mt-1 max-w-md text-sm text-muted-foreground">
                  Memory is keyed by subject. Enter the subject id you want to inspect — the
                  facts, profile, sessions and write-log below are read straight from the store
                  for that subject alone.
                </p>
              </div>
            </div>
          </CardBody>
        </Card>
      ) : (
        <SubjectPanels token={token} subject={subject} />
      )}
    </div>
  )
}

/** Client entry for the Memory section — gated on a reachable backend. */
export function MemoryMount(): ReactElement {
  // The `/memory/*` accessors are RBAC-scoped: hand the view the real session
  // bearer, and hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <MemoryView token={session?.token ?? null} />
      </TooltipProvider>
    </BackendGate>
  )
}
