'use client'

/**
 * React hook that drives a query run end to end.
 *
 * Owns a {@link RunTransport} (mock or live), feeds every event through the pure
 * {@link runReducer}, and exposes actions to start a run, resolve the human
 * approval gate, and reset. The whole console reads from the single
 * {@link RunState} it returns.
 */

import { useCallback, useEffect, useReducer, useRef } from 'react'

import { createTransport } from '@/lib/api/factory'
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
  /** Start a new run for the given query/persona. */
  start: (query: string, persona: string | null, token: string | null) => void
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

  const reset = useCallback((): void => {
    controllerRef.current?.abort()
    controllerRef.current = null
    approvalIdRef.current = null
    dispatch({ kind: 'reset' })
  }, [])

  const start = useCallback(
    (query: string, persona: string | null, token: string | null): void => {
      controllerRef.current?.abort()
      approvalIdRef.current = null
      // Mark the run active up front so the UI locks immediately, before the
      // first `run_started` event arrives.
      dispatch({ kind: 'start' })
      // Create the transport per run so it reads the *resolved* live/mock mode at
      // run time — not a stale mode captured at mount, before the backend probe
      // settles (which could otherwise pin a live transport after an offline fallback).
      controllerRef.current = createTransport().start(query, persona, token, {
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

  return { state, running: state.running, start, resolveApproval, reset }
}