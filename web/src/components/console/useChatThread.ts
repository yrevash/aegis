'use client'

/**
 * Drive a chat thread: many turns, one live run at a time.
 *
 * `useRunStream` holds exactly one {@link RunState} and stays the right hook for the
 * single-run surfaces (the RAG, graph, harness and simulation views). A chat needs a
 * list, so this hook owns a {@link ThreadState} instead and routes each event to the
 * turn that started it. Everything below the reducer is unchanged: the same `startRun`
 * transport, the same `runReducer`.
 *
 * The conversation id comes from the server. `createSession` mints it on the first
 * question of a chat — lazily, so a chat nobody sends never becomes a stored row — and
 * every later turn of that chat carries the same id, which is what makes the memory
 * nodes recall instead of pass through. A failure to mint one is **soft**: the run still
 * happens, without memory, because losing the answer over a thread record is the worse
 * trade.
 *
 * One run at a time is deliberate. A second question while the first is streaming would
 * interleave two event streams into one thread with no way to tell them apart, so the
 * composer locks while a turn is live rather than racing.
 */

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { createSession, getSessionMessages, getSessions } from '@/lib/api/console'
import { startRun } from '@/lib/api/liveTransport'
import type { RunController } from '@/lib/api/transport'
import type { ApprovalDecision } from '@/lib/api/types'

import { questionWithAttachment, type TurnAttachment } from './composerAttachment'
import { rememberChat, rememberedChat } from './lastChat'
import {
  activeSession,
  initialThreadState,
  liveTurn,
  localIdFor,
  threadReducer,
  type ChatSession,
  type RestoredTurn,
  type ThreadState,
  type Turn,
} from './threadReducer'

/** Monotonic suffix, so two ids minted in the same millisecond still differ. */
let counter = 0

