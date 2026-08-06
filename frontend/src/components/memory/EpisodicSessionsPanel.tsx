import { ChevronDown, History, MessagesSquare } from 'lucide-react'
import { useState, type ReactElement } from 'react'

import { getMemorySessionMessages } from '@/api/client'
import { useAsync } from '@/components/admin/useAsync'
import { LoadingRow } from '@/components/common/StateRow'
import { Badge } from '@/components/ui/badge'
import { formatAgo } from '@/lib/datetime'
import { cn } from '@/lib/utils'
import type { MemorySessionRow } from '@/types/memory'

/** Tint a message by which side / origin produced it. */
function roleTint(role: string): string {
  switch (role) {
    case 'user':
      return 'text-foreground'
    case 'tool':
      return 'text-graph-ink'
    case 'assistant':
      return 'text-agent-ink'
    default:
      return 'text-muted-foreground'
  }
}

/** The lazily-loaded message transcript for one expanded session. */
function SessionMessages({ token, sessionId }: { token: string | null; sessionId: string }): ReactElement {
  const { state } = useAsync(() => getMemorySessionMessages(token, sessionId), [token, sessionId])
  if (state.status === 'loading') return <LoadingRow label="Loading transcript…" />
  if (state.status === 'error')
    return <p className="py-3 text-xs text-destructive">Could not load transcript. {state.message}</p>
  if (state.data.rows.length === 0)
    return <p className="py-3 text-xs text-muted-foreground">No messages recorded.</p>
  return (
    <ol className="mt-1 space-y-2 border-l border-border/70 pl-3">
      {state.data.rows.map((m) => (
        <li key={m.id} className="text-xs leading-snug">
          <span className={cn('mr-1.5 font-mono text-[0.6rem] uppercase tracking-wide', roleTint(m.role))}>
            {m.role}
          </span>
          <span className="text-muted-foreground">{m.content}</span>
        </li>
      ))}
    </ol>
  )
}

/** One collapsible episodic-session row with its running summary. */
function SessionRow({ token, session }: { token: string | null; session: MemorySessionRow }): ReactElement {
  const [open, setOpen] = useState(false)
  return (
    <li className="rounded-lg border border-border bg-card">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-2.5 rounded-lg px-3.5 py-3 text-left transition-colors hover:bg-surface-2/50"
      >
        <span className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-md bg-agent/12">
          <MessagesSquare className="size-3.5 text-agent-ink" />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2">
            <span className="font-mono text-[0.72rem] text-foreground">{session.id}</span>
            {session.persona && (
              <Badge variant="secondary" className="text-[0.58rem]">
                {session.persona}
              </Badge>
            )}
            <span className="eyebrow ml-auto text-[0.56rem]">{formatAgo(session.last_active_at)}</span>
          </span>
          <span className="mt-1 block text-xs leading-snug text-muted-foreground">
            {session.summary ?? 'No running summary yet.'}
          </span>
          <span className="mt-1.5 block font-mono text-[0.6rem] text-muted-foreground/80">
            {session.turn_count} turns
          </span>
        </span>
        <ChevronDown className={cn('mt-0.5 size-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')} />
      </button>
      {open && <div className="px-3.5 pb-3.5">{<SessionMessages token={token} sessionId={session.id} />}</div>}
    </li>
  )
}

interface Props {
  token: string | null
  sessions: MemorySessionRow[]
}

/**
 * "Sessions" (§4.3) — the past conversations with this subject, each with a
 * running summary and a lazily-fetched transcript. (The honest store underneath
 * is the episodic memory tier.)
 */
export function EpisodicSessionsPanel({ token, sessions }: Props): ReactElement {
  if (sessions.length === 0) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <History className="size-4" /> No past sessions recorded for this subject yet.
      </div>
    )
  }
  return (
    <ul className="flex flex-col gap-2.5">
      {sessions.map((s) => (
        <SessionRow key={s.id} token={token} session={s} />
      ))}
    </ul>
  )
}
