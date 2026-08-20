/**
 * The draft a finished run offers, and the two ways it wastes the author's work.
 *
 * A skill is refused by the server — a 422, with the document still in the box — when
 * the name is not slug-shaped or the description is over 280 characters. The draft is
 * generated, so those are not typos an author makes; they are defects the generator
 * ships to every author at once. And a draft that listed a step the run never took would
 * be the console inventing a capability, which is the one thing this product may not do.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import {
  MAX_DESCRIPTION_CHARS,
  SKILL_NAME_PATTERN,
  draftFromRun,
  skillName,
} from '../../src/components/skills/skillDraft.ts'

/** The frontmatter of a draft, as the server's parser reads it. */
function frontmatter(document) {
  const [, header = ''] = document.split('---')
  const fields = {}
  for (const line of header.trim().split('\n')) {
    const at = line.indexOf(':')
    if (at > 0) fields[line.slice(0, at).trim()] = line.slice(at + 1).trim()
  }
  return fields
}

test('the generated name is always one the server would accept', () => {
  assert.match(skillName('What is our refund window for enterprise customers?'), SKILL_NAME_PATTERN)
  // Nothing but stopwords and punctuation: the fallback still has to be a legal name,
  // because the alternative is a generated 422 on a document nobody mistyped.
  assert.match(skillName('Why is it?'), SKILL_NAME_PATTERN)
  assert.match(skillName('???'), SKILL_NAME_PATTERN)
  assert.match(skillName(''), SKILL_NAME_PATTERN)
})

test('a long question does not produce a description the server refuses', () => {
  const document = draftFromRun({
    question: `Explain ${'the quarterly reconciliation process '.repeat(30)}in detail`,
    answer: 'A long answer.',
    tools: [],
    sources: [],
  })
  const { description } = frontmatter(document)
  assert.ok(description.length > 0)
  assert.ok(
    description.length <= MAX_DESCRIPTION_CHARS,
    `description was ${description.length} characters, over the ${MAX_DESCRIPTION_CHARS} the rail allows`,
  )
})

test('the draft carries what the run did, and invents nothing where it did nothing', () => {
  const withTools = draftFromRun({
    question: 'How many invoices are overdue?',
    answer: 'Fourteen.',
    tools: ['sql_query', 'sql_query', 'send_email'],
    sources: ['invoices_2026.pdf'],
  })
  assert.match(withTools, /sql_query ×2/, 'a repeated call is counted, not listed twice')
  assert.match(withTools, /send_email/)
  assert.match(withTools, /invoices_2026\.pdf/)
  assert.match(withTools, /Fourteen\./)

  const bare = draftFromRun({
    question: 'How many invoices are overdue?',
    answer: 'Fourteen.',
    tools: [],
    sources: [],
  })
  assert.doesNotMatch(bare, /What the saved run did/, 'no step list over a run that took none')
  assert.doesNotMatch(bare, /What it stood on/, 'no source list over a run that cited none')
})
