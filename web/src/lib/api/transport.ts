/**
 * Transport contract for a query run.
 *
 * A run is a bidirectional session: the backend streams {@link StreamEvent}s over
 * SSE, and the human may resolve an approval gate mid-stream. These types are the
 * seam between `useRunStream` (which reduces events into state) and
 * `liveTransport` (which speaks HTTP), so the hook never touches fetch directly.
 */

import type { ApprovalDecision } from '@/lib/api/types'
import type { StreamEvent } from '@/lib/stream'

/**
 * Everything one run needs to start — the turn, and the conversation it belongs to.
 *
 * `sessionId` is the field this repo spent a phase without. `QueryRequest.session_id`
 * has always existed backend-side and both memory nodes gate on it
 * (`if deps.memory is None or state.get("session_id") is None: return {}`), but the
 * body the console posted was `{ query, persona }` — so every live run recalled
 * nothing and persisted nothing while the product claimed multi-turn memory. It is a
 * request field rather than a fifth positional argument precisely so the next thing
 * the run needs cannot be forgotten at four call sites.
 */
export interface RunRequest {
  /** The user's turn. */
  query: string
  /** Adapter persona id; scopes data and tools. Null for the caller's default. */
  persona: string | null
  /**
   * The conversation this turn belongs to, or null for a deliberately single-shot
   * run. The same id is `memory_session.id` and `chat_sessions.id`, so the recall
   * and the transcript agree on what a conversation is.
   */
  sessionId?: string | null
  /**
   * The REQUESTED width: `'auto'` (the classifier decides), `'single'` or `'team'`.
   * Omitted behaves exactly as `'auto'`.
   *
   * The same story as `sessionId`, one phase later. `aegis.agent.run_agent` has taken
   * `depth_mode` since Phase 5 and honours an explicit value exactly — the classifier is
   * skipped, not overruled — but `QueryRequest` did not carry the field and `POST /query`
   * never passed one, so a mode chosen in the composer could not reach the run by any
   * route. Pydantic drops an unknown body field in silence, so posting it anyway looked
   * like it worked.
   */
  depthMode?: 'auto' | 'single' | 'team' | null
  /**
   * An explicit team width (the composer's Custom mode). Only legal with
   * `depthMode: 'team'` — the server rejects it in any other mode rather than ignoring
   * half the request. It is clamped DOWN by the tenant's cap and never up; the run's
   * `routing` event reports `decided_by: 'platform_cap'` when that happened, so the
   * screen can say who narrowed it.
   */
  requestedFanout?: number | null
}

/** Callbacks a transport invokes as a run progresses. */
export interface RunHandlers {
  /** One decoded stream event. */
  onEvent: (event: StreamEvent) => void
  /** A transport-level failure (network, parse, abort). */
  onError: (error: Error) => void
  /** The stream closed (terminal event received or connection ended). */
  onClose: () => void
}

/** Handle to a live run: resolve its gate or abort it. */
export interface RunController {
  /** Resolve a paused approval gate. */
  resolveApproval: (approvalId: string, decision: ApprovalDecision) => void
  /** Abort the run and release resources. */
  abort: () => void
}
