/**
 * Hand-rolled Server-Sent-Events parsing over a `fetch` streaming reader.
 * Ported from frontend/src/api/sse.ts.
 *
 * `POST /query` returns an SSE stream, and because it needs a request body we
 * cannot use the browser `EventSource` API (which is GET-only) — so we own the
 * stream via a `fetch` reader and parse the wire format ourselves. Each SSE
 * message carries `event: <type>` and `data: <json>`; on the backend the `data`
 * payload already contains the discriminant `type`, so we parse `data` as the
 * {@link StreamEvent} and ignore the redundant `event:` line.
 */

import type { StreamEvent } from '@/lib/stream'

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
    } catch (error) {
      // One malformed frame does not justify tearing down the stream — but it is not
      // nothing either, and swallowing it in silence is how a frame-splitting bug went
      // unnoticed while every run rendered empty. Report it and carry on.
      console.error('[aegis] discarded an unparseable stream frame', {
        error,
        preview: data.slice(0, 200),
      })
    }
  }

  // Per the SSE specification a frame ends at a blank line, which may be CRLFCRLF,
  // LFLF or CRCR. Not global-sticky: `lastIndex` is reset explicitly per match so a
  // partial frame left in the buffer is re-scanned from its start on the next chunk.
  const _FRAME_SEPARATOR = /\r\n\r\n|\n\n|\r\r/

  try {
    for (;;) {
      if (signal?.aborted) break
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // Frames are separated by a blank line, and the separator is not always \n\n.
      // `sse-starlette` — the server on the other end of every one of these streams —
      // writes CRLF (`DEFAULT_SEPARATOR = "\r\n"` in its `event.py`), so a scan for
      // `\n\n` alone matched nothing, no frame ever flushed mid-stream, and the whole
      // response was handed to `extractData` at the end as one blob whose `JSON.parse`
      // threw into a silent `catch`. Every live-run surface in the product — the
      // console, RAG, Harness, Graph, Voice and Simulation — rendered nothing on a
      // successful run while an unsuccessful one looked fine, because the error and
      // close paths do not go through here. The SSE specification names all three
      // separators; accept all three.
      let match = _FRAME_SEPARATOR.exec(buffer)
      while (match !== null) {
        flushFrame(buffer.slice(0, match.index))
        buffer = buffer.slice(match.index + match[0].length)
        _FRAME_SEPARATOR.lastIndex = 0
        match = _FRAME_SEPARATOR.exec(buffer)
      }
    }
    if (buffer.trim().length > 0) flushFrame(buffer)
  } finally {
    reader.releaseLock()
  }
}
