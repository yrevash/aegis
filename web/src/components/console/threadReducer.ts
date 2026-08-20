/**
 * The chat thread — many turns, each owning exactly one run.
 *
 * `runReducer` reduces one run's event stream into one {@link RunState}, and its shape
 * does not change here: a turn *holds* a `RunState` and hands every event of its own run
 * straight to it. That containment is the whole design. A chat is a list of turns, a
 * turn is a question plus the run it started, and nothing about a second question can
 * disturb the first one's state.
 *
 * Sessions sit one level above turns because the rail needs them, and they are bound to
 * the real `chat_sessions` rows `GET/POST /sessions` serves. Two ids therefore exist per
 * chat and neither is redundant:
 *
 * - `id` is a stable local key that never changes, so React never remounts a thread.
 * - `serverId` is the conversation — the same string as `memory_session.id` — minted by
 *   the **server** on the first question. It is null until then, which is exactly what
 *   an unsent chat is: a conversation nobody has started.
 *
 * A chat opened from the rail also carries `restored` turns: the transcript
 * `GET /sessions/{id}/messages` returns. Those are text, not runs — the event log that
 * produced them is gone, and a restored turn renders as what it is rather than pretending
 * to a trace it cannot show.
 *
 * Pure: no React, no transport. {@link useChatThread} is the only thing that wires it up.
 */

import type { StreamEvent } from '@/lib/stream'
import { initialRunState, runReducer, type RunState } from '@/state/runReducer'

import type { TurnAttachment } from './composerAttachment'
import { DEFAULT_RUN_MODE, type RunMode } from './runMode'

/** One question and the run it started, in this tab. */
export interface Turn {
  /** Client-side id; the run's own `run_id` arrives later, on `run_started`. */
  id: string
  /** The question as the person wrote it — never the wire text an attachment extends. */
  question: string
  /** Epoch milliseconds the question was sent. */
  askedAt: number
  /**
   * The image screened for this turn, or null.
   *
   * It lives on the turn rather than in the composer because the guardrail verdict is
   * part of what happened in this turn, and the composer has already moved on to the
   * next question by the time the answer arrives.
   */
  attachment: TurnAttachment | null
  /**
   * The width this turn was *asked* to run at.
   *
   * Kept on the turn, not read back off the composer, because the composer has already
   * moved on: a person who sends a Team question and then flips the menu back to Auto
   * would otherwise have the settled turn re-label itself. It is the request half of the
   * width receipt — the outcome half is the run's own `routing` event.
   */
  mode: RunMode
  run: RunState
}

/**
 * One turn read back from the stored transcript — text only.
 *
 * A reload restores what was said, never how it was decided: `run_events` is backlog,
 * so there is no event log to replay and no tabs to open on a restored turn.
 */
export interface RestoredTurn {
  turnIndex: number
  question: string
  answer: string
}

/** One chat: an ordered list of turns, bound to a server conversation once it starts. */
export interface ChatSession {
  /** Stable local key. */
  id: string
  /** `chat_sessions.id` / `memory_session.id`, or null before the first question. */
  serverId: string | null
  title: string
  startedAt: number
  /** Turns run in this tab. */
  turns: Turn[]
  /** Turns restored from the stored transcript, which come before {@link turns}. */
  restored: RestoredTurn[]
  /** Whether the transcript has been fetched (so it is fetched at most once). */
  restoredLoaded: boolean
}

/** Every chat the rail knows about, and which one the thread is showing. */
export interface ThreadState {
  sessions: ChatSession[]
  activeSessionId: string
}

/** One row of `GET /sessions`, reduced to what the rail needs. */
export interface ServerSession {
  id: string
  title: string
}

/** What can happen to a thread. */
export type ThreadAction =
  /** Open an empty chat and switch to it. */
  | { kind: 'new_chat'; sessionId: string; at: number }
  /** Show an existing chat. */
  | { kind: 'select_chat'; sessionId: string }
  /**
   * Merge the caller's stored conversations into the rail, newest first.
   *
   * `resumeServerId` is the conversation this tab was last looking at, remembered
   * across a reload. A reload used to open a fresh empty chat and leave the
   * conversation the person was mid-way through sitting in the rail, one click away
   * and looking like it had been abandoned.
   */
  | { kind: 'sync_sessions'; rows: ServerSession[]; at: number; resumeServerId?: string | null }
  /** Bind a chat to the conversation the server just minted for it. */
  | { kind: 'bind_session'; sessionId: string; serverId: string }
  /** Fill in a chat's stored transcript. */
  | { kind: 'restore'; sessionId: string; turns: RestoredTurn[] }
  /** Send a question in the active chat, opening its turn. */
  | {
      kind: 'ask'
      sessionId: string
      turnId: string
      question: string
      attachment?: TurnAttachment | null
      /** The width the composer asked for. Defaults to Auto. */
      mode?: RunMode
      at: number
    }
  /** One stream event, routed to the turn that owns the run. */
  | { kind: 'event'; turnId: string; event: StreamEvent }
  /** The transport closed; the turn is no longer running whatever else happened. */
  | { kind: 'closed'; turnId: string }

/** The longest a chat title runs in the rail before it is elided. */
const TITLE_LIMIT = 48

/** A chat's title: the first question, trimmed to fit the rail. */
export function titleFor(question: string): string {
  const clean = question.trim().replace(/\s+/g, ' ')
  return clean.length <= TITLE_LIMIT ? clean : `${clean.slice(0, TITLE_LIMIT - 1)}…`
}

/** An empty, unstarted chat. */
export function emptySession(sessionId: string, at: number): ChatSession {
  return {
    id: sessionId,
    serverId: null,
    title: '',
    startedAt: at,
    turns: [],
    restored: [],
    restoredLoaded: false,
  }
}

