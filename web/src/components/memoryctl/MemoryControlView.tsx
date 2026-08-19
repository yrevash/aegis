'use client'

import { Brain } from 'lucide-react'
import { useEffect, useState, type ReactElement } from 'react'

import { BackendGate } from '@/components/shared/BackendGate'
import { Card, CardBody } from '@/components/ui/Card'
import { TooltipProvider } from '@/components/primitives/tooltip'
import { ChatThreadsPanel } from '@/components/memory/ChatThreadsPanel'
import { ErrorRow, LoadingRow } from '@/components/memory/StateRow'
import { SubjectPanels } from '@/components/memory/SubjectRecord'
import { useAsync } from '@/components/memory/useAsync'
import { getMemorySubjects } from '@/lib/api/client'
import { useAuth } from '@/lib/auth/AuthContext'

import { FactManager } from './FactManager'
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
      <div>
        <p className="eyebrow mb-1">long-term memory · Postgres + embedded Chroma</p>
        <h1 className="t-hero text-foreground">Memory</h1>
      </div>

      <div className="grid items-start gap-6 lg:grid-cols-5">
        <Card className="lg:col-span-2">
          <CardBody>
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
          </CardBody>
        </Card>

        <Card className="lg:col-span-3">
          <CardBody>
            {selected === null ? (
              <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
                <Brain className="size-7 text-muted-foreground/50" aria-hidden />
                <p className="max-w-md text-sm text-muted-foreground">
                  Choose a subject to see what the agent has learned, correct anything it
                  has wrong, and decide what it keeps.
                </p>
              </div>
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
      <div className="flex min-h-[420px] items-center justify-center rounded-2xl border border-dashed border-border bg-surface-2/40 text-sm text-muted-foreground">
        Connecting…
      </div>
    )
  }

  return (
    <BackendGate>
      <TooltipProvider>
        <MemoryControl token={session?.token ?? null} />
      </TooltipProvider>
    </BackendGate>
  )
}
