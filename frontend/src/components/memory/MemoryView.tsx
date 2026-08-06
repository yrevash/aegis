import { MessagesSquare } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { getMemoryFacts, getMemoryProfile, getMemorySessions, getMemoryWrites } from '@/api/client'
import { isMock } from '@/api/mode'
import { useAsync } from '@/components/admin/useAsync'
import { LoadingRow } from '@/components/common/StateRow'
import { BentoGrid, BentoTile, RevealOnScroll } from '@/components/shared'
import { formatAgo } from '@/lib/datetime'

import { EpisodicSessionsPanel } from './EpisodicSessionsPanel'
import { KnowledgeGraphTile } from './KnowledgeGraphTile'
import { PanelHeader } from './PanelHeader'
import { RecallDebugPanel } from './RecallDebugPanel'
import { SemanticFactsPanel } from './SemanticFactsPanel'
import { StructuredProfilePanel } from './StructuredProfilePanel'
import { SubjectSummary } from './SubjectSummary'
import { WriteLogPanel } from './WriteLogPanel'
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
 * Memory (§4.3) — reframed from three raw memory tiers into "what the agent knows
 * about this subject". A non-linear bento: a subject-summary hero + knowledge
 * graph anchor up top, then What we know / Profile / Sessions / Recent updates as
 * varied tiles, with the recall trace demoted to an admin "Why did it recall
 * this?" expander. The honest tier machinery (semantic / episodic / structured,
 * bitemporal, relevance × recency × importance) lives one layer down in tooltips
 * and detail popovers.
 */
export function MemoryView({ token }: { token: string | null }): ReactElement {
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
    <div className="flex flex-col gap-4">
      {/* Subject picker — the one control on the surface. */}
      <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
        <div>
          <h2 className="t-title text-foreground">Aegis Memory</h2>
          <p className="text-xs text-muted-foreground">
            What the agent knows about a subject — long-term memory on Postgres + pgvector.
          </p>
        </div>
        <label className="flex items-center gap-2 sm:ml-auto">
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

      <BentoGrid>
        {/* Row 1 — subject summary hero + knowledge-graph anchor. */}
        <BentoTile span={8} rows={2} hero reveal index={0}>
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
        </BentoTile>
        <RevealOnScroll className="col-span-12 min-h-[20rem] lg:col-span-4 lg:row-span-2" delayMs={40}>
          <KnowledgeGraphTile token={token} />
        </RevealOnScroll>

        {/* Row 2 — what we know (wide) + profile. */}
        <BentoTile span={7} reveal index={2}>
          <SemanticFactsPanel state={facts.state} />
        </BentoTile>
        <BentoTile span={5} reveal index={3}>
          <StructuredProfilePanel state={profile.state} />
        </BentoTile>

        {/* Row 3 — sessions + recent updates. */}
        <BentoTile span={6} reveal index={4}>
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
        </BentoTile>
        <BentoTile span={6} reveal index={5}>
          <WriteLogPanel state={writes.state} />
        </BentoTile>
      </BentoGrid>

      {/* Flagship recall trace — demoted to an admin expander under the bento. */}
      <RecallDebugPanel token={token} subject={subject} />
    </div>
  )
}