/** A thread holding one empty chat. */
export function initialThreadState(sessionId: string, at: number): ThreadState {
  return { sessions: [emptySession(sessionId, at)], activeSessionId: sessionId }
}

/** The chat currently shown, or null when the active id names none. */
export function activeSession(state: ThreadState): ChatSession | null {
  return state.sessions.find((s) => s.id === state.activeSessionId) ?? null
}

/** The turn whose run is still streaming, or null. */
export function liveTurn(session: ChatSession | null): Turn | null {
  return session?.turns.find((t) => t.run.running) ?? null
}

/** Whether a chat has anything in it at all. */
export function isEmptyChat(session: ChatSession | null): boolean {
  return session === null || (session.turns.length === 0 && session.restored.length === 0)
}

/**
 * The local key a synced conversation gets.
 *
 * Deterministic on purpose: a resume has only the server id to go on, and both the
 * merge and the transcript fetch have to name the same chat without one of them
 * guessing.
 */
export function localIdFor(serverId: string): string {
  return `chat-${serverId}`
}

/**
 * Which chat to show after a sync — the remembered one, or the one already open.
 *
 * A reload restores where the person was, and **only** when this tab has nothing of its
 * own. A sync is an async merge that can land seconds after the console mounted; if
 * somebody has already opened a chat or typed a question in the meantime, moving them is
 * worse than the defect this fixes. So the resume applies to an untouched, unstarted
 * chat and to nothing else.
 */
function resumedId(
  state: ThreadState,
  sessions: ChatSession[],
  resumeServerId: string | null,
): string {
  if (resumeServerId === null) return state.activeSessionId
  const target = sessions.find((s) => s.serverId === resumeServerId)
  if (target === undefined || target.id === state.activeSessionId) return state.activeSessionId

  const current = sessions.find((s) => s.id === state.activeSessionId)
  const untouched =
    current !== undefined && current.serverId === null && current.turns.length === 0
  return untouched ? target.id : state.activeSessionId
}

/** Apply `change` to one session, leaving every other identical. */
function mapSession(
  state: ThreadState,
  sessionId: string,
  change: (session: ChatSession) => ChatSession,
): ThreadState {
  return {
    ...state,
    sessions: state.sessions.map((s) => (s.id === sessionId ? change(s) : s)),
  }
}

/** Apply `change` to whichever session owns `turnId`, leaving the others identical. */
function mapTurn(state: ThreadState, turnId: string, change: (turn: Turn) => Turn): ThreadState {
  let touched = false
  const sessions = state.sessions.map((session) => {
    if (!session.turns.some((t) => t.id === turnId)) return session
    touched = true
    return { ...session, turns: session.turns.map((t) => (t.id === turnId ? change(t) : t)) }
  })
  return touched ? { ...state, sessions } : state
}

/** Reduce one action into the thread (pure). */
export function threadReducer(state: ThreadState, action: ThreadAction): ThreadState {
  switch (action.kind) {
    case 'new_chat': {
      // Opening a second unstarted chat would litter the rail with duplicates that are
      // all the same chat; switch back to the one that already exists instead.
      const spare = state.sessions.find((s) => s.serverId === null && s.turns.length === 0)
      if (spare !== undefined) return { ...state, activeSessionId: spare.id }
      return {
        sessions: [emptySession(action.sessionId, action.at), ...state.sessions],
        activeSessionId: action.sessionId,
      }
    }

    case 'select_chat':
      return state.sessions.some((s) => s.id === action.sessionId)
        ? { ...state, activeSessionId: action.sessionId }
        : state

    case 'sync_sessions': {
      const known = new Set(
        state.sessions.map((s) => s.serverId).filter((id): id is string => id !== null),
      )
      const added = action.rows
        .filter((row) => !known.has(row.id))
        .map(
          (row): ChatSession => ({
            id: localIdFor(row.id),
            serverId: row.id,
            title: row.title,
            startedAt: action.at,
            turns: [],
            restored: [],
            restoredLoaded: false,
          }),
        )
      const sessions = added.length === 0 ? state.sessions : [...state.sessions, ...added]
      const activeSessionId = resumedId(state, sessions, action.resumeServerId ?? null)

      // The rail re-syncs on every token change; a merge that changes nothing must
      // return the same object, or every poll remounts the whole rail.
      if (added.length === 0 && activeSessionId === state.activeSessionId) return state
      return { sessions, activeSessionId }
    }

    case 'bind_session':
      return mapSession(state, action.sessionId, (s) =>
        s.serverId === null ? { ...s, serverId: action.serverId } : s,
      )

    case 'restore':
      return mapSession(state, action.sessionId, (s) => ({
        ...s,
        restored: action.turns,
        restoredLoaded: true,
      }))

    case 'ask': {
      const turn: Turn = {
        id: action.turnId,
        question: action.question,
        askedAt: action.at,
        attachment: action.attachment ?? null,
        mode: action.mode ?? DEFAULT_RUN_MODE,
        // `running` up front, so the composer locks before `run_started` arrives.
        run: { ...initialRunState, running: true },
      }
      return mapSession(state, action.sessionId, (s) => ({
        ...s,
        title: s.title === '' ? titleFor(action.question) : s.title,
        turns: [...s.turns, turn],
      }))
    }

    case 'event':
      return mapTurn(state, action.turnId, (turn) => ({
        ...turn,
        run: runReducer(turn.run, action.event),
      }))

    case 'closed':
      return mapTurn(state, action.turnId, (turn) =>
        // Idempotent: a run that ended on its own terminal event already cleared this.
        turn.run.running ? { ...turn, run: { ...turn.run, running: false } } : turn,
      )
  }
}