/** A local key for a chat or a turn. */
function mintKey(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now().toString(36)}-${counter}`
}

/** Pair the stored transcript's user/assistant rows back into turns. */
function pairTranscript(
  rows: { turn_index: number; role: string; content: string }[],
): RestoredTurn[] {
  const ordered = [...rows].sort((a, b) => a.turn_index - b.turn_index)
  const turns: RestoredTurn[] = []
  for (const row of ordered) {
    if (row.role === 'user') {
      turns.push({ turnIndex: row.turn_index, question: row.content, answer: '' })
      continue
    }
    const open = turns.at(-1)
    // An assistant row with no question before it is a transcript we cannot pair;
    // show it as its own turn rather than dropping what was said.
    if (open === undefined || open.answer !== '') {
      turns.push({ turnIndex: row.turn_index, question: '', answer: row.content })
    } else {
      open.answer = row.content
    }
  }
  return turns
}

/** What the chat shell reads and calls. */
export interface UseChatThread {
  thread: ThreadState
  /** The chat currently shown. */
  session: ChatSession | null
  /** The turn whose run is streaming, or null. */
  live: Turn | null
  /** Whether a run is in flight (the composer is locked while it is). */
  running: boolean
  /** Send a question in the active chat, with whatever image was screened for it. */
  ask: (
    question: string,
    persona: string | null,
    attachment?: TurnAttachment | null,
  ) => void
  /** Open a new chat. */
  newChat: () => void
  /** Show an existing chat, loading its stored transcript the first time. */
  selectChat: (sessionId: string) => void
  /** Approve or reject the live turn's gate. */
  resolveApproval: (decision: ApprovalDecision) => void
  /** Whether the live gate has been answered (so the card can disable itself). */
  approvalResolved: boolean
}

/** Drive a chat thread end to end. */
export function useChatThread(token: string | null): UseChatThread {
  const [thread, dispatch] = useReducer(threadReducer, null, () =>
    initialThreadState(mintKey('chat'), Date.now()),
  )
  const controllerRef = useRef<RunController | null>(null)
  const approvalIdRef = useRef<string | null>(null)
  const [approvalResolved, setApprovalResolved] = useState(false)

  const session = useMemo(() => activeSession(thread), [thread])
  const live = useMemo(() => liveTurn(session), [session])
  const running = live !== null

  // The caller's stored conversations, so the rail is the real list and not just what
  // this tab happened to run. A failure leaves the rail with the current chat only.
  //
  // The same pass resumes the conversation this browser was last in. A reload used to
  // open an empty chat and leave the person's thread sitting in the rail looking
  // abandoned; the reducer only takes the resume when this tab is still untouched, so a
  // late-arriving sync never yanks somebody out of a chat they have already started.
  useEffect(() => {
    let alive = true
    const resumeServerId = rememberedChat()
    void getSessions(token)
      .then((response) => {
        if (!alive) return
        const rows = response.rows.map((row) => ({ id: row.id, title: row.title }))
        dispatch({ kind: 'sync_sessions', rows, at: Date.now(), resumeServerId })
        if (resumeServerId === null || !rows.some((row) => row.id === resumeServerId)) return
        // The resumed chat renders its stored transcript, exactly as it would if it had
        // been picked from the rail by hand.
        void getSessionMessages(token, resumeServerId)
          .then((messages) => {
            if (!alive) return
            dispatch({
              kind: 'restore',
              sessionId: localIdFor(resumeServerId),
              turns: pairTranscript(messages.rows),
            })
          })
          .catch(() => {
            /* The chat is open; its transcript simply did not come back. */
          })
      })
      .catch(() => {
        /* No stored conversations to show; the current chat still works. */
      })
    return () => {
      alive = false
    }
  }, [token])

  // Remember what is on screen, so the next load opens here. An unstarted chat is not
  // somewhere to come back to, so it clears the memory rather than pinning a blank.
  useEffect(() => {
    rememberChat(session?.serverId ?? null)
  }, [session?.serverId])

  const openRun = useCallback(
    (
      sessionKey: string,
      serverId: string | null,
      question: string,
      persona: string | null,
      attachment: TurnAttachment | null,
    ): void => {
      const turnId = mintKey('turn')
      dispatch({
        kind: 'ask',
        sessionId: sessionKey,
        turnId,
        question,
        attachment,
        at: Date.now(),
      })
      // The thread shows the question the person wrote; the run receives that question
      // plus the screened description of the image, which is the only form an
      // attachment takes on this wire. A refused image contributes nothing.
      const query = questionWithAttachment(question, attachment)
      controllerRef.current = startRun({ query, persona, sessionId: serverId }, token, {
        onEvent: (event) => {
          if (event.type === 'approval_required') approvalIdRef.current = event.approval_id
          dispatch({ kind: 'event', turnId, event })
        },
        onError: (error) =>
          dispatch({
            kind: 'event',
            turnId,
            event: { type: 'error', run_id: turnId, seq: -1, message: error.message },
          }),
        // A transport can end without a terminal event (a dropped connection); clear
        // `running` so the composer unlocks regardless.
        onClose: () => dispatch({ kind: 'closed', turnId }),
      })
    },
    [token],
  )

  const ask = useCallback(
    (
      question: string,
      persona: string | null,
      attachment: TurnAttachment | null = null,
    ): void => {
      const current = activeSession(thread)
      if (current === null) return
      approvalIdRef.current = null
      setApprovalResolved(false)

      if (current.serverId !== null) {
        openRun(current.id, current.serverId, question, persona, attachment)
        return
      }
      // First question of this chat: mint the conversation, then run in it.
      void createSession(token, question.slice(0, 80))
        .then((row) => {
          dispatch({ kind: 'bind_session', sessionId: current.id, serverId: row.id })
          openRun(current.id, row.id, question, persona, attachment)
        })
        .catch(() => openRun(current.id, null, question, persona, attachment))
    },
    [thread, openRun, token],
  )

  const newChat = useCallback((): void => {
    controllerRef.current?.abort()
    controllerRef.current = null
    dispatch({ kind: 'new_chat', sessionId: mintKey('chat'), at: Date.now() })
  }, [])

  const selectChat = useCallback(
    (sessionId: string): void => {
      dispatch({ kind: 'select_chat', sessionId })
      const target = thread.sessions.find((s) => s.id === sessionId)
      if (target === undefined || target.restoredLoaded || target.serverId === null) return
      void getSessionMessages(token, target.serverId)
        .then((response) =>
          dispatch({ kind: 'restore', sessionId, turns: pairTranscript(response.rows) }),
        )
        .catch(() => dispatch({ kind: 'restore', sessionId, turns: [] }))
    },
    [thread, token],
  )

  const resolveApproval = useCallback((decision: ApprovalDecision): void => {
    const id = approvalIdRef.current
    if (id === null) return
    setApprovalResolved(true)
    controllerRef.current?.resolveApproval(id, decision)
    approvalIdRef.current = null
  }, [])

  useEffect(() => () => controllerRef.current?.abort(), [])

  return {
    thread,
    session,
    live,
    running,
    ask,
    newChat,
    selectChat,
    resolveApproval,
    approvalResolved,
  }
}
