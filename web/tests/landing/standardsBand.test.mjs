/**
 * The band's numbers must be *derived*, and its framing must stay unambiguous.
 *
 * Two failure modes, one each way, and each of them has already happened elsewhere in
 * this repository:
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

import { byJurisdiction, segments } from '../../src/components/landing/standardsSummary.ts'

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

test('the segments are proportions of the served total, not of anything remembered', () => {
  const bands = segments({
    enforced: 29,
    partial: 58,
    not_implemented: 22,
    not_applicable: 5,
    total: 114,
  })

  assert.deepEqual(
    bands.map((b) => b.state),
    ['enforced', 'partial', 'not_implemented', 'not_applicable'],
  )
  assert.deepEqual(
    bands.map((b) => b.count),
    [29, 58, 22, 5],
  )
  // The widths sum to the whole bar, whatever the totals happen to be.
  const total = bands.reduce((sum, b) => sum + Number.parseFloat(b.width), 0)
  assert.ok(Math.abs(total - 100) < 1e-9, `segments covered ${total}% of the strip`)

  // And a different payload gives different segments — the maths is on the argument.
  const other = segments({
    enforced: 1,
    partial: 1,
    not_implemented: 0,
    not_applicable: 0,
    total: 2,
  })
  assert.deepEqual(
    other.map((b) => b.width),
    ['50%', '50%'],
  )
})

test('a state that never occurred is not drawn', () => {
  const bands = segments({
    enforced: 3,
    partial: 0,
    not_implemented: 0,
    not_applicable: 1,
    total: 4,
  })
  assert.deepEqual(
    bands.map((b) => b.state),
    ['enforced', 'not_applicable'],
    'a zero-width slice teaches a reader to stop reading the legend (DESIGN.md §4)',
  )
})

test('an empty control table draws nothing rather than a full-width nothing', () => {
  assert.deepEqual(
    segments({
      enforced: 0,
      partial: 0,
      not_implemented: 0,
      not_applicable: 0,
      total: 0,
    }),
    [],
  )
})

test('frameworks group by jurisdiction in the order the authority served them', () => {
  const coverage = { enforced: 0, partial: 0, not_implemented: 0, not_applicable: 0, total: 0 }
  const groups = byJurisdiction([
    { id: 'a', mark: 'A', name: 'A', version: '1', jurisdiction: 'India', coverage },
    { id: 'b', mark: 'B', name: 'B', version: '1', jurisdiction: 'India', coverage },
    { id: 'c', mark: 'C', name: 'C', version: '1', jurisdiction: 'International', coverage },
  ])

  assert.deepEqual(
    groups.map((g) => [g.jurisdiction, g.frameworks.length]),
    [
      ['India', 2],
      ['International', 1],
    ],
  )
})

test('no control total is written into the band or its helper', () => {
  // The band renders framework names, counts and a strip — all off the wire. A bare
  // two- or three-digit integer in the visible source is the shape a pasted total
  // takes, and there is no legitimate reason for one here.
  for (const [name, source] of [
    ['StandardsBand.tsx', BAND],
    ['standardsSummary.ts', HELPER],
  ]) {
    const visible = strip(source)
      // Tailwind and CSS carry plenty of legitimate numbers; none of them are counts.
      .replace(/className="[^"]*"/g, ' ')
      .replace(/\bsize-\d+|\bh-\d|\bw-\d/g, ' ')
      .replace(/[\d.]+(?:rem|px|%|ch|em)/g, ' ')
      // `* 100` turns a ratio into a CSS percentage. It is a unit, not a count.
      .replace(/\*\s*100\b/g, ' ')
    const literals = visible.match(/(?<![\w.\-/])\d{2,4}(?![\w.%])/g) ?? []
    assert.deepEqual(
      literals,
      [],
      `${name} contains a bare integer literal (${literals.join(', ')}) — every count must come from the endpoint`,
    )
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
