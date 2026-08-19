'use client'

/**
 * React hook that drives a query run end to end.
 *
 * Opens the SSE run, feeds every event through the pure {@link runReducer}, and
 * exposes actions to start a run, resolve the human approval gate, and reset. The
 * whole console reads from the single {@link RunState} it returns.
 *
 * It also owns the run's **conversation**. `POST /query` has always accepted a
 * `session_id`, and both memory nodes return early without one — so a console that
 * never sent it recalled nothing and persisted nothing on every live run. Making the
 * hook responsible for the id, rather than each call site, is what stops that being
 * possible again: a surface that wants a single-shot run has to say so.
 */

import { useCallback, useEffect, useRef, useState, useReducer } from 'react'

import { createSession, uploadAttachment, type AttachmentResponse } from '@/lib/api/console'
import { startRun } from '@/lib/api/liveTransport'
import type { RunController } from '@/lib/api/transport'
import type { ApprovalDecision } from '@/lib/api/types'
import type { StreamEvent } from '@/lib/stream'

import { initialRunState, runReducer, type RunState } from './runReducer'

type Action =
  | { kind: 'event'; event: StreamEvent }
  /** Begin a run: clear prior state and mark it active immediately. */
  | { kind: 'start' }
  /** The transport closed; ensure the run is no longer marked active. */
  | { kind: 'closed' }
  | { kind: 'reset' }

function reducer(state: RunState, action: Action): RunState {
  switch (action.kind) {
    case 'reset':
      return initialRunState
    case 'start':
      return { ...initialRunState, running: true }
    case 'closed':
      // Idempotent: a normal run already cleared `running` on its terminal event.
      return state.running ? { ...state, running: false } : state
    case 'event':
      return runReducer(state, action.event)
  }
}

/** Actions and state returned by {@link useRunStream}. */
export interface UseRunStream {
  state: RunState
  /** Whether a run is currently active. */
  running: boolean
  /**
   * Start a new run for the given query/persona.
   *
   * `sessionId` decides the conversation, and its three values are three different
   * intentions:
   *
   * - **omitted** — the hook manages one, creating it on the first run and reusing it
   *   for every later turn. This is the default because a chat that forgets the
   *   previous turn is the defect, not the feature.
   * - a **string** — that conversation, chosen by the caller (a session rail picking a
   *   thread out of `GET /sessions`).
   * - **null** — deliberately single-shot. Memory stays inert, exactly as it did
   *   before this existed.
   */
  start: (
    query: string,
    persona: string | null,
    token: string | null,
    sessionId?: string | null,
  ) => void
  /** The conversation the last run belonged to, or null if none/single-shot. */
  sessionId: string | null
  /**
   * Screen an image through the attachment rails and keep it for this run.
   *
   * Resolves to the screened descriptor — including a `blocked: true` one, which is a
   * verdict rather than a failure and is meant to be rendered as a guardrail chip.
   */
  attach: (
    imageBase64: string,
    options: { mimeType?: string; question?: string; filename?: string | null },
    token: string | null,
  ) => Promise<AttachmentResponse>
  /** Attachments screened for the current run; cleared by {@link reset}. */
  attachments: AttachmentResponse[]
  /** Approve or reject the active gate. */
  resolveApproval: (decision: ApprovalDecision) => void
  /** Abort any active run and clear state. */
  reset: () => void
}

/** Drive a single query run and expose its reduced state. */
export function useRunStream(): UseRunStream {
  const [state, dispatch] = useReducer(reducer, initialRunState)
  const controllerRef = useRef<RunController | null>(null)
  const approvalIdRef = useRef<string | null>(null)
  const sessionRef = useRef<string | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [attachments, setAttachments] = useState<AttachmentResponse[]>([])

  const reset = useCallback((): void => {
    controllerRef.current?.abort()
    controllerRef.current = null
    approvalIdRef.current = null
    setAttachments([])
    dispatch({ kind: 'reset' })
  }, [])

  const start = useCallback(
    (
      query: string,
      persona: string | null,
      token: string | null,
      sessionArg?: string | null,
    ): void => {
      controllerRef.current?.abort()
      approvalIdRef.current = null
      // Mark the run active up front so the UI locks immediately, before the
      // first `run_started` event arrives.
      dispatch({ kind: 'start' })

      const open = (resolved: string | null): void => {
        sessionRef.current = resolved
        setSessionId(resolved)
        controllerRef.current = startRun({ query, persona, sessionId: resolved }, token, {
          onEvent: (event) => {
            if (event.type === 'approval_required') approvalIdRef.current = event.approval_id
            dispatch({ kind: 'event', event })
          },
          onError: (error) =>
            dispatch({
              kind: 'event',
              event: { type: 'error', run_id: 'error', seq: -1, message: error.message },
            }),
          // A transport can end without a terminal `run_finished` (e.g. the
          // connection drops); clear `running` so Run/Reset re-enable regardless.
          onClose: () => dispatch({ kind: 'closed' }),
        })
      }

      if (sessionArg !== undefined) {
        open(sessionArg)
        return
      }
      if (sessionRef.current !== null) {
        open(sessionRef.current)
        return
      }
      // First turn of a managed conversation: mint one, then run. A failure here is
      // **soft** on purpose — an unreachable or unprovisioned sessions endpoint
      // costs the run its memory, and taking the whole answer down over a thread
      // record would be the worse trade of the two.
      void createSession(token)
        .then((row) => open(row.id))
        .catch(() => open(null))
    },
    [],
  )

  const attach = useCallback(
    async (
      imageBase64: string,
      options: { mimeType?: string; question?: string; filename?: string | null },
      token: string | null,
    ): Promise<AttachmentResponse> => {
      const screened = await uploadAttachment(token, {
        image_base64: imageBase64,
        mime_type: options.mimeType ?? 'image/png',
        question: options.question ?? '',
        filename: options.filename ?? null,
      })
      setAttachments((prior) => [...prior, screened])
      return screened
    },
    [],
  )

  const resolveApproval = useCallback((decision: ApprovalDecision): void => {
    const id = approvalIdRef.current
    if (id === null) return
    controllerRef.current?.resolveApproval(id, decision)
    approvalIdRef.current = null
  }, [])

  useEffect(() => () => controllerRef.current?.abort(), [])

  return {
    state,
    running: state.running,
    start,
    sessionId,
    attach,
    attachments,
    resolveApproval,
    reset,
  }
}
