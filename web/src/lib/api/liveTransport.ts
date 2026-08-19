/**
 * The run transport — streams a query from the FastAPI backend over SSE.
 *
 * `POST /query` opens the stream (see {@link readSSEStream} for why we use a
 * `fetch` reader rather than `EventSource`). Approval decisions are sent to
 * `POST /approval` out of band; the backend resumes the same stream.
 */

import type { ApprovalDecision } from '@/lib/api/types'

import { ApiError } from './apiError'
import { getAuthToken, reportSessionExpired } from './authToken'
import { postApproval } from './client'
import { API_BASE } from './config'
import { readSSEStream } from './sse'
import type { RunController, RunHandlers, RunRequest } from './transport'

/**
 * Open the stream, turning an unreachable backend into a sentence.
 *
 * `fetch` rejects with `TypeError: Failed to fetch` when nothing answers, and that
 * string went straight to the screen as "The run stopped. Failed to fetch" — which
 * names a browser API rather than the thing that is wrong. A *fresh* load with the
 * backend down is handled well by `BackendGate`; this is the same fact, said as
 * plainly, for the session that was already open when the backend went away.
 */
async function postQuery(init: RequestInit): Promise<Response> {
  try {
    return await fetch(`${API_BASE}/query`, init)
  } catch (cause) {
    if (init.signal?.aborted === true) throw cause
    throw new ApiError(
      0,
      'POST',
      '/query',
      'The backend stopped answering. Check it is still running, then send the question again.',
    )
  }
}

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
    const res = await postQuery({
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
      // A sentence, not a status line. `The run stopped. POST /query failed: 401
      // Unauthorized` told the person the route and nothing they could act on; a
      // 401 here is an expired bearer, and it signs the console out on the way past.
      if (res.status === 401) reportSessionExpired()
      throw new ApiError(
        res.ok ? 0 : res.status,
        'POST',
        '/query',
        res.ok ? 'The backend accepted the question but sent no stream to read.' : undefined,
      )
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
