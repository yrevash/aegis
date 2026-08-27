'use client'

import { Check, Loader2, MessagesSquare, Pencil, Trash2 } from 'lucide-react'
import { useCallback, useEffect, useRef, useState, type ReactElement } from 'react'

import { SceneState } from '@/components/illustration/Scene'
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
 *
 * **Both of those controls used to go through `window.confirm` / `window.prompt`, and
 * both were therefore inert.** A native dialog is not this codebase's confirmation: it
 * is suppressed outright by an automated browser and by Chrome's "prevent this page
 * from creating additional dialogs", and in both cases the call returns *false* — so
 * Delete fired no request and showed nothing, which is exactly how it was found. It
 * also carries none of the things a destructive control here owes a reader: it cannot
 * name the row in the page's own voice, it cannot be focus-managed, and it cannot be
 * styled to the light theme. Both now use the inline two-step confirmation the rest of
 * the memory control plane uses (`memoryctl/FactManager`, `memoryctl/RetentionPanel`):
 * a sentence naming the thread, a destructive button, and "Keep it".
 */
export function ChatThreadsPanel({ token }: { token: string | null }): ReactElement {
  const [sessions, setSessions] = useState<ChatSessionRow[] | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [openId, setOpenId] = useState<string | null>(null)
  const [turns, setTurns] = useState<ChatMessageRow[] | null>(null)
  /** The thread whose delete is armed — at most one, like the fact list's. */
  const [confirmId, setConfirmId] = useState<string | null>(null)
  /** The thread being retitled, and the title being typed. */
  const [renamingId, setRenamingId] = useState<string | null>(null)
  const [renameText, setRenameText] = useState('')
  /** The thread a request is in flight for, so only its own controls go busy. */
  const [busyId, setBusyId] = useState<string | null>(null)
  const [failure, setFailure] = useState<string | null>(null)
  /** Focus lands on the destructive button when the confirmation opens, not on the
      row behind it — the confirmation is what the keyboard is now aimed at. */
  const confirmRef = useRef<HTMLButtonElement>(null)
  const renameRef = useRef<HTMLInputElement>(null)

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

  useEffect(() => {
    if (confirmId != null) confirmRef.current?.focus()
  }, [confirmId])

  useEffect(() => {
    if (renamingId != null) renameRef.current?.select()
  }, [renamingId])

  const rename = async (row: ChatSessionRow): Promise<void> => {
    const next = renameText.trim()
    if (next === '' || next === row.title) {
      setRenamingId(null)
      return
    }
    setBusyId(row.id)
    setFailure(null)
    try {
      await renameSession(token, row.id, next)
      setRenamingId(null)
      load()
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : 'The rename did not go through.')
    } finally {
      setBusyId(null)
    }
  }

  const remove = async (row: ChatSessionRow): Promise<void> => {
    setBusyId(row.id)
    setFailure(null)
    try {
      await deleteSession(token, row.id)
      setConfirmId(null)
      if (openId === row.id) {
        setOpenId(null)
        setTurns(null)
      }
      // The list is re-read rather than spliced: the server decides what is left.
      load()
    } catch (err: unknown) {
      setFailure(err instanceof Error ? err.message : 'The delete did not go through.')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="flex flex-col gap-3">
      <PanelHeader
        icon={MessagesSquare}
        title="Chats"
        tint="bg-blue-200/12"
        ink="text-blue-700"
        info="A chat's id is also its memory_session id, so this transcript and the recall on this page describe the same thread. Turns are written by the run that produced them and cannot be edited."
        right={
          sessions != null ? (
            <span className="eyebrow text-muted-foreground">{sessions.length}</span>
          ) : null
        }
      />

      {error != null && <ErrorRow message={error} />}
      {error == null && sessions == null && <LoadingRow label="Loading chats…" />}
      {error == null && sessions != null && sessions.length === 0 && (
        <SceneState name="assistant" size="md" className="py-4">
          <p className="text-sm font-medium text-foreground">No conversations yet</p>
          <p className="mt-1 text-sm text-muted-foreground">Ask something in the Console.</p>
        </SceneState>
      )}

      {sessions != null && sessions.length > 0 && (
        <ul
          className="relative min-w-0 divide-y divide-border overflow-y-auto overscroll-contain rounded-lg border border-border"
          style={{ maxHeight: 420 }}
          tabIndex={0}
          role="group"
          aria-label="Chats"
        >
          {sessions.map((row) => (
            <li key={row.id} className="px-3 py-2.5">
              {renamingId === row.id ? (
                <div className="flex min-w-0 flex-wrap items-center gap-2">
                  <label htmlFor={`rename-${row.id}`} className="sr-only">
                    New title
                  </label>
                  <input
                    ref={renameRef}
                    id={`rename-${row.id}`}
                    value={renameText}
                    autoComplete="off"
                    onChange={(e) => setRenameText(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void rename(row)
                      if (e.key === 'Escape') setRenamingId(null)
                    }}
                    className="h-8 min-w-0 flex-1 basis-40 rounded-md border border-border bg-card px-2 text-sm text-foreground outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  />
                  <Button
                    type="button"
                    size="sm"
                    disabled={busyId === row.id || renameText.trim() === ''}
                    onClick={() => void rename(row)}
                  >
                    {busyId === row.id ? (
                      <Loader2
                        className="size-3.5 animate-spin motion-reduce:animate-none"
                        aria-hidden
                      />
                    ) : (
                      <Check className="size-3.5" aria-hidden />
                    )}
                    Save
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setRenamingId(null)}
                  >
                    Cancel
                  </Button>
                </div>
              ) : confirmId === row.id ? (
                <div className="flex min-w-0 flex-col gap-2">
                  <p role="alert" className="text-sm break-words text-foreground">
                    Delete “{row.title}”? Its turns and its memory session go with it.
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button
                      ref={confirmRef}
                      type="button"
                      size="sm"
                      variant="destructive"
                      disabled={busyId === row.id}
                      onClick={() => void remove(row)}
                    >
                      {busyId === row.id ? (
                        <Loader2
                          className="size-3.5 animate-spin motion-reduce:animate-none"
                          aria-hidden
                        />
                      ) : (
                        <Trash2 className="size-3.5" aria-hidden />
                      )}
                      {busyId === row.id ? 'Deleting…' : 'Delete permanently'}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      onClick={() => setConfirmId(null)}
                    >
                      Keep it
                    </Button>
                  </div>
                </div>
              ) : (
                <div className="flex min-w-0 items-center gap-2">
                  <button
                    type="button"
                    onClick={() => open(row.id)}
                    className="min-w-0 flex-1 rounded-md text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-expanded={openId === row.id}
                  >
                    <span className="block truncate text-sm text-foreground">{row.title}</span>
                    <span
                      translate="no"
                      className="tabular block truncate font-mono text-[0.6875rem] text-muted-foreground"
                    >
                      {row.last_active_at != null ? formatAgo(row.last_active_at) : '—'} ·{' '}
                      {row.id.slice(0, 8)}
                    </span>
                  </button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Rename ${row.title}`}
                    onClick={() => {
                      setRenameText(row.title)
                      setRenamingId(row.id)
                      setConfirmId(null)
                    }}
                  >
                    <Pencil className="size-3.5" aria-hidden />
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Delete ${row.title}`}
                    onClick={() => {
                      setConfirmId(row.id)
                      setRenamingId(null)
                    }}
                  >
                    <Trash2 className="size-3.5" aria-hidden />
                  </Button>
                </div>
              )}

              {openId === row.id && (
                <div className="mt-2 space-y-2 border-t border-border/70 pt-2">
                  {turns == null && <LoadingRow label="Loading transcript…" />}
                  {turns != null && turns.length === 0 && (
                    <EmptyRow>
                      No turns recorded for this conversation.
                    </EmptyRow>
                  )}
                  {turns?.map((turn) => (
                    <div key={turn.turn_index} className="min-w-0 text-sm break-words">
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

      {failure != null && (
        <p role="alert" className="text-sm break-words text-destructive">
          {failure}
        </p>
      )}
    </div>
  )
}
