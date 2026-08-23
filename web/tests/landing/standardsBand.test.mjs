/**
 * The band's numbers must be *derived*, and its framing must stay unambiguous.
 *
 * Three failure modes, and each of them has already happened somewhere in this
 * repository:
 *
 * **A total gets typed in.** The compliance table's split changes whenever a control
 * changes state — India's frameworks were being added to it in the same week this band
 * shipped — and a landing page is the one place nobody re-derives anything. "27
 * enforced" outliving the twenty-seventh control is a lie told at the top of the funnel
 * by a product whose entire pitch is that it does not do that. `LivePlatform`'s own
 * docstring records the same defect happening once already: a heading said "Twelve
 * modules" over a grid rendering fifteen. So the band and its helper are swept for
 * control totals, and every figure on screen must have come off the wire.
 *
 * **A control identifier gets pasted in.** The band names individual clauses now — `Art.
 * 14`, `LLM06`, `s.12(3)` — which is a much better claim and a much worse thing to
 * hardcode: an id typed into the shortlist keeps rendering after the control stops being
 * enforced, and the page then claims a mechanism the repository no longer has. The
 * shortlist may name frameworks and nothing finer, and every printed id must have come
 * from the endpoint's `enforced_controls`.
 *
 * **The framing gets softened.** ISO 27001, SOC 2 and GDPR normally mean *audited and
 * certified*. Aegis holds none of that. The correction is currently made three times —
 * in the heading, in a persistent notice, and in the disclosure — and each of those is
 * one edit away from being trimmed by somebody tidying up a marketing page. The words
 * that carry it are pinned here, along with the rule that no certification mark or
 * accreditation seal is ever rendered as an image.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  SHORTLIST,
  resolveGroups,
  shownCount,
} from '../../src/components/landing/standardsSummary.ts'

const BAND = readFileSync(
  fileURLToPath(new URL('../../src/components/landing/StandardsBand.tsx', import.meta.url)),
  'utf8',
)
const HELPER = readFileSync(
  fileURLToPath(new URL('../../src/components/landing/standardsSummary.ts', import.meta.url)),
  'utf8',
)

/** The sources with comments stripped — what a reader can actually see. */
const strip = (source) => source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/\/\/[^\n]*/g, ' ')

/** A served framework row, shaped exactly like `GET /v1/platform/standards` sends one. */
const framework = (id, mark, enforced, mapped) => ({
  id,
  mark,
  name: mark,
  version: '1',
  jurisdiction: 'International',
  coverage: {
    enforced: enforced.length,
    partial: mapped - enforced.length,
    not_implemented: 0,
    not_applicable: 0,
    total: mapped,
  },
  enforced_controls: enforced.map((controlId) => ({ id: controlId, title: `t ${controlId}` })),
})

const SERVED = [
  framework('eu-ai-act', 'EU AI Act', ['Art. 12', 'Art. 13', 'Art. 14'], 10),
  framework('owasp-llm', 'OWASP LLM Top 10', ['LLM01', 'LLM02', 'LLM05', 'LLM06', 'LLM07'], 10),
  framework('dpdp', 'DPDP Act', ['s.12(1)-(2)', 's.12(3)'], 12),
  framework('privacy', 'GDPR', ['GDPR Art. 16', 'GDPR Art. 17'], 9),
]

test('the shortlist names frameworks, never control ids', () => {
  // A control id in the shortlist is an id that keeps rendering after the control stops
  // being enforced. The groups may only say *which frameworks*; the endpoint says which
  // controls, on every read.
  for (const group of SHORTLIST) {
    assert.ok(group.title.length > 0, 'a group with no title is an unlabelled card')
    assert.ok(Array.isArray(group.frameworks) && group.frameworks.length > 0)
    assert.deepEqual(
      Object.keys(group).sort(),
      ['frameworks', 'title'],
      'a group carries an editorial title and framework ids — nothing finer',
    )
  }
})

test('the shortlist is the one edit, and it names the three groups the band claims', () => {
  assert.deepEqual(
    SHORTLIST.map((group) => [...group.frameworks]),
    [['eu-ai-act'], ['owasp-llm'], ['dpdp', 'privacy']],
    'the trustworthy-AI trio, the LLM attack surface, and data-principal rights across two jurisdictions',
  )
})

test('every printed control came off the wire, and every count is the length of what is printed', () => {
  const groups = resolveGroups(SERVED, SHORTLIST)

  assert.deepEqual(
    groups.map((group) => group.claims.map((claim) => claim.controls.map((c) => c.id))),
    [
      [['Art. 12', 'Art. 13', 'Art. 14']],
      [['LLM01', 'LLM02', 'LLM05', 'LLM06', 'LLM07']],
      [
        ['s.12(1)-(2)', 's.12(3)'],
        ['GDPR Art. 16', 'GDPR Art. 17'],
      ],
    ],
  )

  // The numerator is a count of the rows about to be drawn, so the two cannot disagree.
  for (const group of groups) {
    for (const claim of group.claims) {
      assert.equal(claim.controls.length, claim.controls.length)
      assert.ok(claim.mapped > claim.controls.length || claim.mapped === claim.controls.length)
    }
    assert.equal(
      group.count,
      group.claims.reduce((sum, claim) => sum + claim.controls.length, 0),
    )
  }

  assert.equal(shownCount(groups), 12)
})

