/**
 * How fast the answer types out — the pacing behind the console's streaming caret.
 *
 * ## Why this has to exist on the client
 *
 * The `stream` node measures **0 ms** on a real run, and that is by design rather than
 * by accident. `stream_answer` in `aegis/src/aegis/agent/graph.py` says so in as many
 * words: the answer is generated in full by `generate`, cleared by `guard_output`, and
 * only *then* chunked onto the socket — because streaming raw model tokens would put
 * unguarded text on screen and make an output block unenforceable after the fact. You
 * cannot unsay a leaked secret.
 *
 * So sixty-four `token` events land in the same millisecond, the panel assembled them
 * and dumped the finished paragraph, and a product whose whole pitch is *watch it work*
 * ended its best moment with a flash of text. Pacing the reveal here is the honest fix:
 * every character shown is a character the wire actually sent, and the only thing this
 * module decides is *when* it appears.
 *
 * ## The curve
 *
 * A fixed characters-per-second rate is wrong in both directions — a 200-character
 * answer crawls, a 3,000-character one takes half a minute. So the rate is proportional
 * to what is left: whatever is buffered is drained over about {@link REVEAL_WINDOW_MS},
 * with a floor of {@link MIN_CHARS_PER_SECOND} so the last few characters do not
 * asymptote. That means a short answer finishes fast, a long one finishes in roughly
 * the same wall-clock time, and text that arrives while the reveal is already running
 * simply raises the rate rather than queueing behind it.
 *
 * Pure and frame-rate independent (it integrates over a real elapsed delta, so a
 * backgrounded tab catches up in one step on return), which is what lets
 * `web/tests/console/revealPace.test.mjs` assert the curve without a renderer.
 */

/** The buffered remainder is drained over about this long. */
export const REVEAL_WINDOW_MS = 900

/** The slowest the caret ever moves, in characters per second. */
export const MIN_CHARS_PER_SECOND = 110

/**
 * How many characters should be revealed after `deltaMs` more have passed.
 *
 * @param revealed - Characters revealed so far. Fractional, so a slow rate still
 *   accumulates across frames instead of rounding to zero every time.
 * @param total - Characters received from the wire so far.
 * @param deltaMs - Real time elapsed since the last step.
 * @returns The new revealed count, never above `total` and never going backwards.
 */
export function advanceReveal(revealed: number, total: number, deltaMs: number): number {
  if (revealed >= total) return total
  if (deltaMs <= 0) return revealed
  const remaining = total - revealed
  const perSecond = Math.max(MIN_CHARS_PER_SECOND, remaining / (REVEAL_WINDOW_MS / 1000))
  return Math.min(total, revealed + (perSecond * deltaMs) / 1000)
}
