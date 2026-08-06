/**
 * Hand-rolled Server-Sent-Events parsing over a `fetch` streaming reader.
 *
 * `POST /query` returns an SSE stream, and because it needs a request body we
 * cannot use the browser `EventSource` API (which is GET-only) — so we own the
 * stream via a `fetch` reader and parse the wire format ourselves. Each SSE
 * message carries `event: <type>` and `data: <json>`; on the backend the
 * `data` payload already contains the discriminant `type`, so we parse `data`
 * as the {@link StreamEvent} and ignore the redundant `event:` line.
 *
 * @see backend/src/app/api/schemas.py — the emitted wire format.
 */

import type { StreamEvent } from '@/types/stream'

/** Split a raw SSE frame into its `data:` payload (joined multi-line). */
function extractData(frame: string): string | null {
  const dataLines: string[] = []
  for (const rawLine of frame.split('\n')) {
    const line = rawLine.replace(/\r$/, '')
    if (line.startsWith(':')) continue // comment / heartbeat
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''))
    }
  }
  return dataLines.length > 0 ? dataLines.join('\n') : null
}

/**
 * Read an SSE response body to completion, decoding each frame into a
 * {@link StreamEvent} and invoking `onEvent`.
 *
 * @param body - The `ReadableStream` from a streaming `fetch` response.
 * @param onEvent - Called once per successfully parsed event.
 * @param signal - Optional abort signal to stop reading early.
 */
export async function readSSEStream(
  body: ReadableStream<Uint8Array>,
  onEvent: (event: StreamEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  const flushFrame = (frame: string): void => {
    const data = extractData(frame)
    if (data === null) return
    try {
      onEvent(JSON.parse(data) as StreamEvent)
    } catch {
      // Ignore malformed frames rather than tearing down the whole stream.
    }
  }

  try {
    for (;;) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line (\n\n).
      let sep = buffer.indexOf('\n\n')
      while (sep !== -1) {
        flushFrame(buffer.slice(0, sep))
        buffer = buffer.slice(sep + 2)
        sep = buffer.indexOf('\n\n')
      }
    }
    if (buffer.trim().length > 0) flushFrame(buffer)
  } finally {
    reader.releaseLock()
  }
}
