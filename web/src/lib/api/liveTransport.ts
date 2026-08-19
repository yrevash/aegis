/**
 * The run transport — streams a query from the FastAPI backend over SSE.
 *
 * `POST /query` opens the stream (see {@link readSSEStream} for why we use a
 * `fetch` reader rather than `EventSource`). Approval decisions are sent to
 * `POST /approval` out of band; the backend resumes the same stream.
 */

import type { ApprovalDecision } from '@/lib/api/types'

import { getAuthToken } from './authToken'
import { postApproval } from './client'
import { API_BASE } from './config'
import { readSSEStream } from './sse'
import type { RunController, RunHandlers, RunRequest } from './transport'

/**
 * Begin a run and return its controller.
 *
 * @param request - The turn, its persona, the conversation it belongs to, and the
 *   width the user asked for.
 * @param token - Bearer token for RBAC, or null to use the stored session token.
 * @param handlers - Run lifecycle callbacks.
 */
export function startRun(
  request: RunRequest,
  token: string | null,
  handlers: RunHandlers,
): RunController {
  const {
    query,
    persona,
    sessionId = null,
    depthMode = null,
    requestedFanout = null,
  } = request
  const controller = new AbortController()
  // Per-call token wins; else fall back to the signed-in session's token.
  const bearer = token ?? getAuthToken()

  const run = async (): Promise<void> => {
    const headers = new Headers({ 'Content-Type': 'application/json' })
    if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
    const res = await fetch(`${API_BASE}/query`, {
      method: 'POST',
      headers,
      // `session_id` is what turns the memory nodes from pass-throughs into a
      // recall; omitting it is why memory was dark in the live product. `depth_mode`
      // and `requested_fanout` are the same seam for width — the run honours an
      // explicit mode exactly, and this is the only wire it can arrive on.
      //
      // Both are sent as `null` when unset rather than omitted, which is deliberate:
      // `QueryRequest` now forbids unknown fields, so a typo here is a 422 that names
      // the field instead of a 200 that quietly ran in Auto.
      body: JSON.stringify({
        query,
        persona,
        session_id: sessionId,
        depth_mode: depthMode,
        requested_fanout: requestedFanout,
      }),
      signal: controller.signal,
    })
    if (!res.ok || res.body === null) {
      throw new Error(`POST /query failed: ${res.status} ${res.statusText}`)
    }
    await readSSEStream(res.body, handlers.onEvent, controller.signal)
  }

  run()
    .catch((error: unknown) => {
      if (controller.signal.aborted) return
      handlers.onError(error instanceof Error ? error : new Error(String(error)))
    })
    .finally(() => handlers.onClose())

  return {
    resolveApproval(approvalId: string, decision: ApprovalDecision): void {
      void postApproval({ approval_id: approvalId, decision }, bearer).catch((error: unknown) =>
        handlers.onError(error instanceof Error ? error : new Error(String(error))),
      )
    },
    abort(): void {
      controller.abort()
    },
  }
}
