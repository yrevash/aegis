/**
 * The answer must render as the document the model wrote, not as its punctuation.
 *
 * The owner asked for an answer that reads like the ones they get from Claude or
 * ChatGPT. Half of that was length and was fixed in the backend prompt; this is the
 * other half. A model that writes `## What to check` and `1. **Confirm the refund
 * window**` had it rendered as hashes, digits and asterisks running down a
 * `whitespace-pre-wrap` paragraph.
 *
 * Two properties matter beyond "it parses". The first is that a **prefix** parses — the
 * answer types out a character at a time, so this runs on partial input constantly and
 * must never throw or drop text. The second is that a link never becomes a link: a
 * clickable target synthesised from model output is a phishing surface in a product
 * whose whole claim is that it does not assert anything without provenance.
 */

import assert from 'node:assert/strict'
import test from 'node:test'

import { parseAnswer, parseInline } from '../../src/components/console/answerMarkdown.ts'

/** The plain text a block carries, whatever emphasis it is split into. */
const flat = (spans) => spans.map((s) => s.text).join('')

test('emphasis becomes runs, and the text survives intact', () => {
  const spans = parseInline('Check the **refund window** in `policy.md` first.')
  assert.equal(flat(spans), 'Check the refund window in policy.md first.')
  assert.deepEqual(
    spans.map((s) => s.kind),
    ['text', 'bold', 'text', 'code', 'text'],
  )
})

test('a heading, a numbered list and a paragraph become three blocks', () => {
  const blocks = parseAnswer(
    ['## What to check', '', '1. Confirm the payment cleared', '2. Check the refund window', '', 'Then escalate.'].join('\n'),
  )
  assert.deepEqual(
    blocks.map((b) => b.kind),
    ['heading', 'list', 'paragraph'],
  )
  assert.equal(blocks[0].level, 2)
  assert.equal(blocks[1].ordered, true)
  assert.equal(blocks[1].start, 1)
  assert.equal(blocks[1].items.length, 2)
  assert.equal(flat(blocks[2].spans), 'Then escalate.')
})

test('bullets and numbers are different lists, not one', () => {
  const blocks = parseAnswer('- first\n- second\n1. one\n2. two')
  assert.deepEqual(
    blocks.map((b) => b.ordered),
    [false, true],
  )
})

test('a fenced block keeps every character, including the ones that look like markup', () => {
  const blocks = parseAnswer('Run this:\n\n```\nSELECT * FROM refunds -- **not bold**\n```')
  assert.equal(blocks[1].kind, 'code')
  assert.equal(blocks[1].text, 'SELECT * FROM refunds -- **not bold**')
})

test('a partial answer parses — every prefix of a real one', () => {
  const answer = '## What to check\n\n1. **Confirm** the payment cleared\n2. Check `policy.md`\n\nThen escalate.'
  for (let i = 1; i <= answer.length; i += 1) {
    const blocks = parseAnswer(answer.slice(0, i))
    assert.ok(Array.isArray(blocks), `prefix of length ${i} did not parse`)
  }
})

test('a link is never turned into a link', () => {
  const blocks = parseAnswer('See [the policy](https://example.invalid/steal) for details.')
  assert.equal(blocks.length, 1)
  assert.equal(
    flat(blocks[0].spans),
    'See [the policy](https://example.invalid/steal) for details.',
  )
  assert.ok(blocks[0].spans.every((s) => s.kind === 'text'))
})

test('plain prose is one paragraph, not a pile of lines', () => {
  const blocks = parseAnswer('I cannot answer that.\nThere is no document to cite.')
  assert.equal(blocks.length, 1)
  assert.equal(flat(blocks[0].spans), 'I cannot answer that. There is no document to cite.')
})

test('empty text yields no blocks rather than an empty paragraph', () => {
  assert.deepEqual(parseAnswer(''), [])
  assert.deepEqual(parseAnswer('\n\n  \n'), [])
})
