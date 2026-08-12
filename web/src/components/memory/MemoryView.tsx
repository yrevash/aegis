'use client'

import { MessagesSquare, WifiOff } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import {
  getMemoryFacts,
  getMemoryProfile,
  getMemorySessions,
  getMemoryWrites,
} from '@/lib/api/client'
import { isMock } from '@/lib/api/mode'
import { useAuth } from '@/lib/auth/AuthContext'
import { probeBackend, type ResolvedMode } from '@/lib/api/mode'
import { Card, CardBody } from '@/components/ui/Card'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { cn } from '@/lib/utils'

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

/** The known demo subjects (the console scenario's principals). */
const SUBJECTS: { id: string; label: string }[] = [
  { id: 'cust-mreed', label: 'M. Reed · A-771' },
  { id: 'acct-771', label: 'Account A-771' },
]

/**
 * Memory (§4.3) — "what the agent knows about this subject". A subject-summary
 * hero, then the semantic facts / structured profile / episodic sessions /
 * write-log as varied panels, with the flagship recall trace as a
 * "Why did it recall this?" expander at the foot. The honest tier machinery
 * (semantic / episodic / structured, bitemporal, relevance × recency ×
 * importance) lives one layer down in tooltips and detail popovers.
 *
 * A faithful Next.js port of `frontend/src/components/memory/MemoryView.tsx`
 * onto the web app's Card/Badge components and shared library.
 */
function MemoryView({ token }: { token: string | null }): ReactElement {
  const [subject, setSubject] = useState(SUBJECTS[0].id)
  const subjectLabel = SUBJECTS.find((s) => s.id === subject)?.label ?? subject

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
      {/* Section header + the one control on the surface: the subject picker. */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="eyebrow mb-1">long-term memory · Postgres + pgvector</p>
          <h1 className="t-hero text-foreground">Memory</h1>
        </div>
        <label className="flex items-center gap-2">
          <span className="eyebrow">subject</span>
          <select
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            className="h-9 rounded-md border border-input bg-surface px-3 text-sm outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            {SUBJECTS.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* Row 1 — subject-summary hero. */}
      <Card>
        <CardBody>
          <SubjectSummary
            subjectLabel={subjectLabel}
            highlights={highlights}
            lastSeen={lastSeen}
            stats={[
              { label: 'Facts', value: factCount },
              { label: 'Sessions', value: sessionCount },
              { label: 'Turns', value: turnCount },
            ]}
            trend={growth}
            loading={profile.state.status === 'loading'}
            sample={isMock()}
          />
        </CardBody>
      </Card>

      {/* Row 2 — what we know (wide) + profile. */}
      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="lg:col-span-2">
          <CardBody>
            <SemanticFactsPanel state={facts.state} />
          </CardBody>
        </Card>
        <Card>
          <CardBody>
            <StructuredProfilePanel state={profile.state} />
          </CardBody>
        </Card>
      </div>

      {/* Row 3 — sessions + recent updates. */}
      <div className="grid gap-6 lg:grid-cols-2">
        <Card>
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
        <Card>
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
 * Client entry for the Memory section. Runs the boot probe once (live-first, mock
 * fallback) before mounting the view, so the `/memory/*` fetches read the
 * resolved mode; the offline demo seeds from the in-browser fixtures and is
 * labelled with the honest banner — mirrors `EvalsMount` / `LLMOpsMount`.
 */
export function MemoryMount(): ReactElement {
  // The `/memory/*` accessors are RBAC-scoped: hand the view the real session
  // bearer, and hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()
  const [mode, setMode] = useState<ResolvedMode | null>(null)

  useEffect(() => {
    let alive = true
    void probeBackend().then((resolved) => {
      if (alive) setMode(resolved)
    })
    return () => {
      alive = false
    }
  }, [])

  if (mode === null || !hydrated) {
    return (
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <TooltipProvider>
      {mode.mode === 'mock' && (
        <div
          role="status"
          className={cn(
            'mb-4 flex items-center justify-center gap-2 rounded-lg bg-block px-4 py-1.5 text-center text-[0.78rem] font-medium text-white',
          )}
        >
          <WifiOff className="size-3.5 shrink-0" />
          <span className="font-mono uppercase tracking-wide">Offline demo — mock data</span>
        </div>
      )}
      <MemoryView token={session?.token ?? null} />
    </TooltipProvider>
  )
}