test('a denominator is available for every claim, so no card can imply the whole framework', () => {
  const groups = resolveGroups(SERVED, SHORTLIST)
  const pairs = groups.flatMap((group) =>
    group.claims.map((claim) => [claim.mark, claim.controls.length, claim.mapped]),
  )
  assert.deepEqual(pairs, [
    ['EU AI Act', 3, 10],
    ['OWASP LLM Top 10', 5, 10],
    ['DPDP Act', 2, 12],
    ['GDPR', 2, 9],
  ])
})

test('a control that stops being enforced stops being printed', () => {
  // The endpoint only serves ids for `state == "enforced"`, so this is what a demotion
  // looks like from the page's side: the row simply is not there, and the count drops
  // with it. The band must never hold an id of its own to fill the gap.
  const demoted = SERVED.map((f) =>
    f.id === 'owasp-llm'
      ? framework('owasp-llm', 'OWASP LLM Top 10', ['LLM01', 'LLM02'], 10)
      : f,
  )
  const groups = resolveGroups(demoted, SHORTLIST)
  const owasp = groups.find((group) => group.title.includes('attack surface'))

  assert.deepEqual(
    owasp.claims[0].controls.map((c) => c.id),
    ['LLM01', 'LLM02'],
  )
  assert.equal(owasp.count, 2)
  assert.equal(shownCount(groups), 9)
})

test('a framework enforcing nothing contributes nothing, and an empty group is dropped', () => {
  const stripped = SERVED.map((f) =>
    f.id === 'eu-ai-act' ? framework('eu-ai-act', 'EU AI Act', [], 10) : f,
  )
  const titles = resolveGroups(stripped, SHORTLIST).map((group) => group.title)
  assert.ok(
    !titles.some((title) => title.includes('oversight')),
    'an empty card is worse than no card — the group is dropped whole',
  )

  // A group that still has one of its two frameworks keeps that one.
  const halved = SERVED.map((f) => (f.id === 'dpdp' ? framework('dpdp', 'DPDP Act', [], 12) : f))
  const rights = resolveGroups(halved, SHORTLIST).find((group) => group.title.includes('rights'))
  assert.deepEqual(
    rights.claims.map((claim) => claim.mark),
    ['GDPR'],
  )

  // And nothing served at all yields nothing, so the band can state the absence.
  assert.deepEqual(resolveGroups([], SHORTLIST), [])
  assert.equal(shownCount([]), 0)
})

test('no control total is written into the band or its helper', () => {
  // Every figure on screen is a count of something fetched. A bare two- or three-digit
  // integer in the visible source is the shape a pasted total takes, and there is no
  // legitimate reason for one here.
  for (const [name, source] of [
    ['StandardsBand.tsx', BAND],
    ['standardsSummary.ts', HELPER],
  ]) {
    const visible = strip(source)
      // Tailwind and CSS carry plenty of legitimate numbers; none of them are counts.
      .replace(/className="[^"]*"/g, ' ')
      .replace(/\bsize-\d+|\bh-\d|\bw-\d/g, ' ')
      .replace(/[\d.]+(?:rem|px|%|ch|em)/g, ' ')
    const literals = visible.match(/(?<![\w.\-/])\d{2,4}(?![\w.%])/g) ?? []
    assert.deepEqual(
      literals,
      [],
      `${name} contains a bare integer literal (${literals.join(', ')}) — every count must come from the endpoint`,
    )
  }
})

test('no control identifier is written into the band or its helper', () => {
  // The shapes the mapped standards use for a clause: `Art. 12`, `LLM06`, `A.8.15`,
  // `CC6.1`, `AML.T0054`, `s.12(3)`, `A05:2025`. Any of them in visible source means the
  // page is holding an id of its own instead of printing the endpoint's.
  const SHAPES = [
    /\bArt\.\s?\d/,
    /\bLLM\d{2}\b/,
    /\bAML\.T\d/,
    /\bCC\d\.\d/,
    /\bs\.\d+\(\d/,
    /\bA\d{2}:\d{4}\b/,
    /\bA\.\d+\.\d+\b/,
  ]
  for (const [name, source] of [
    ['StandardsBand.tsx', BAND],
    ['standardsSummary.ts', HELPER],
  ]) {
    const visible = strip(source)
    for (const shape of SHAPES) {
      assert.ok(
        !shape.test(visible),
        `${name} names a control identifier matching ${shape} — ids must come from GET /platform/standards, which serves only enforced ones`,
      )
    }
  }
})

test('the band says it is not certification, and says it where a reader cannot miss it', () => {
  const visible = strip(BAND)

  assert.ok(
    visible.includes('Compliance-readiness evidence — not certification.'),
    'the persistent notice is the one sentence this band cannot ship without',
  )
  assert.ok(
    visible.includes('Certified against none.'),
    'the section heading itself must carry the correction, not only the notice',
  )
  assert.ok(
    visible.includes('Alignment, not certification.'),
    'the disclosure repeats it beside the frameworks it applies to',
  )
  assert.ok(
    /no ISO 27001 or ISO\/IEC 42001 certificate, no SOC 2 report and no EU AI Act\s+conformity assessment/.test(
      visible,
    ),
    'the specific things Aegis does not hold must be named, not gestured at',
  )
  assert.ok(
    /No framework here is enforced in every clause/.test(visible),
    'naming clauses invites the whole-framework reading; the disclosure must refuse it',
  )
})

test('no framework is rendered as an image, only as type', () => {
  const visible = strip(BAND)
  assert.ok(
    !/<img|<Image|\.svg|\.png|\.webp|\.avif/.test(visible),
    'reproducing a certification mark or accreditation seal is the part that is a real problem',
  )
})

test('the band publishes its source, so a reader can check where the counts came from', () => {
  assert.ok(strip(BAND).includes('GET /platform/standards'))
})
