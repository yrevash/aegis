/**
 * How long a question may be, and what the composer says as it fills up.
 *
 * The input rail refuses anything over 8,000 characters — `MAX_INPUT_CHARS` in
 * `aegis/src/aegis/guardrails/schema.py`, where it is called out as context-stuffing
 * rather than a question. The composer did not know that. A 60,000-character paste was
 * accepted, stored in the thread, titled the chat, rendered in full, sent, and refused
 * at the far end, which is four wasted steps and a rejection the person could have been
 * spared before they pressed Enter.
 *
 * The counter is deliberately quiet until it matters. A number ticking under every
 * question is noise for the 99% of questions that are one line long; it appears near the
 * limit, where it is the difference between "trim this" and "why was that refused?".
 *
 * ## Why the *composed* query is what gets measured
 *
 * A screened image travels as text appended to the question (see
 * {@link questionWithAttachment}), and the rail counts what arrives, not what was typed.
 * Measuring only the typed characters would let a 7,900-character question plus a long
 * image description sail past this check and be refused anyway — which is the same
 * defect with an extra step.
 */

/** Built once — `toLocaleString()` constructs a new formatter on every call. */
const COUNT = new Intl.NumberFormat('en-US')

/**
 * The input rail's ceiling, in characters.
 *
 * Mirrors `aegis.guardrails.schema.MAX_INPUT_CHARS`. If that moves, this moves: a client
 * cap that is *stricter* than the rail refuses questions the backend would have taken,
 * and one that is looser is the bug this file exists to close.
 */
export const MAX_QUESTION_CHARS = 8_000

/** How close to the ceiling the counter starts showing itself. */
const COUNTER_VISIBLE_AT = 0.9

/** What the composer knows about the length of what is about to be sent. */
export interface QuestionLength {
  /** Characters in the composed query — the typed question plus any attached description. */
  used: number
  /** Characters still available. Negative once over. */
  remaining: number
  /** True once the rail would refuse this. The Send button is disabled on it. */
  over: boolean
  /** Whether the counter is worth showing yet. */
  showCounter: boolean
  /** The counter's own words, or `''` when it is not shown. */
  label: string
}

/**
 * Measure what is about to be sent.
 *
 * @param composed - The query as it will go on the wire, attachment description included.
 * @returns Whether it fits, and what to tell the person about it.
 */
export function questionLength(composed: string): QuestionLength {
  const used = composed.length
  const remaining = MAX_QUESTION_CHARS - used
  const over = used > MAX_QUESTION_CHARS
  const showCounter = used >= MAX_QUESTION_CHARS * COUNTER_VISIBLE_AT

  return {
    used,
    remaining,
    over,
    showCounter,
    label: !showCounter
      ? ''
      : over
        ? `${COUNT.format(-remaining)} characters over the ${COUNT.format(MAX_QUESTION_CHARS)} the agent accepts — trim it to send.`
        : `${COUNT.format(remaining)} characters left of ${COUNT.format(MAX_QUESTION_CHARS)}.`,
  }
}
