/**
 * Transport abstraction for a query run.
 *
 * A run is a bidirectional session: the backend streams {@link StreamEvent}s,
 * and the human may resolve an approval gate mid-stream. Both the live SSE
 * backend and the in-browser mock implement {@link RunTransport}, so the UI and
 * the `useRunStream` hook are identical whether or not a backend is present.
 */

import type { ApprovalDecision } from '@/lib/api/types'
import type { StreamEvent } from '@/lib/stream'

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

/** Starts runs and returns a controller for each. */
export interface RunTransport {
  /**
   * Begin a run.
   *
   * @param query - The user query.
   * @param persona - Optional adapter persona id.
   * @param token - Bearer token for RBAC, or null.
   * @param handlers - Run lifecycle callbacks.
   */
  start: (
    query: string,
    persona: string | null,
    token: string | null,
    handlers: RunHandlers,
  ) => RunController
}
