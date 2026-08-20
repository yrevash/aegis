/**
 * The pure half of the receipt — what a provenance line actually says.
 *
 * The receipt is the one signature element of this design system (DESIGN.md §1):
 * every figure that has an origin carries where it came from, in the same
 * treatment everywhere. Before it was a component, each screen improvised it,
 * and the improvisations disagreed about the small things — some constants
 * embed their own label (`'Source: usage_ledger · statsforecast'`), some do not
 * (`'platform default'`); some pass an empty detail string, some pass `null`.
 * Rendering those naively gives `Source: Source: usage_ledger ·  · ` and a
 * reader who stops believing the line.
 *
 * So the joining and the de-duplication live here, framework-free, and
 * `web/tests/design/receiptText.test.mjs` exercises them directly.
 */

/** The separator between the parts of a receipt — one interpunct, spaced. */
const SEP = ' · '

/** What a rendered receipt is made of: a lead-in word, and the origin itself. */
export interface ReceiptText {
  /** The lead-in, without punctuation — the component draws the colon. */
  label: string
  /** The origin and any measured detail, joined and de-duplicated. */
  body: string
}

/**
 * Normalise one receipt into the two strings a {@link ReceiptText} renders.
 *
 * The label is stripped off the front of the origin when the caller's constant
 * already carries it, so the ~20 `Source: …` strings that predate this module
 * migrate by being passed in unchanged.
 *
 * @param label - The lead-in word, e.g. `Source`, `Decided by`, `Evidence`.
 * @param origin - Where the figure came from. Never a claim, always a fact.
 * @param detail - Measured detail this particular render can add.
 * @returns The label and the joined body, both already trimmed.
 */
export function receiptText(
  label: string,
  origin: string,
  detail?: string | null,
): ReceiptText {
  const lead = label.trim().replace(/:$/, '')
  const parts = [stripLabel(origin, lead), detail ?? '']
    .map((part) => part.trim())
    .filter((part) => part !== '')
  return { label: lead, body: parts.join(SEP) }
}

/**
 * Drop a leading `<label>:` from an origin string, case-insensitively.
 *
 * Only the exact lead-in is removed — an origin that merely *mentions* the word
 * ("sourced from the ledger") is left alone, because the component is about to
 * print the label itself and swallowing a real word would change the fact.
 */
function stripLabel(origin: string, label: string): string {
  const text = origin.trim()
  const prefix = `${label.toLowerCase()}:`
  if (!text.toLowerCase().startsWith(prefix)) return text
  return text.slice(prefix.length).trim()
}
