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
