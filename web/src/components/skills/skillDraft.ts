/**
 * Turning a finished run into a `SKILL.md` draft — the pure half.
 *
 * A skill is the platform's self-improvement loop: a short instruction sheet the agent
 * loads on demand, with a `load_skill` tool call you can watch in the trace. Writing one
 * meant leaving the console for the settings screen and typing it from a blank template,
 * which is the wrong moment and the wrong surface — the run that just went well is the
 * material, and by the time you have navigated away you are recalling it rather than
 * reading it.
 *
 * This module is everything about that draft that is not React: the slug, the
 * one-sentence description, and the body. Kept pure because every one of the server's
 * refusals is a rule this text has to satisfy *before* it is posted — a name that is not
 * slug-shaped, a description over 280 characters or a body over 20,000 is a 422 with the
 * author's work still in the box.
 *
 * **Nothing here invents content.** The draft carries the question that was asked, the
 * tools the run actually called and the sources it actually stood on. Where the run
 * recorded none of something, that section is absent rather than filled with a
 * plausible-looking placeholder — the author is writing a procedure their agent will
 * follow, and a fabricated step is worse than a missing one.
 *
 * @see aegis/src/aegis/skills/document.py — the parser these bounds mirror
 */

/** Slug-shaped: an identifier in a tool call, not a title. Mirrors `SKILL_NAME_PATTERN`. */
export const SKILL_NAME_PATTERN = /^[a-z0-9][a-z0-9_-]{1,63}$/

/** Mirrors `MAX_DESCRIPTION_CHARS` — it sits in every system prompt this skill resolves into. */
export const MAX_DESCRIPTION_CHARS = 280

/** Mirrors `MAX_BODY_CHARS` — the body re-enters the prompt whole once loaded. */
export const MAX_BODY_CHARS = 20_000

/** The name a draft falls back to when the question yields no usable slug. */
const FALLBACK_NAME = 'saved_run'

/** Words too common to be worth a trigger, and too common to be worth a name. */
const STOPWORDS = new Set([
  'a', 'an', 'and', 'are', 'as', 'at', 'be', 'but', 'by', 'can', 'did', 'do', 'does',
  'for', 'from', 'has', 'have', 'how', 'i', 'in', 'is', 'it', 'me', 'my', 'of', 'on', 'or',
  'our', 'show', 'that', 'the', 'their', 'them', 'there', 'these', 'this', 'to', 'was',
  'we', 'were', 'what', 'when', 'where', 'which', 'who', 'why', 'will', 'with', 'you', 'your',
])

/** The words of a question, lowercased and stripped of punctuation. */
function words(question: string): string[] {
  return question
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, ' ')
    .split(/\s+/)
    .filter((word) => word !== '')
}

/**
 * A slug-shaped name derived from the question.
 *
 * Content words only, capped at six of them, so `What is our refund window for
 * enterprise customers?` becomes `refund_window_enterprise_customers` rather than a
 * 60-character transcription of the sentence. The result always satisfies
 * {@link SKILL_NAME_PATTERN}: a question of nothing but stopwords or punctuation still
 * has to produce a name the server will accept.
 */
export function skillName(question: string): string {
  const kept = words(question)
    .filter((word) => !STOPWORDS.has(word))
    .slice(0, 6)
    .join('_')
    .slice(0, 60)
    .replace(/[-_]+$/, '')
  if (SKILL_NAME_PATTERN.test(kept)) return kept
  // A name that fails the pattern is one the server would refuse. Falling back is not a
  // silent correction: the name sits on the first line of the draft, in the box, and the
  // author can see it before they save.
  return FALLBACK_NAME
}

/** Up to four content words from the question, as the `triggers` list. */
export function skillTriggers(question: string): string[] {
  const seen = new Set<string>()
  for (const word of words(question)) {
    if (STOPWORDS.has(word) || word.length < 4) continue
    seen.add(word)
    if (seen.size === 4) break
  }
  return [...seen]
}

