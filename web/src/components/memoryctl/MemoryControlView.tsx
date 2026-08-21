'use client'

import { Brain, Clock, MessagesSquare, Users } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { SceneState } from '@/components/illustration/Scene'
import { BackendGate } from '@/components/shared/BackendGate'
import { Card, CardBody } from '@/components/ui/Card'
import { StatCard } from '@/components/ui/StatCard'
import { PageHeader } from '@/components/primitives/PageHeader'
import { Receipt } from '@/components/primitives/Receipt'
import { ChatThreadsPanel } from '@/components/memory/ChatThreadsPanel'
import { formatAgo } from '@/components/memory/datetime'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { SubjectPanels } from '@/components/memory/SubjectRecord'
import { useAsync } from '@/components/memory/useAsync'
import { getMemorySubjects } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'
import { cn } from '@/lib/utils'

import { FactManager } from './FactManager'
import { ScopeLadder } from './ScopeLadder'
import { RetentionPanel } from './RetentionPanel'
import { SubjectPicker } from './SubjectPicker'

/**
 * Memory (§7.5) — see what is held about someone, correct it, delete it, stop keeping it.
 *
 * The read half of this screen already existed and shipped nothing you could act on: a
 * subject's facts, profile, sessions, write log and recall trace, behind a free-text box
 * you had to already know the key to use. **A memory screen you cannot correct is a
 * report**, and the product's largest measured gap was that its end-user had no write
 * action anywhere in it — not even about themselves.
 *
 * So this is the same record with the three missing verbs attached, and the whole of who
 * may do what to whom is one server-derived list:
 *
 * - a **client** sees exactly one subject, its own, and may write, correct and erase its
 *   own memory — the first durable relationship the role has with the product;
 * - a **tenant admin** sees its own tenant's people and may do the same for any of them,
 *   plus run the retention horizon across the tenant;
 * - a **platform admin** spans tenants, and the AI team keeps the deep record below for
 *   the diagnosis it was built for.
 *
 * Nobody reaches across a tenant, and the reason is not this file: every subject here
 * came from `GET /memory/subjects`, which builds the set from the caller's sealed
 * `TenantScope` through the `memory_subject_for` adapter seam. The browser never
 * composes a subject key, so there is nothing for a person to type that would widen it —
 * and the day the domain scopes memory to an account rather than a person, that seam
 * changes and this screen follows with no edit.
 */
function MemoryControl({ token }: { token: string | null }): ReactElement {
  // Bumped after every write, correction and deletion. Each panel reads it as a
  // dependency, so a corrected fact can never still be rendered as the old one by the
  // record below while the manager above shows the new one.
  const [version, setVersion] = useState(0)
  const [selected, setSelected] = useState<string | null>(null)
  const subjects = useAsync(() => getMemorySubjects(token), [token, version])

  const data = subjects.state.status === 'ready' ? subjects.state.data : null
  const rows = data?.rows ?? []
  const canManageOthers = data?.may_manage_others ?? false
  // The server names the caller's own subject; the browser does not work it out. Kept as
  // a plain string so the effect below depends on a value rather than on a fresh array.
  const initial = data ? (data.self_subject ?? data.rows[0]?.subject ?? null) : null

  // Default to the caller's own record: the common case is a person looking at
  // themselves, and an administrator lands somewhere real rather than on a picker.
  useEffect(() => {
    if (selected === null && initial !== null) setSelected(initial)
  }, [initial, selected])

  const current = rows.find((r) => r.subject === selected) ?? null
  const bump = (): void => setVersion((v) => v + 1)

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="long-term memory · Postgres + Qdrant"
        title="Memory"
      />

      {/* The record's own size, led with. Every figure is a field off the same
          response the picker below renders — one receipt covers all four. */}
      {current !== null && (
        <div>
          <div
            className={cn(
              'grid grid-cols-1 gap-4 sm:grid-cols-2',
              canManageOthers ? 'xl:grid-cols-4' : 'xl:grid-cols-3',
            )}
          >
            <StatCard
              label="Facts held"
              value={String(current.fact_count)}
              icon={Brain}
              tone="graph"
            />
            <StatCard
              label="Sessions"
              value={String(current.session_count)}
              icon={MessagesSquare}
              tone="agent"
            />
            <StatCard
              label="Last active"
              value={current.last_active === null ? 'never' : formatAgo(current.last_active)}
              icon={Clock}
              tone="neutral"
            />
            {canManageOthers && (
              <StatCard
                label="Subjects in reach"
                value={String(rows.length)}
                icon={Users}
                tone="ml"
              />
            )}
          </div>
          <Receipt origin="GET /memory/subjects" className="mt-4" />
        </div>
      )}

      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="min-w-0 lg:col-span-2">
          <CardBody>
            <div className="flex flex-col gap-5">
              <div className="flex flex-col gap-3">
                <h2 className="t-title text-foreground">
                  {canManageOthers ? 'Whose memory' : 'Your record'}
                </h2>
                {subjects.state.status === 'loading' && <LoadingRow label="Loading subjects…" />}
                {subjects.state.status === 'error' && (
                  <ErrorRow message={subjects.state.message} />
                )}
                {subjects.state.status === 'ready' && (
                  <SubjectPicker rows={rows} selected={selected} onSelect={setSelected} />
                )}
              </div>
              {/*
                The reach ladder, under the picker rather than on its own screen: the
                question "who else can see this?" is asked while looking at the record,
                and every rung is a field off the same response the picker renders.
              */}
              {subjects.state.status === 'ready' && selected !== null && (
                <ScopeLadder
                  rows={rows}
                  selected={selected}
                  canManageOthers={canManageOthers}
                />
              )}
            </div>
          </CardBody>
        </Card>

        <Card className="min-w-0 lg:col-span-3">
          <CardBody>
            {selected === null ? (
              <SceneState name="subjects" size="md" className="py-6">
                <p className="text-sm font-medium text-foreground">No subject chosen</p>
                <p className="mt-1 text-sm text-muted-foreground">
                  Pick one to read, correct and erase its memory.
                </p>
              </SceneState>
            ) : (
              <FactManager
                token={token}
                subject={selected}
                refreshKey={version}
                onChanged={bump}
              />
            )}
          </CardBody>
        </Card>
      </div>

      {selected !== null && (
        <Card>
          <CardBody>
            <RetentionPanel
              token={token}
              subject={selected}
              subjectRow={current}
              canSweep={canManageOthers}
              refreshKey={version}
              onChanged={bump}
            />
          </CardBody>
        </Card>
      )}

      {/* The caller's own chat threads. Not gated on the subject: a chat is keyed by the
          person who had it, and it is how an operator finds a session worth inspecting. */}
      <Card>
        <CardBody>
          <ChatThreadsPanel token={token} />
        </CardBody>
      </Card>

      {/* The full record — facts with their belief history, the structured profile, the
          sessions, the write log and the recall trace. Underneath the controls on
          purpose: the evidence for a correction and the button that makes it belong on
          one screen. */}
      {selected !== null && (
        <SubjectPanels token={token} subject={selected} refreshKey={version} />
      )}
    </div>
  )
}

/** Client entry for the Memory section — gated on a reachable backend. */
export function MemoryControlMount(): ReactElement {
  // The `/memory/*` routes are RBAC-scoped: hand the view the real session bearer, and
  // hold it back until the persisted session has been restored.
  const { session, hydrated } = useAuth()

  if (!hydrated) {
    return (
      <div
        role="status"
        className="flex min-h-[240px] items-center justify-center rounded-lg border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground"
      >
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
        <MemoryControl token={session?.token ?? null} />
    </BackendGate>
  )
}
