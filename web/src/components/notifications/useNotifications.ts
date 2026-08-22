'use client'

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from 'react'

import { errorSentence } from '@/lib/api/apiError'
import {
  getNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  subscribeNotifications,
  type NotificationRow,
  type StreamStatus,
} from '@/lib/api/notifications'
import { useAuth } from '@/lib/auth/AuthContext'

import { chimeEnabled, persistChime, playChime } from './chime'
import {
  EMPTY_NOTIFICATIONS,
  notificationReducer,
  type NotificationState,
} from './notificationState'

/** What the bell renders from. */
export interface NotificationFeed extends NotificationState {
  /** Where the live socket is. */
  status: StreamStatus
  /** The server's own sentence when the first read failed, else null. */
  error: string | null
  /** True until the first read of `GET /notifications` settles either way. */
  loading: boolean
  /** Mark one row read — optimistically, then reconciled against the server. */
  markRead: (id: string) => void
  /** Mark every row read. */
  markAllRead: () => void
  /** Whether the optional sound is on for this browser. */
  sound: boolean
  /** Turn the sound on or off; the choice persists. */
  setSound: (on: boolean) => void
}

/** How many rows the panel asks for. Deeper history is the audit trail's job. */
const PAGE = 30

/**
 * The bell's data: one read of the feed, one live socket, and the arithmetic in
 * {@link notificationReducer}.
 *
 * **The socket's lifetime is the session's.** It opens when a bearer exists and is
 * closed — with any pending retry cancelled — the moment the session goes away, so a
 * signed-out console holds no connection and cannot be handed rows it may no longer
 * read. `signOut` clears the token in `AuthContext`, this effect's cleanup runs, and
 * the feed is emptied rather than left on screen behind a login redirect.
 *
 * **A missing backend is a state, not a crash.** The endpoints may not be deployed;
 * the read then fails with the server's own 404 sentence, the badge shows nothing, and
 * the panel says what it knows. Nothing is invented to fill it.
 */
export function useNotifications(): NotificationFeed {
  const { session, hydrated } = useAuth()
  const token = session?.token ?? null
  const [state, dispatch] = useReducer(notificationReducer, EMPTY_NOTIFICATIONS)
  const [status, setStatus] = useState<StreamStatus>('closed')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [sound, setSoundState] = useState(false)
  // Read in an effect, not in the initializer: this hook renders on the server too,
  // and reading storage during render is the hydration mismatch that costs a screen.
  useEffect(() => {
    setSoundState(chimeEnabled(typeof window === 'undefined' ? null : window.localStorage))
  }, [])

  const soundRef = useRef(sound)
  soundRef.current = sound

  const reload = useCallback(
    (bearer: string) => {
      setLoading(true)
      return getNotifications(bearer, { limit: PAGE })
        .then((data) => {
          dispatch({ type: 'loaded', rows: data.rows, unread: data.unread })
          setError(null)
        })
        .catch((failure: unknown) => setError(errorSentence(failure)))
        .finally(() => setLoading(false))
    },
    [dispatch],
  )

  useEffect(() => {
    // Wait for the persisted session; firing now would send no bearer and 401.
    if (!hydrated) return
    if (token === null) {
      dispatch({ type: 'cleared' })
      setStatus('closed')
      setError(null)
      return
    }
    let alive = true
    void reload(token)
    const subscription = subscribeNotifications(token, {
      onNotification: (row: NotificationRow) => {
        if (!alive) return
        dispatch({ type: 'arrived', row })
        if (row.read_at === null) playChime(soundRef.current)
      },
      onStatus: (next) => {
        if (alive) setStatus(next)
      },
    })
    return () => {
      alive = false
      subscription.close()
    }
  }, [token, hydrated, reload])

  const markRead = useCallback(
    (id: string) => {
      // Optimistic, because the commonest reason to click a row is to open what it
      // points at, and a badge that lags a navigation reads as broken. The server is
      // still the authority: a refusal re-reads the feed, so the count on screen is
      // never a claim the backend has not agreed to.
      dispatch({ type: 'read', id, at: new Date().toISOString() })
      markNotificationRead(token, id).catch((failure: unknown) => {
        setError(errorSentence(failure))
        if (token !== null) void reload(token)
      })
    },
    [token, reload],
  )

  const markAllRead = useCallback(() => {
    dispatch({ type: 'readAll', at: new Date().toISOString() })
    markAllNotificationsRead(token).catch((failure: unknown) => {
      setError(errorSentence(failure))
      if (token !== null) void reload(token)
    })
  }, [token, reload])

  const setSound = useCallback((on: boolean) => {
    setSoundState(on)
    persistChime(typeof window === 'undefined' ? null : window.localStorage, on)
  }, [])

  return useMemo(
    () => ({ ...state, status, error, loading, markRead, markAllRead, sound, setSound }),
    [state, status, error, loading, markRead, markAllRead, sound, setSound],
  )
}