/** Cut `text` to `limit` characters on a word boundary, with an ellipsis when it cut. */
function clamp(text: string, limit: number): string {
  const flat = text.replace(/\s+/g, ' ').trim()
  if (flat.length <= limit) return flat
  const cut = flat.slice(0, limit - 1)
  const space = cut.lastIndexOf(' ')
  return `${(space > limit / 2 ? cut.slice(0, space) : cut).trimEnd()}…`
}

/** What a finished run offers a draft. Every field is read off the run, never invented. */
export interface RunDraft {
  /** The question as the person wrote it. */
  question: string
  /** The answer the run produced. */
  answer: string
  /** Tool names the run actually called, in order, duplicates included. */
  tools: string[]
  /** Labels of the sources the answer stood on. */
  sources: string[]
}

/** `["a", "a", "b"]` → `["a ×2", "b"]`, preserving first-seen order. */
function tally(names: string[]): string[] {
  const counts = new Map<string, number>()
  for (const name of names) counts.set(name, (counts.get(name) ?? 0) + 1)
  return [...counts.entries()].map(([name, n]) => (n === 1 ? name : `${name} ×${n}`))
}

/**
 * A `SKILL.md` draft from one finished run.
 *
 * The shape is the run's own story in the order it happened: what was asked, what the
 * agent did, what it stood on, and what it said. Sections the run has no material for are
 * left out entirely — a run that called no tool gets no "how it was answered" list, since
 * a heading over an empty list reads as a step that was skipped rather than one that
 * never existed.
 *
 * The result is a **draft**, and the editor says so: the body is what happened, and
 * turning what happened into what should happen next time is the author's edit, not this
 * function's guess.
 */
export function draftFromRun(run: RunDraft): string {
  const question = run.question.trim()
  const description = clamp(
    `Follow this when a question looks like: ${question}`,
    MAX_DESCRIPTION_CHARS,
  )
  const triggers = skillTriggers(question)

  const parts: string[] = [
    '---',
    `name: ${skillName(question)}`,
    `description: ${description}`,
    ...(triggers.length > 0 ? [`triggers: [${triggers.join(', ')}]`] : []),
    '---',
    '',
    '# When this applies',
    '',
    `A question like: ${question}`,
    '',
  ]

  const steps = tally(run.tools)
  if (steps.length > 0) {
    parts.push('# What the saved run did', '')
    steps.forEach((step, index) => parts.push(`${index + 1}. Called \`${step}\`.`))
    parts.push('')
  }

  if (run.sources.length > 0) {
    parts.push('# What it stood on', '')
    for (const source of run.sources.slice(0, 6)) parts.push(`- ${source}`)
    parts.push('')
  }

  const answer = run.answer.trim()
  if (answer !== '') {
    parts.push('# The answer it gave', '', answer, '')
  }

  parts.push(
    '# Edit before you save',
    '',
    'The above is what happened once. A skill is what should happen every time — trim it',
    'to the steps worth repeating.',
    '',
  )

  return clampBody(parts.join('\n'))
}

/** The blank template, for a skill written from nothing rather than from a run. */
export const NEW_SKILL_TEMPLATE = `---
name: my_skill
description: One sentence saying when this applies. It sits in every system prompt.
triggers: [refund, invoice]
---

# What to do

- The steps, in the order you want them followed.
`

/**
 * Keep a draft inside `MAX_BODY_CHARS`.
 *
 * The server's bound is on the body alone, so measuring the whole document here is the
 * conservative reading — a draft that passes this cannot fail that. The cut is stated in
 * the document itself: an author who saves a silently truncated procedure has shipped a
 * procedure that stops mid-sentence.
 */
function clampBody(document: string): string {
  if (document.length <= MAX_BODY_CHARS) return document
  const notice = '\n\n_(This draft was cut to fit the 20,000-character limit on a skill body.)_\n'
  return `${document.slice(0, MAX_BODY_CHARS - notice.length).trimEnd()}${notice}`
}
