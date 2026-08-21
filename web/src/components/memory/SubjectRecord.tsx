'use client'

import { MessagesSquare } from 'lucide-react'
import type { ReactElement } from 'react'

import {
  getMemoryFacts,
  getMemoryProfile,
  getMemorySessions,
  getMemoryWrites,
} from '@/lib/api/client'
import { BarChart } from '@/components/charts/BarChart'
import { SceneState } from '@/components/illustration/Scene'
import { Figure } from '@/components/primitives/Figure'
import { Receipt } from '@/components/primitives/Receipt'
import { Card, CardBody, CardHeader } from '@/components/ui/Card'

import { EpisodicSessionsPanel } from './EpisodicSessionsPanel'
import { ErrorRow, LoadingRow } from './StateRow'
import { PanelHeader } from './PanelHeader'
import { RecallDebugPanel } from './RecallDebugPanel'
import { SemanticFactsPanel } from './SemanticFactsPanel'
import { StructuredProfilePanel } from './StructuredProfilePanel'
import { SubjectSummary } from './SubjectSummary'
import { WriteLogPanel } from './WriteLogPanel'
import { formatAgo } from './datetime'
import { useAsync } from './useAsync'
import { totalRecalls, writeActivity } from './memoryCharts'
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
  const turnCount = sessions.state.status === 'ready' ? totalTurns(sessionRows) : null
  const supersededCount =
    facts.state.status === 'ready' && factCount !== null ? factRows.length - factCount : null
  const recallCount = facts.state.status === 'ready' ? totalRecalls(factRows) : null

  const profileData = profile.state.status === 'ready' ? profile.state.data.data : {}
  const highlights = summaryHighlights(profileData)
  const lastActive = sessionRows
    .map((s) => s.last_active_at)
    .filter((t): t is string => t != null)
    .sort()
    .at(-1)
  const lastSeen = lastActive ? formatAgo(lastActive) : null
  const growth = factGrowthSeries(writeRows)
  const activity = writeActivity(writeRows)

  return (
    <div className="space-y-6">
      {/* Row 1 — the summary hero beside the one genuine event stream in this
          portal: `GET /memory/writes` carries an op and a ts per row. */}
      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="min-w-0 lg:col-span-2">
          <CardBody>
            <SubjectSummary
              subjectLabel={subject}
              highlights={highlights}
              lastSeen={lastSeen}
              // Facts and Sessions are the KPI band's, at the top of the screen.
              // These three are measured off the detail reads and appear nowhere
              // else: how much was said, what belief was retired, how often the
              // agent has actually reached for any of it.
              stats={[
                { label: 'Turns', value: turnCount },
                { label: 'Superseded', value: supersededCount },
                { label: 'Recalls', value: recallCount },
              ]}
              trend={growth}
              loading={profile.state.status === 'loading'}
            />
          </CardBody>
        </Card>

        <Card className="min-w-0 lg:col-span-3">
          <CardHeader eyebrow="writes per day" title="What memory did" as="h3" />
          <CardBody className="space-y-3 pt-4">
            {writes.state.status === 'loading' && <LoadingRow label="Loading write log…" />}
            {writes.state.status === 'error' && <ErrorRow message={writes.state.message} />}
            {writes.state.status === 'ready' && activity.series.length === 0 && (
              <SceneState name="noChart">
                <p className="text-sm font-medium text-foreground">Nothing written yet</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Every learn, update and retirement lands here.
                </p>
              </SceneState>
            )}
            {activity.series.length > 0 && (
              <>
                {/*
                  **A bar per day, not an area.** Daily write counts are sparse
                  discrete events, and an area interpolates between them — on a
                  log whose entries all landed on one day it drew a thin spike at
                  the right edge of an empty plot, which reads as a broken chart
                  rather than as a young record. Bars say "one busy day in this
                  window" without claiming a continuum, and `writeActivity` now
                  draws only the days the record actually covers.
                */}
                <div className="min-w-0">
                  <BarChart
                    allowDecimals={false}
                    data={activity.rows}
                    index="day"
                    category="total"
                    color="graph"
                    height={200}
                    valueFormatter={(v) => `${v} ${v === 1 ? 'write' : 'writes'}`}
                  />
                </div>
                {/* The op composition the stack used to carry, as a legend line —
                    the same tallies, in a fraction of the height. */}
                <ul className="flex flex-wrap items-center gap-x-3 gap-y-1">
                  {activity.series.map((s) => (
                    <li key={s.key} className="flex min-w-0 items-center gap-1.5 text-[0.72rem]">
                      <span className="truncate text-muted-foreground">{s.label}</span>
                      <Figure className="text-[0.72rem] leading-4 font-medium text-foreground">
                        {s.value}
                      </Figure>
                    </li>
                  ))}
                </ul>
                {/* The receipt states the window that was actually drawn, and says
                    so when anything fell outside it. The range is measured, never
                    padded out to a round fortnight. */}
                <Receipt
                  origin="GET /memory/writes"
                  detail={[
                    `${activity.plotted} ${activity.plotted === 1 ? 'write' : 'writes'}`,
                    activity.window,
                    activity.omitted > 0 ? `${activity.omitted} older not drawn` : null,
                  ]
                    .filter((part): part is string => part !== null)
                    .join(' · ')}
                />
              </>
            )}
          </CardBody>
        </Card>
      </div>

      {/* Rows 2 and 3 share one 3 + 2 column rhythm so the vertical seam lines
          up, and `items-start` keeps every card at its own content height
          instead of padding the shorter one out with dead space. */}
      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="min-w-0 lg:col-span-3">
          <CardBody>
            <SemanticFactsPanel state={facts.state} />
          </CardBody>
        </Card>
        <Card className="min-w-0 lg:col-span-2">
          <CardBody>
            <StructuredProfilePanel state={profile.state} />
          </CardBody>
        </Card>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="min-w-0 lg:col-span-3">
          <CardBody>
            <div className="flex flex-col gap-3">
              <PanelHeader
                icon={MessagesSquare}
                title="Sessions"
                tint="bg-blue-200/12"
                ink="text-blue-700"
                info="Past conversations with this subject, each with a running summary. Expand a row for its transcript."
              />
              {sessions.state.status === 'loading' && <LoadingRow label="Loading sessions…" />}
              {sessions.state.status === 'error' && (
                <ErrorRow message={sessions.state.message} />
              )}
              {sessions.state.status === 'ready' && (
                <EpisodicSessionsPanel token={token} sessions={sessionRows} />
              )}
            </div>
          </CardBody>
        </Card>
        <Card className="min-w-0 lg:col-span-2">
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
