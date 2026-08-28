/**
 * The guardrail demonstrator stream — one adversarial payload, one real verdict.
 *
 * `GET /v1/stream/guardrail-demo?q=<text>` (`backend/src/app/api/routes.py:1523`) runs a
 * real `Guardrails().stream_check_input_agui(q, em)` and forwards every AG-UI frame the
 * emitter produces. It is **unauthenticated by design**, on the same reasoning as
 * `/health`: it touches no tenant data, and a demonstrator that can fail on a stale
 * bearer mid-presentation is a demonstrator of the wrong thing. So this module follows
 * the `notifications.ts` GET-SSE shape and drops both the bearer and the reconnect
 * ladder — a probe that did not land is a fact to render, not a thing to retry behind
 * the reader's back.
 *
 * **Three properties of this wire that cost time to rediscover**, all verified against
 * the emitter itself (`aegis/src/aegis/core/stream.py`) rather than guessed:
 *
 * 1. There is **no `event:` line**. `EventEncoder` writes `data: {json}\n\n` and nothing
 *    else, so the frame's own `type` is the only discriminant — which is exactly what
 *    {@link readSSEStream} already assumes.
 * 2. A **second** CUSTOM frame, `guardrail_cache`, can arrive between STEP_STARTED and
 *    the verdict when the injection rail consults its cache. Narrowing on `type ===
 *    'CUSTOM'` alone would hand a cache hit/miss to the caller as a verdict, so the
 *    narrowing is on `name === 'guardrail_verdict'`.
 * 3. There is **no RUN_ERROR frame**. A rail that raises ends the stream after
 *    STEP_STARTED and nothing more arrives. That is why this returns `null` rather than
 *    throwing: "the stream closed without a verdict" is a real outcome with its own
 *    meaning, distinct from "the request never reached the backend", and a caller that
 *    cannot tell them apart will report one as the other.
 *
 * @see backend/src/app/api/routes.py — `guardrail_demo`
 * @see aegis/src/aegis/guardrails/pipeline.py — `stream_check_input_agui`
 */

import { ApiError } from './apiError'
import { API_BASE } from './config'
import { readSSEStream } from './sse'

/** The route this module reads, for an {@link ApiError} that has to name it. */
const PATH = '/stream/guardrail-demo'

/**
 * Per-rail timing as the backend actually sends it.
 *
 * Every sub-rail is `null` — `stream_check_input_agui` brackets the *whole* input
 * pipeline with one `time.monotonic()` pair and fills `schema`, `pii` and `injection`
 * with `None` placeholders. Typing them as `number | null` rather than eliding them is
 * deliberate: it is the type telling a chart author that six per-rail durations do not
 * exist and cannot be drawn. Only `total` was measured.
 */
export interface PerRailTimingMs {
  schema: number | null
  pii: number | null
  injection: number | null
  /** The one measured figure: milliseconds spent inside the rail, server-side. */
  total: number
}

/** One PII span, as character offsets into the submitted text. Kinds only, never values. */
export interface RedactionSpan {
  kind: string
  start: number
  end: number
}

/**
 * The `value` of a `guardrail_verdict` CUSTOM frame.
 *
 * The AG-UI envelope around it is camelCase (`threadId`, `stepName`, `rawEvent`) because
 * the encoder aliases it, but the `value` payload is a plain dict the pipeline builds by
 * hand and it stays **snake_case** on the wire. Mirroring that here rather than
 * renaming it keeps the parse honest: `redaction_spans` is what arrives.
 */
export interface GuardrailDemoVerdict {
  /** `pass` | `block` | `redact` | `flag` — but a backend may ship a new one first. */
  verdict: string
  /** Zero or one element: the layer that decided. Empty when no rail claimed it. */
  rules: string[]
  /** The rail's own sentence, verbatim. Never composed on this side. */
  rationale: string
  /** PII *kinds* the rail masked. Never the values. */
  redactions: string[]
  redaction_spans: RedactionSpan[]
  per_rail_timing_ms: PerRailTimingMs
  spanKind: string
}

/** The minimum shape every AG-UI frame on this stream shares. */
interface DemoFrame {
  type?: unknown
  name?: unknown
  value?: unknown
}

/** Whether a decoded frame is the verdict — and not the `guardrail_cache` frame beside it. */
function isVerdictFrame(frame: unknown): frame is { value: GuardrailDemoVerdict } {
  if (typeof frame !== 'object' || frame === null) return false
  const f = frame as DemoFrame
  if (f.type !== 'CUSTOM' || f.name !== 'guardrail_verdict') return false
  const value = f.value
  if (typeof value !== 'object' || value === null) return false
  const v = value as Partial<GuardrailDemoVerdict>
  return typeof v.verdict === 'string' && typeof v.per_rail_timing_ms?.total === 'number'
}

/**
 * Fire one payload at the real input rail and wait for its verdict.
 *
 * @param text - The adversarial payload to screen. Sent verbatim, URL-encoded.
 * @param signal - Aborts the in-flight read; a firing line stopped mid-run uses this.
 * @returns The verdict frame's `value`, or `null` when the stream closed without one.
 * @throws ApiError - When the request never reached the backend, or it refused.
 */
export async function streamGuardrailDemo(
  text: string,
  signal?: AbortSignal,
): Promise<GuardrailDemoVerdict | null> {
  let res: Response
  try {
    res = await fetch(`${API_BASE}${PATH}?q=${encodeURIComponent(text)}`, {
      method: 'GET',
      headers: new Headers({ Accept: 'text/event-stream' }),
      cache: 'no-store',
      signal,
    })
  } catch (error) {
    // An abort is the caller's own decision and must not be dressed up as a backend
    // failure — it is re-thrown as-is so the firing line can tell "I stopped this"
    // from "the rail could not be reached".
    if (signal?.aborted) throw error
    throw new ApiError(0, 'GET', PATH)
  }
  if (!res.ok || res.body === null) {
    throw new ApiError(res.ok ? 0 : res.status, 'GET', PATH)
  }

  let verdict: GuardrailDemoVerdict | null = null
  await readSSEStream<unknown>(
    res.body,
    (frame) => {
      if (isVerdictFrame(frame)) verdict = frame.value
    },
    signal,
  )
  return verdict
}
