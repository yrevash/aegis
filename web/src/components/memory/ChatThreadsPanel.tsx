'use client'

import { MessagesSquare, Pencil, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState, type ReactElement } from 'react'

import { Button } from '@/components/primitives/button'
import {
  deleteSession,
  getSessionMessages,
  getSessions,
  renameSession,
  type ChatMessageRow,
  type ChatSessionRow,
} from '@/lib/api/console'

import { formatAgo } from './datetime'
import { PanelHeader } from './PanelHeader'
import { EmptyRow, ErrorRow, LoadingRow } from './StateRow'

/**
 * The caller's own chat threads and their transcripts.
 *
 * It belongs on the memory control plane rather than beside it: a chat session and a
 * `memory_session` are **the same id**, so this panel is the other half of the record
 * the rest of this screen inspects. "Here is what was said" sits next to "here is what
 * was remembered", and a recall that looks wrong can be checked against the turns that
 * produced it without leaving the page.
 *
 * The transcript is read-only by design. `POST /query` is the only writer of
 * `chat_messages` — a client that could post its own turns could post ones that never
 * happened — so what a person may do here is rename a thread and delete one.
 */
export function ChatThreadsPanel({ token }: { token: string | null }): ReactElement {
  const [sessions, setSessions] = useState<ChatSessionRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatMessageRow[] | null>(null)

  const load = useCallback((): void => {
    getSessions(token)
      .then((data) => {
        setSessions(data.rows)
        setError(null)
      })
      .catch((err: unknown) => {
        setError(err instanceof Error ? err.message : 'Is the backend running?')
      })
  }, [token])

  useEffect(() => {
    load()
  }, [load])

  const open = (id: string): void => {
    if (openId === id) {
      setOpenId(null)
      setTurns(null)
      return
    }
    setOpenId(id)
    setTurns(null)
    getSessionMessages(token, id)
      .then((data) => setTurns(data.rows))
      .catch(() => setTurns([]))
  }

  const rename = (row: ChatSessionRow): void => {
    const next = window.prompt('Rename this conversation', row.title)
    if (next == null || next.trim() === '' || next.trim() === row.title) return
    void renameSession(token, row.id, next.trim()).then(load).catch(load)
  }

  const remove = (row: ChatSessionRow): void => {
    if (!window.confirm(`Delete “${row.title}”? Its turns go with it.`)) return
    void deleteSession(token, row.id)
      .then(() => {
        if (openId === row.id) {
          setOpenId(null)
          setTurns(null)
        }
        load()
      })
      .catch(load)
  }

  return (
    <div className="flex h-full flex-col gap-3">
      <PanelHeader
        icon={MessagesSquare}
        title="Chats"
        tint="bg-blue-200/12"
        ink="text-blue-700"
        info="Your own console conversations. A chat's id is also its memory_session id, so the transcript here and the recall on this page describe the same thread. Turns are written by the run that produced them and cannot be edited."
        right={
          sessions != null ? (
            <span className="eyebrow text-muted-foreground">{sessions.length}</span>
          ) : null
        }
      />

      {error != null && <ErrorRow message={error} />}
      {error == null && sessions == null && <LoadingRow label="Loading chats…" />}
      {error == null && sessions != null && sessions.length === 0 && (
        <EmptyRow>
          No conversations yet. Ask something in the Console and it will appear here.
        </EmptyRow>
      )}

      {sessions != null && sessions.length > 0 && (
        <ul className="divide-y divide-border rounded-xl border border-border">
          {sessions.map((row) => (
            <li key={row.id} className="px-3 py-2.5">
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => open(row.id)}
                  className="min-w-0 flex-1 text-left"
                  aria-expanded={openId === row.id}
                >
                  <span className="block truncate text-sm text-foreground">{row.title}</span>
                  <span className="block font-mono text-[0.68rem] text-muted-foreground">
                    {row.last_active_at != null ? formatAgo(row.last_active_at) : '—'} ·{' '}
                    {row.id.slice(0, 8)}
                  </span>
                </button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Rename ${row.title}`}
                  onClick={() => rename(row)}
                >
                  <Pencil className="size-3.5" />
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  aria-label={`Delete ${row.title}`}
                  onClick={() => remove(row)}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>

              {openId === row.id && (
                <div className="mt-2 space-y-2 border-t border-border/70 pt-2">
                  {turns == null && <LoadingRow label="Loading transcript…" />}
                  {turns != null && turns.length === 0 && (
                    <EmptyRow>
                      No turns recorded for this conversation.
                    </EmptyRow>
                  )}
                  {turns?.map((turn) => (
                    <div key={turn.turn_index} className="text-sm">
                      <span className="eyebrow mr-2 text-muted-foreground">{turn.role}</span>
                      <span className="text-foreground">{turn.content}</span>
                    </div>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
