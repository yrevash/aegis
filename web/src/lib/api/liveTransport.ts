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
import type { RunController, RunHandlers } from './transport'

/**
 * Begin a run and return its controller.
 *
 * @param query - The user query.
 * @param persona - Optional adapter persona id.
 * @param token - Bearer token for RBAC, or null to use the stored session token.
 * @param handlers - Run lifecycle callbacks.
 */
export function startRun(
  query: string,
  persona: string | null,
  token: string | null,
  handlers: RunHandlers,
): RunController {
  const controller = new AbortController()
  // Per-call token wins; else fall back to the signed-in session's token.
  const bearer = token ?? getAuthToken()

  const run = async (): Promise<void> => {
    const headers = new Headers({ 'Content-Type': 'application/json' })
    if (bearer) headers.set('Authorization', `Bearer ${bearer}`)
    const res = await fetch(`${API_BASE}/query`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ query, persona }),
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
