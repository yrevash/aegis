/**
 * The little bit of Markdown a model actually writes, parsed into blocks.
 *
 * ## Why this exists rather than a dependency
 *
 * The owner's verdict on the shipped answer was that it did not read like *"what Claude
 * or ChatGPT gives"*. Part of that was length (a backend prompt said "be concise and
 * decisive", and that has been fixed at the source). The rest is this: the model writes
 * `**A knowledge document**`, `## What to check` and `1.` numbered steps, and the panel
 * rendered them as literal asterisks and hashes in one `whitespace-pre-wrap` paragraph.
 * A structured answer arriving as punctuation soup reads as a broken product, and it is
 * the last thing anybody looks at before deciding whether they believe the run.
 *
 * There is no Markdown library in `package.json` and adding one is not this lane's to
 * do, so this parses the subset that shows up in practice and nothing else. That is a
 * feature rather than a compromise: the output is a typed tree the renderer turns into
 * React elements, so there is no `dangerouslySetInnerHTML` anywhere near model output
 * and no HTML passthrough to sanitise.
 *
 * **Links are deliberately not parsed.** `[text](url)` stays literal text. A clickable
 * target synthesised from model output is a phishing surface in a product whose entire
 * pitch is that it does not assert anything without provenance — citations belong in the
 * sources strip, where they carry a receipt, not inline where they carry nothing.
 *
 * Partial input is expected and safe: the answer types out a character at a time, so
 * this parses prefixes constantly. An unclosed `**` simply renders as text until its
 * closer arrives, which is what every streaming chat surface does.
 *
 * Pure, so `web/tests/console/answerMarkdown.test.mjs` can read the tree directly.
 */

/** One run of inline text, and the emphasis it carries. */
export interface Inline {
  kind: 'text' | 'bold' | 'italic' | 'code'
  text: string
}

/** One block of an answer. */
export type Block =
  | { kind: 'paragraph'; spans: Inline[] }
  | { kind: 'heading'; level: 1 | 2 | 3; spans: Inline[] }
  | { kind: 'list'; ordered: boolean; start: number; items: Inline[][] }
  | { kind: 'quote'; spans: Inline[] }
  | { kind: 'code'; text: string }
  | { kind: 'rule' }

/** `**bold**`, `*italic*`, `_italic_` and `` `code` ``, in one pass. */
const INLINE = /(\*\*[^*]+\*\*|`[^`]+`|\*[^*\s][^*]*\*|_[^_\s][^_]*_)/g

/**
 * Split one line into its emphasis runs.
 *
 * @param text - A single line of answer text.
 * @returns The runs, in order. A line with no markup is one `text` run.
 */
export function parseInline(text: string): Inline[] {
  const spans: Inline[] = []
  let last = 0
  for (const match of text.matchAll(INLINE)) {
    const at = match.index
    if (at > last) spans.push({ kind: 'text', text: text.slice(last, at) })
    const token = match[0]
    if (token.startsWith('**')) spans.push({ kind: 'bold', text: token.slice(2, -2) })
    else if (token.startsWith('`')) spans.push({ kind: 'code', text: token.slice(1, -1) })
    else spans.push({ kind: 'italic', text: token.slice(1, -1) })
    last = at + token.length
  }
  if (last < text.length) spans.push({ kind: 'text', text: text.slice(last) })
  return spans.length > 0 ? spans : [{ kind: 'text', text }]
}

const HEADING = /^(#{1,3})\s+(.*)$/
const BULLET = /^\s*[-*•]\s+(.*)$/
const NUMBERED = /^\s*(\d{1,3})[.)]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const RULE = /^\s*(?:---+|\*\*\*+|___+)\s*$/
const FENCE = /^\s*```/

/**
 * Parse an answer into blocks.
 *
 * @param text - The answer as it has arrived so far, complete or not.
 * @returns The blocks, in order. Empty input yields no blocks.
 */
export function parseAnswer(text: string): Block[] {
  const blocks: Block[] = []
  const lines = text.split('\n')

  let paragraph: string[] = []
  let list: { ordered: boolean; start: number; items: string[] } | null = null
  let fence: string[] | null = null

  const closeParagraph = (): void => {
    if (paragraph.length === 0) return
    blocks.push({ kind: 'paragraph', spans: parseInline(paragraph.join(' ')) })
    paragraph = []
  }
  const closeList = (): void => {
    if (list === null) return
    blocks.push({
      kind: 'list',
      ordered: list.ordered,
      start: list.start,
      items: list.items.map(parseInline),
    })
    list = null
  }
  const closeAll = (): void => {
    closeParagraph()
    closeList()
  }

  for (const raw of lines) {
    // A fence swallows everything verbatim until it closes — or until the stream
    // stops, which on a partial answer is the common case and still renders.
    if (fence !== null) {
      if (FENCE.test(raw)) {
        blocks.push({ kind: 'code', text: fence.join('\n') })
        fence = null
      } else {
        fence.push(raw)
      }
      continue
    }
    if (FENCE.test(raw)) {
      closeAll()
      fence = []
      continue
    }

    if (raw.trim() === '') {
      closeAll()
      continue
    }

    if (RULE.test(raw)) {
      closeAll()
      blocks.push({ kind: 'rule' })
      continue
    }

    const heading = HEADING.exec(raw)
    if (heading !== null) {
      closeAll()
      const level = Math.min(3, heading[1].length) as 1 | 2 | 3
      blocks.push({ kind: 'heading', level, spans: parseInline(heading[2]) })
      continue
    }

    const quote = QUOTE.exec(raw)
    if (quote !== null) {
      closeAll()
      blocks.push({ kind: 'quote', spans: parseInline(quote[1]) })
      continue
    }

    const numbered = NUMBERED.exec(raw)
    if (numbered !== null) {
      closeParagraph()
      if (list === null || !list.ordered) {
        closeList()
        list = { ordered: true, start: Number(numbered[1]), items: [] }
      }
      list.items.push(numbered[2])
      continue
    }

    const bullet = BULLET.exec(raw)
    if (bullet !== null) {
      closeParagraph()
      if (list === null || list.ordered) {
        closeList()
        list = { ordered: false, start: 1, items: [] }
      }
      list.items.push(bullet[1])
      continue
    }

    // A plain line continuing a list item belongs to that item, not to a new
    // paragraph wedged between two bullets.
    if (list !== null && /^\s{2,}\S/.test(raw)) {
      list.items[list.items.length - 1] += ` ${raw.trim()}`
      continue
    }

    closeList()
    paragraph.push(raw.trim())
  }

  if (fence !== null && fence.length > 0) blocks.push({ kind: 'code', text: fence.join('\n') })
  closeAll()
  return blocks
}
