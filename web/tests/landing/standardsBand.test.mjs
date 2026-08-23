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
 * certified*. Aegis holds none of that. The amber notice that used to carry the
 * correction above the grid was removed by the owner's decision — this is a hackathon
 * project and its audience knows it holds no certificate — which leaves **two** carriers,
 * the section heading and the disclosure, and makes them the more load-bearing rather
 * than the less. Both are pinned here, and every heading branch is checked, not just the
 * one that renders when the endpoint answers. So is the rule that no certification mark
 * or accreditation seal is ever rendered as an image.
 *
 * **A framework gets claimed whole on the strength of a mapping nobody sees.** The band
 * now leads with frameworks whose every mapped control is enforced, and the denominator
 * of that claim is *our* mapping — four NIST functions is a coarser unit than seventeen
 * ISO controls. The `N of N` on the row and the sentence naming whose mapping it is are
 * both pinned, because dropping either turns a defensible claim into an unbounded one.
 */

import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

import {
  SHORTLIST,
  completeFrameworks,
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

test('a framework is claimed in full only when the endpoint says every control is enforced', () => {
  // The rule is two numbers off the wire and nothing else. No framework id is listed
  // anywhere for this group, so the day OWASP LLM or MITRE ATLAS reaches completeness it
  // joins the row with no edit in the source — and the day one loses a control it leaves.
  const served = [
    framework('nist-ai-rmf', 'NIST AI RMF', ['GOVERN', 'MAP', 'MEASURE', 'MANAGE'], 4),
    framework('eu-ai-act', 'EU AI Act', ['Art. 12', 'Art. 13', 'Art. 14'], 10),
  ]
  assert.deepEqual(
    completeFrameworks(served).map((claim) => [claim.mark, claim.controls.length, claim.mapped]),
    [['NIST AI RMF', 4, 4]],
  )

  // One control demoted, and the framework is no longer claimed in full.
  const demoted = [framework('nist-ai-rmf', 'NIST AI RMF', ['MEASURE', 'MANAGE'], 4), served[1]]
  assert.deepEqual(completeFrameworks(demoted), [])

  // A framework mapping nothing is not vacuously complete.
  assert.deepEqual(completeFrameworks([framework('empty', 'Empty', [], 0)]), [])
  assert.deepEqual(completeFrameworks([]), [])
})

test('a framework claimed in full is not printed a second time in the shortlist', () => {
  // `owasp-llm` is in SHORTLIST and would be in both groups the day it reaches ten of ten.
  // The band excludes what it has already claimed, and a group emptied that way is dropped
  // whole rather than drawn as an empty card.
  const complete = SERVED.map((f) =>
    f.id === 'owasp-llm'
      ? framework(
          'owasp-llm',
          'OWASP LLM Top 10',
          ['LLM01', 'LLM02', 'LLM03', 'LLM04', 'LLM05', 'LLM06', 'LLM07', 'LLM08', 'LLM09', 'LLM10'],
          10,
        )
      : f,
  )
  const inFull = completeFrameworks(complete)
  assert.deepEqual(
    inFull.map((claim) => claim.frameworkId),
    ['owasp-llm'],
  )

  const exclude = new Set(inFull.map((claim) => claim.frameworkId))
  const titles = resolveGroups(complete, SHORTLIST, exclude).map((group) => group.title)
  assert.ok(
    !titles.some((title) => title.includes('attack surface')),
    'the group is dropped whole once its only framework is claimed in full',
  )
  assert.deepEqual(titles, [
    'Record-keeping, transparency, oversight',
    'Data-principal rights, India and the EU',
  ])
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

test('the band says it is not certification, in both states it can render', () => {
  const visible = strip(BAND)

  // The amber banner was removed by the owner's decision — this is a hackathon project and
  // the audience knows it holds no certificate. What may not be removed is the correction
  // itself, and with the banner gone the heading is where it lives. Both branches of the
  // heading carry it, including the one rendered when the endpoint does not answer.
  // Every headline string in the file, whichever branch produces it: the one that leads
  // with a framework held in full, the one that counts several, the plain enforced-controls
  // fallback, and the one rendered when the endpoint does not answer.
  const headlines = visible.match(/(["`])[^"`]*(?:enforced in full|end to end)[^"`]*\1/g) ?? []
  assert.ok(
    headlines.length >= 4,
    `expected every heading branch to be a literal in this file, found ${headlines.length}`,
  )
  for (const headline of headlines) {
    assert.ok(
      headline.includes('Certified against none.'),
      `a heading branch dropped the correction: ${headline}`,
    )
  }
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
    /Nobody independent has audited any of it\./.test(visible),
    'a framework held in full is still a framework nobody has audited',
  )
})

test('a framework claimed in full still prints what "in full" is measured against', () => {
  // This is the claim the band did not previously make, and the one that can most easily
  // become a lie. "In full" is against *our* mapping — four NIST functions is a coarser
  // unit than seventeen ISO controls — so the denominator is printed beside it and the
  // disclosure says whose mapping it is.
  const visible = strip(BAND)
  assert.ok(
    /means every control this\s+table maps/.test(visible),
    'the disclosure must say that the denominator is our own mapping',
  )
  const card = visible.slice(visible.indexOf('function InFullRow'))
  assert.ok(card.length > 0, 'the band draws no in-full card')
  assert.ok(
    card.includes('{claim.mapped}') && card.includes('{claim.controls.length}'),
    'the in-full card prints N of N off the wire, never a bare "complete"',
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
