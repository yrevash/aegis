'use client'

import { MessagesSquare } from 'lucide-react'
import type { ReactElement } from 'react'

import {
  getMemoryFacts,
  getMemoryProfile,
  getMemorySessions,
  getMemoryWrites,
} from '@/lib/api/client'
import { Card, CardBody } from '@/components/ui/Card'

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
 * recall trace — the **read** half of the memory control plane.
 *
 * It is a component rather than a screen so the four `/memory/*` reads are only ever
 * issued for a subject the operator actually chose. The picker, the write, the
 * correction and the erasure live in `@/components/memoryctl`, which mounts this
 * underneath them: a record you can read and a record you can change are one screen,
 * and splitting them would put the evidence on a different page from the button.
 */
export function SubjectPanels({
  token,
  subject,
  refreshKey = 0,
}: {
  token: string | null
  subject: string
  /** Bump to re-read after a write — a corrected fact must not still read as the old one. */
  refreshKey?: number
}): ReactElement {
  const deps = [token, subject, refreshKey]
  const facts = useAsync(() => getMemoryFacts(token, subject, true), deps)
  const profile = useAsync(() => getMemoryProfile(token, subject), deps)
  const sessions = useAsync(() => getMemorySessions(token, subject), deps)
  const writes = useAsync(() => getMemoryWrites(token, subject), deps)

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
